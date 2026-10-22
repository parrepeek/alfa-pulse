"""Агент интерпретации: план -> инструменты -> общий контекст -> черновик -> самопроверка -> исправление.

Собственная реализация цикла (без агентных фреймворков). Режимы:
- llm: план и текст пишет GLM-5.3-Flash, критик - Qwen3.6 (другая модель), числа и атрибуцию проверяет verify.py;
- rules: тот же цикл и те же инструменты, текст собирается из справочника механизмов (работает без ключа).
Результат - карточка по JSON-схеме: факт / позиция банка / вывод AI разделены, у всего есть источник; трасса сохраняется.
"""
import hashlib
import json
import re
import time

from .. import config, db
from ..llm import LLMUnavailable, json_call
from ..taxonomy import FACTORS
from .tools import Toolbox
from .verify import failed, verify

MAX_REPAIRS = 2
OCR_NOISE = re.compile(r"\d0/0|\d,\d{1,2}96\b|\d96 ")  # артефакты распознавания процентов: 14,096 / 14,00/0
QUESTIONS = {
    "ставка": "Какая доля кредитов и лизинга группы по плавающей ставке и когда ближайшие пересмотры?",
    "курс": "Какая доля закупок и выручки в валюте и есть ли хеджирование?",
    "санкции_ВЭД": "Через какие банки и страны идут платежи по ключевым контрактам, есть ли запасной маршрут?",
    "сырье": "Можно ли переложить рост цен в цены для покупателей и как быстро?",
    "потребспрос": "Какая доля продаж приходится на потребительские сегменты в 4 квартале?",
    "стройка_ипотека": "Какая доля продаж проектов зависит от ипотеки и какой запас по эскроу?",
    "налоги_регулирование": "Какие компании группы попадут под новые ставки и есть ли льготы?",
    "концентрация_контрагентов": "Есть ли лимит и обеспечение по крупнейшему покупателю?",
}


def _sentences(text):
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", " ".join((text or "").split())) if len(s.strip()) > 30]


def _best_sentence(text, query):
    stems = {w[:5] for w in re.findall(r"[а-яё]{5,}", query.lower())}
    sents = [s for s in _sentences(text) if len(s) < 400 and not OCR_NOISE.search(s)]
    best = max(sents, key=lambda s: sum(st in s.lower() for st in stems), default="")
    return best if best and sum(st in best.lower() for st in stems) >= 2 else ""  # нерелевантный фрагмент не показываем


# ---------- план ----------
def plan_rules(ev, pr, sc):
    factors = sorted(sc["factor_contrib"], key=lambda f: -sc["factor_contrib"][f])[:2] or list(ev.get("factors", {}))[:2]
    steps = []
    for f in factors:
        steps.append({"tool": "search_bank_research", "args": {"query": ev["title"] + " " + ev.get("summary", ""), "factor": f},
                      "why": f"позиция банка по фактору «{f}»"})
        steps.append({"tool": "get_bank_forecast", "args": {"factor": f}, "why": f"прогноз банка для фактора «{f}»"})
    if ev.get("sectors") or set(factors) & {"концентрация_контрагентов", "потребспрос", "сырье"}:
        steps.append({"tool": "get_counterparty_exposure", "args": {"sectors": ev.get("sectors")}, "why": "затронуты поставщики/покупатели клиента"})
    steps.append({"tool": "find_related_events", "args": {}, "why": "связанные события: вывод не по одной новости"})
    return factors, steps


def plan_llm(ev, pr, sc):
    sys_ = ("Ты планировщик анализа влияния события на бизнес клиента банка. Доступные инструменты: "
            "search_bank_research(query, factor), get_bank_forecast(factor), get_counterparty_exposure(sectors), find_related_events(). "
            f"Факторы: {list(FACTORS)}. Верни JSON: factors (1-2 главных фактора), steps [{{tool, args, why}}] не более 6 шагов.")
    user = json.dumps({"event": {k: ev.get(k) for k in ("title", "summary", "factors", "sectors", "countries", "commodities")},
                       "client_exposure": {k: v["score"] for k, v in pr["exposure"].items()}, "ranking": sc["factor_contrib"]}, ensure_ascii=False)
    obj, _ = json_call("reason", sys_, user, required=["steps"], max_tokens=600)
    allowed = {"search_bank_research", "get_bank_forecast", "get_counterparty_exposure", "find_related_events"}
    steps = [s for s in obj["steps"] if s.get("tool") in allowed][:6]
    factors = [f for f in obj.get("factors", []) if f in FACTORS][:2] or plan_rules(ev, pr, sc)[0]
    return factors, steps


def _list(x):  # инструмент мог вернуть ошибку {"error": ...} - в контекст она не попадает, но остается в трассе
    return x if isinstance(x, list) else []


def execute(tb: Toolbox, steps, ev, pr):
    out = {"research": [], "forecasts": [], "counterparties": None, "related": []}
    for s in steps:
        a = s.get("args", {}) or {}
        if s["tool"] == "search_bank_research":
            out["research"] += _list(tb.search_bank_research(a.get("query", ev["title"]), a.get("factor")))
        elif s["tool"] == "get_bank_forecast":
            out["forecasts"] += [x | {"factor": a.get("factor")} for x in _list(tb.get_bank_forecast(a.get("factor")))]
        elif s["tool"] == "get_counterparty_exposure":
            cp = tb.get_counterparty_exposure(pr["group_id"], a.get("sectors"))
            out["counterparties"] = cp if isinstance(cp, dict) and "error" not in cp else None
        elif s["tool"] == "find_related_events":
            out["related"] = _list(tb.find_related_events(ev, pr))
    seen, uniq = set(), []
    for r in out["research"]:
        k = (r["doc_id"], r["page"], r["text"][:50])
        if k not in seen:
            seen.add(k)
            uniq.append(r)
    out["research"] = uniq
    return out


# ---------- позиция банка ----------
def bank_view(found, ev):
    items, notes = [], []
    by_key, seen = {}, set()
    for fc in found["forecasts"]:
        k = (fc["doc_id"], fc["indicator"], fc["period"], fc["value"])
        if k not in seen:  # один прогноз мог прийти по двум факторам
            seen.add(k)
            by_key.setdefault((fc["indicator"], fc["period"]), []).append(fc)
    for (ind, period), lst in by_key.items():
        lst.sort(key=lambda x: x["date"], reverse=True)
        new = lst[0]
        quote = new["note"].split("| цитата: ")[-1] if "| цитата: " in new["note"] else ""
        if not quote:
            continue
        items.append({"text": f"Прогноз банка: {ind} {new['value']} ({period}).", "doc_id": new["doc_id"], "page": new["page"],
                      "date": new["date"], "quote": quote, "kind": "прогноз"})
        if len(lst) > 1 and lst[1]["value"] != new["value"]:
            notes.append(f"Банк обновил прогноз «{ind}, {period}»: было {lst[1]['value']} ({lst[1]['date']}), стало {new['value']} ({new['date']}).")
    for r in found["research"][:3]:
        s = _best_sentence(r["text"], ev["title"] + " " + ev.get("summary", ""))
        if s and not any(s[:40] in x["quote"] for x in items):
            items.append({"text": s, "doc_id": r["doc_id"], "page": r["page"], "date": r["date"], "quote": s, "kind": "аналитика", "title": r["title"]})
    return items[:4], notes


# ---------- черновик без LLM ----------
def _sells(pr, commodity):
    names = " ".join([pr.get("lead_okved", "")] + [c["okved"] for c in pr.get("top_companies", [])]).lower()
    return {"топливо": "топлив", "металлы": "металл", "зерно и корма": "зерн", "продукты питания": "пищев"}.get(commodity, "@@") in names


def draft_rules(ev, pr, sc, factors, found):
    why, impacts, actions = [], [], []
    used = set()
    for f in factors:
        b = [x for x in pr["exposure"].get(f, {}).get("because", []) if x and x not in used]
        used |= set(b)
        if f == "сырье" and ev.get("commodities"):
            b = [f"{c}: {pr['commodities'][c]:.0%} выручки и закупок" for c in ev["commodities"] if c in pr["commodities"]] or b
        if b:
            why.append({"text": f"{FACTORS[f]['title']}: " + "; ".join(b[:3]) + ".", "basis": b[:3], "label": "ai"})
    cw = pr.get("trade_countries_weight", {})
    hit = [f"{c.title()} ({cw[c]:.0%} выручки группы)" for c in ev.get("countries", []) if cw.get(c, 0) >= 0.05]
    if hit:
        why.append({"text": "Страны события совпадают с вашей ВЭД: " + ", ".join(hit) + ".", "basis": hit, "label": "ai"})
    cp = found.get("counterparties")
    if cp and ev.get("sectors"):
        parts = [f"{k}: {v:.0%} закупок" for k, v in cp["suppliers_by_sector"].items() if k in ev["sectors"]] + \
                [f"{k}: {v:.0%} поступлений" for k, v in cp["customers_by_sector"].items() if k in ev["sectors"]]
        if parts:
            why.append({"text": "Событие затрагивает ваших контрагентов: " + "; ".join(parts) + ".", "basis": parts, "label": "ai"})

    horizon = {"ставка": "до конца 2026 г.", "курс": "1-3 месяца", "потребспрос": "4 квартал 2026 г."}
    for f in factors:
        v = ev["factors"].get(f, -0.5)
        spec = FACTORS[f]
        if f == "сырье" and any(_sells(pr, c) for c in ev.get("commodities", [])):
            impacts.append({"type": "возможность", "text": "Группа сама продает этот товар: рост цен повышает выручку и стоимость запасов.",
                            "mechanism": "цена товара -> выручка", "horizon": "1-3 месяца", "label": "ai", "basis": [pr.get("lead_okved", "")]})
            impacts.append({"type": "риск", "text": "Если закупки идут по рыночной цене, маржа зависит от скорости переложения цены на покупателей.",
                            "mechanism": "цена закупки -> маржа", "horizon": "1-3 месяца", "label": "ai", "basis": ["закупки сырья"]})
        else:
            kind, text = ("риск", spec["risk"]) if v < 0 else ("возможность", spec["opportunity"] or spec["risk"])
            impacts.append({"type": kind, "text": text + ".", "mechanism": spec["title"], "horizon": horizon.get(f, "1-3 месяца"),
                            "label": "ai", "basis": pr["exposure"].get(f, {}).get("because", [])[:2]})
    tcs = pr.get("top_customer_share") or 0
    negative = sum(ev["factors"].get(f, 0) for f in factors) < 0
    if negative and tcs >= 0.3 and set(factors) & {"ставка", "потребспрос", "концентрация_контрагентов", "санкции_ВЭД", "курс"}:
        impacts.append({"type": "риск", "text": f"Крупнейший покупатель дает {tcs:.0%} поступлений: если событие ухудшит его положение, задержка оплаты ударит по ликвидности группы сильнее обычного.",
                        "mechanism": "концентрация покупателей", "horizon": "1-3 месяца", "label": "ai", "basis": [f"крупнейший покупатель {tcs:.0%}"]})
    for r in found.get("related", [])[:1]:
        same = all((r.get("signs", {}).get(f, 0) < 0) == (ev["factors"].get(f, 0) < 0) for f in r["common_factors"])
        direction = "усиливает эффект" if same else "действует в противоположную сторону"
        impacts.append({"type": "контекст", "text": f"Связанное событие «{r['title']}» ({r['date']}) {direction} по факторам: {', '.join(r['common_factors'])}.",
                        "mechanism": "совокупное влияние", "horizon": "", "label": "ai", "basis": [r["id"]]})
    for i, f in enumerate(factors):
        for text, product in FACTORS[f]["actions"][: 2 if i == 0 else 1]:
            actions.append({"text": text + ".", "product": product, "label": "ai"})
    return {"why_it_matters": why, "impacts": impacts, "actions": actions,
            "questions": [QUESTIONS[f] for f in factors if f in QUESTIONS]}


# ---------- черновик LLM ----------
DRAFT_SYS = (
    "Ты аналитик Альфа-Банка для руководителя крупной компании. Напиши персональную интерпретацию события по JSON-контексту. "
    "Правила: используй только факты из контекста; позицию банка бери только из bank_view и не приписывай банку свои выводы; "
    "в why_it_matters ссылайся минимум на 2 признака клиента из profile (basis - дословные признаки); не пересказывай новость, а объясняй механизм "
    "и учитывай несколько факторов и связанные события; числа - только из контекста. "
    "Верни JSON: why_it_matters [{text, basis}], impacts [{type: риск|возможность|контекст, text, mechanism, horizon, basis}], "
    "actions [{text, product (или null)}], questions [строки]."
)


def draft_llm(ev, pr, sc, factors, found, bank, issues=None):
    ctx = {"event": {k: ev.get(k) for k in ("title", "summary", "date", "factors", "sectors", "countries", "commodities", "facts")},
           "profile": {"main_sector": pr["main_sector"], "revenue_bn_rub": pr["revenue_bn_rub"], "sector_mix": pr["sector_mix"],
                       "products_share_of_revenue": pr["products"], "trade_countries": pr.get("trade_countries_weight"),
                       "commodities": pr["commodities"], "top_customer_share": pr["top_customer_share"],
                       "exposure": {f: pr["exposure"][f] for f in factors if f in pr["exposure"]}, "client_corrections": pr.get("client_corrections")},
           "bank_view": bank, "counterparties": found.get("counterparties"), "related_events": found.get("related"),
           "main_factors": factors}
    user = json.dumps(ctx, ensure_ascii=False)
    if issues:
        user += "\nИсправь замечания проверки: " + json.dumps(issues, ensure_ascii=False)
    obj, _ = json_call("reason", DRAFT_SYS, user, required=["why_it_matters", "impacts", "actions"], max_tokens=1500)
    for b in ("why_it_matters", "impacts", "actions"):
        for it in obj.get(b, []):
            it["label"] = "ai"
    return obj


def critic_llm(card):
    sys_ = ("Ты строгий редактор. Проверь карточку: нет ли пересказа вместо интерпретации, противоречий между позицией банка и выводами, "
            "выводов, приписанных банку без источника, общих советов без связи с клиентом. Верни JSON {issues: [строки]} (пустой список, если все хорошо).")
    obj, _ = json_call("critic", sys_, json.dumps({k: card[k] for k in ("what_happened", "why_it_matters", "impacts", "bank_view", "actions")},
                                                  ensure_ascii=False), required=["issues"], max_tokens=500, temperature=0)
    return [i for i in obj.get("issues", []) if isinstance(i, str)][:5]


# ---------- основной цикл ----------
def _grounding(ev, pr, found, bank):
    texts = [f["text"] for f in ev.get("facts", [])] + [b["quote"] for b in bank] + [b["text"] for b in bank]
    texts += [x for e in pr["exposure"].values() for x in e.get("because", [])]
    texts += [json.dumps(pr["products"]), json.dumps(pr.get("trade_countries_weight", {})), json.dumps(pr["commodities"]),
              json.dumps(pr["sector_mix"]), str(pr.get("top_customer_share")), str(pr.get("top_supplier_share")), str(pr["revenue_bn_rub"])]
    for k in ("suppliers_by_sector", "customers_by_sector"):
        texts += [f"{v:.0%}" for v in pr[k].values()]
    texts += [f"{v:.0%}" for v in pr["commodities"].values()] + [f"{v:.0%}" for v in pr.get("trade_countries_weight", {}).values()]
    texts += [f"{v:.0%}" for v in pr["products"].values()] + [f"{(pr.get('top_customer_share') or 0):.0%}"]
    texts += [r["title"] + " " + r["date"] for r in found.get("related", [])]
    return texts


def run(user_id: str, group_id: str, event_id: str, role: str, allowed_groups: list[str], force: bool = False) -> dict:
    from .. import ranking
    key = f"{group_id}|{event_id}|{role}|{'llm' if config.llm_enabled() else 'rules'}"
    c = db.conn()
    if not force:
        r = c.execute("SELECT data FROM insights WHERE key=?", (key,)).fetchone()
        if r:
            return json.loads(r["data"])
    trace_id = "tr_" + hashlib.sha1((key + str(time.time())).encode()).hexdigest()[:10]
    tb = Toolbox(trace_id, user_id, allowed_groups)
    trace = {"trace_id": trace_id, "user": user_id, "group_id": group_id, "event_id": event_id, "role": role, "steps": []}
    t0 = time.time()

    ev = tb.get_event(event_id)
    pr = tb.get_client_profile(group_id)
    if not pr or "error" in pr or "error" in ev:
        raise PermissionError((pr or {}).get("error") or ev.get("error"))
    sc = ranking.score(ev, pr, role)
    mode = "llm" if config.llm_enabled() else "rules"
    try:
        factors, steps = plan_llm(ev, pr, sc) if mode == "llm" else plan_rules(ev, pr, sc)
    except (LLMUnavailable, ValueError):
        mode = "rules"
        factors, steps = plan_rules(ev, pr, sc)
    trace["steps"].append({"step": "план", "mode": mode, "factors": factors, "plan": steps})

    found = execute(tb, steps, ev, pr)
    bank, notes = bank_view(found, ev)
    if not bank:  # адаптация: позиции банка не нашлось - расширяем поиск по фактору
        trace["steps"].append({"step": "адаптация", "reason": "не найдена позиция банка", "action": "повторный поиск по ключевым словам фактора"})
        for f in factors:
            found["research"] += _list(tb.search_bank_research(FACTORS[f]["research_q"], f))
        bank, notes = bank_view(found, ev)
    trace["steps"].append({"step": "сбор", "research": [(r["doc_id"], r["page"]) for r in found["research"]],
                           "forecasts": [(f["indicator"], f["value"], f["date"]) for f in found["forecasts"]], "related": [r["id"] for r in found["related"]]})

    ctx = {"bank_docs": [{"doc_id": r["doc_id"], "stale": r.get("stale", False)} for r in found["research"]] +
                        [{"doc_id": f["doc_id"], "stale": False} for f in found["forecasts"]],
           "bank_texts": [r["text"] for r in found["research"]] + [f["note"] for f in found["forecasts"]],
           "grounding_texts": _grounding(ev, pr, found, bank)}

    issues, it = None, 0
    while True:
        it += 1
        try:
            body = draft_llm(ev, pr, sc, factors, found, bank, issues) if mode == "llm" else draft_rules(ev, pr, sc, factors, found)
        except (LLMUnavailable, ValueError) as e:
            trace["steps"].append({"step": "сбой LLM", "error": str(e), "action": "переход в режим rules"})
            mode = "rules"
            body = draft_rules(ev, pr, sc, factors, found)
        card = assemble(ev, pr, sc, factors, body, bank, notes, found, role, trace_id, mode)
        checks = verify(card, ctx)
        crit = []
        if mode == "llm":
            try:
                crit = critic_llm(card)
            except (LLMUnavailable, ValueError):
                crit = []
        bad = failed(checks)
        trace["steps"].append({"step": f"проверка #{it}", "checks": checks, "critic": crit})
        if (not bad and not crit) or it > MAX_REPAIRS:
            break
        issues = [f"{c['name']}: {c['detail']}" for c in bad] + crit
        if mode == "rules":  # детерминированное исправление: убираем неподтвержденное
            card = repair_rules(card, bad)
            checks = verify(card, ctx)
            trace["steps"].append({"step": "исправление", "removed": [c["name"] for c in bad], "checks": checks})
            break
    card["quality"] = {"checks": checks, "iterations": it, "mode": mode, "critic": crit, "ms": round((time.time() - t0) * 1000)}
    trace["tool_calls"] = tb.calls
    trace["result_quality"] = card["quality"]
    c.execute("INSERT OR REPLACE INTO traces VALUES(?,?,?)", (trace_id, time.time(), db.jdump(trace)))
    c.execute("INSERT OR REPLACE INTO insights VALUES(?,?,?)", (key, time.time(), db.jdump(card)))
    c.commit()
    return card


def repair_rules(card, bad):
    names = {c["name"] for c in bad}
    if "позиция_банка_из_документов" in names:
        card["bank_view"] = [b for b in card["bank_view"] if b.get("kind") == "прогноз"]
    if "числа_подтверждены" in names:
        for b in ("why_it_matters", "impacts"):
            card[b] = [x for x in card[b] if not re.search(r"\d", x["text"]) or x.get("basis")]
    return card


def assemble(ev, pr, sc, factors, body, bank, notes, found, role, trace_id, mode):
    facts = [{"text": f["text"], "quote": f.get("quote", f["text"]), "source": f.get("source", ev["source"])} for f in ev.get("facts", [])]
    status = "тестовое событие (синтетика)" if ev.get("synthetic") else ("подтверждено несколькими источниками" if ev.get("also_reported_by") else "один источник")
    sources = [{"type": "событие", "title": ev["title"], "ref": ev.get("url") or ev["source"], "date": ev["date"]}]
    sources += [{"type": "аналитика Альфа-Банка", "title": b.get("title") or b["doc_id"], "ref": f"{b['doc_id']}, с. {b['page']}", "date": b["date"]} for b in bank]
    sources += [{"type": "данные клиента", "title": "Профиль группы: продукты банка, ФТС, граф платежей", "ref": pr["group_id"], "date": ""}]
    sources += [{"type": "связанное событие", "title": r["title"], "ref": r["id"], "date": r["date"]} for r in found.get("related", [])[:2]]
    uniq, refs = [], set()
    for s in sources:  # один документ-страница в списке оснований один раз
        if s["ref"] not in refs:
            refs.add(s["ref"])
            uniq.append(s)
    sources = uniq
    return {
        "trace_id": trace_id,
        "client": {"group_id": pr["group_id"], "role": role, "main_sector": pr["main_sector"], "revenue_bn_rub": pr["revenue_bn_rub"]},
        "event": {k: ev.get(k) for k in ("id", "title", "date", "source", "url", "kind", "synthetic")},
        "relevance": {"score": sc["score"], "main_factor": sc["main_factor"], "factors": factors, "why": sc["why"]},
        "what_happened": {"summary": ev.get("summary", ""), "facts": facts, "status": status, "also_reported_by": ev.get("also_reported_by", [])},
        "why_it_matters": body.get("why_it_matters", []),
        "impacts": body.get("impacts", []),
        "bank_view": [b | {"label": "bank"} for b in bank],
        "bank_notes": notes,
        "bank_absent": not bank,
        "actions": body.get("actions", []),
        "questions": body.get("questions", []),
        "sources": sources,
        "related": found.get("related", []),
    }
