"""Самопроверка карточки: детерминированные проверки, которые не зависят от модели.

1 факты с источником и цитатой; 2 позиция банка только из документов банка и не устаревших;
3 числа в выводах встречаются в источниках/профиле; 4 персонализация: минимум 2 признака клиента;
5 полнота шести блоков; 6 вывод модели не выдан за позицию банка; 7 используется свежий прогноз банка.
"""
import re

NUM = re.compile(r"\d+(?:[.,]\d+)?")
BANK_PHRASES = ("альфа-банк считает", "по мнению банка", "банк ожидает", "по оценке альфа", "мы ожидаем")


def _nums(text: str) -> set[str]:
    return {n.replace(".", ",").rstrip("0").rstrip(",") for n in NUM.findall(text or "")}


def _norm(t: str) -> str:
    return " ".join(re.sub(r"[^\wа-яё%,.]", " ", (t or "").lower()).split())


def verify(card: dict, ctx: dict) -> list[dict]:
    checks = []

    def add(name, ok, detail=""):
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    facts = card.get("what_happened", {}).get("facts", [])
    add("факты_с_источником", facts and all(f.get("source") and f.get("quote") for f in facts), f"{len(facts)} фактов")

    bad_bank = []
    allowed = {d["doc_id"]: d for d in ctx.get("bank_docs", [])}
    bank_texts = " ".join(_norm(x) for x in ctx.get("bank_texts", []))
    for b in card.get("bank_view", []):
        d = allowed.get(b.get("doc_id"))
        if not d or d.get("stale"):
            bad_bank.append(f"{b.get('doc_id')}: нет в базе или устарел")
        elif _norm(b.get("quote", ""))[:50] not in bank_texts:
            bad_bank.append(f"{b.get('doc_id')}: цитата не найдена в документе")
    add("позиция_банка_из_документов", not bad_bank, "; ".join(bad_bank) or f"{len(card.get('bank_view', []))} утверждений банка")

    grounded = set()
    for src in ctx.get("grounding_texts", []):
        grounded |= _nums(src)
    unsupported = []
    for block in ("why_it_matters", "impacts", "actions"):
        for it in card.get(block, []):
            for n in _nums(it.get("text", "")) - grounded:
                if n not in {"1", "2", "3", "4", "5", "6", "12"}:  # сроки и перечисления
                    unsupported.append(f"{n} в «{it.get('text', '')[:40]}…»")
    add("числа_подтверждены", not unsupported, "; ".join(unsupported[:4]) or "все числа найдены в источниках или профиле")

    feats = {f for it in card.get("why_it_matters", []) for f in it.get("basis", [])}
    add("персонализация", len(feats) >= 2, f"признаков клиента: {len(feats)}")

    blocks = {"что произошло": facts, "почему важно": card.get("why_it_matters"), "последствия": card.get("impacts"),
              "позиция банка": card.get("bank_view"), "действия": card.get("actions"), "основания": card.get("sources")}
    empty = [k for k, v in blocks.items() if not v]
    if "позиция банка" in empty and card.get("bank_absent"):  # честное «у банка нет позиции по теме» - не ошибка
        empty.remove("позиция банка")
        add("полнота", not empty, "позиции банка по теме нет в материалах (показано пользователю)" if not empty else "пусто: " + ", ".join(empty))
    else:
        add("полнота", not empty, "пусто: " + ", ".join(empty) if empty else "все 6 блоков")

    leak = [it.get("text", "")[:40] for b in ("why_it_matters", "impacts", "actions") for it in card.get(b, [])
            if any(p in it.get("text", "").lower() for p in BANK_PHRASES)]
    add("разделение_банк_и_AI", not leak, "; ".join(leak))

    outdated = [b for b in card.get("bank_view", []) if b.get("superseded")]
    add("свежий_прогноз_банка", not outdated, "использован замененный прогноз" if outdated else "")
    return checks


def failed(checks):
    return [c for c in checks if not c["ok"]]
