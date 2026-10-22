"""Персональное ранжирование событий (объяснимая формула, без LLM).

балл = Σ_f чувствительность_f × |влияние_f| × уверенность × вес_роли_f
     + совпадение стран ВЭД + отрасли/контрагенты (ограничено)
     × (0.5 + 0.5 × свежесть),  свежесть = exp(-дни/14)
Затем порог (не лента всех новостей) и разнообразие (не больше 2 событий одного фактора в топе).
"""
import json
import math
from datetime import date

from . import config, db
from .taxonomy import ROLE_WEIGHTS


def score(ev: dict, pr: dict, role: str | None = None) -> dict:
    expo = pr["exposure"]
    rw = ROLE_WEIGHTS.get(role or "", {})
    m = sum(pr["commodities"].get(x, 0) for x in ev.get("commodities", []))
    contrib = {}
    secs = ev.get("sectors", [])
    present = sum(pr["sector_mix"].get(x, 0) + pr["suppliers_by_sector"].get(x, 0) + pr["customers_by_sector"].get(x, 0) for x in secs)
    gate = 0.4 if secs and present < 0.05 else 1.0  # событие про чужую отрасль: учитываем, но слабее
    for f, v in ev.get("factors", {}).items():
        e = min(1, 2.5 * m) if f == "сырье" and ev.get("commodities") else expo.get(f, {"score": 0})["score"]
        contrib[f] = e * abs(v) * ev.get("confidence", 0.6) * rw.get(f, 1.0) * (1.0 if f == "сырье" and m else gate)
    extra = {}
    cw = pr.get("trade_countries_weight", {})
    c = sum(cw.get(x, 0) for x in ev.get("countries", []))
    if c:
        extra["страны ВЭД"] = min(0.3, 0.5 * c)  # бонус за совпадение стран не должен перекрывать сам фактор
    s = sum(pr["sector_mix"].get(x, 0) for x in ev.get("sectors", [])) + 0.5 * sum(
        pr["suppliers_by_sector"].get(x, 0) + pr["customers_by_sector"].get(x, 0) for x in ev.get("sectors", []))
    if s:
        extra["отрасль/контрагенты"] = min(0.3, 0.5 * s)
    age = max(0, (config.TODAY - date.fromisoformat(ev["date"])).days)
    fresh = math.exp(-age / 14)
    total = (sum(contrib.values()) + sum(extra.values())) * (0.5 + 0.5 * fresh)
    main = max(contrib, key=contrib.get) if contrib else None
    return {"score": round(total, 3), "main_factor": main, "factor_contrib": {k: round(v, 3) for k, v in contrib.items()},
            "extra": {k: round(v, 3) for k, v in extra.items()}, "freshness": round(fresh, 2), "why": why(ev, pr, main)}


def why(ev, pr, main) -> list[str]:
    out = []
    for f in sorted(ev.get("factors", {}), key=lambda k: k != main):
        b = pr["exposure"].get(f, {}).get("because", [])
        if f == "сырье" and ev.get("commodities"):
            b = [f"{c}: {pr['commodities'][c]:.0%} (своя выручка и закупки)" for c in ev["commodities"] if c in pr["commodities"]]
        b = [x for x in b if x]
        if b:
            out.append("; ".join(b[:3]))
    cw = pr.get("trade_countries_weight", {})
    hit = [f"{c.title()} ({cw[c]:.0%} выручки группы)" for c in ev.get("countries", []) if cw.get(c, 0) >= 0.05]
    if hit:
        out.append("страны ВЭД: " + ", ".join(hit))
    sec = [f"{s}: {pr['sector_mix'][s]:.0%} выручки" for s in ev.get("sectors", []) if pr["sector_mix"].get(s, 0) >= 0.05] + \
          [f"поставщики «{s}»: {pr['suppliers_by_sector'][s]:.0%} закупок" for s in ev.get("sectors", []) if pr["suppliers_by_sector"].get(s, 0) >= 0.05]
    if sec:
        out.append("отрасль события: " + "; ".join(sec))
    uniq = []
    for x in out:  # без повторов одних и тех же признаков
        if x not in uniq:
            uniq.append(x)
    return uniq[:3]


def feed(pr: dict, role: str | None = None, limit: int | None = None, threshold: float | None = None) -> list[dict]:
    limit = limit or config.FEED_SIZE
    threshold = config.FEED_THRESHOLD if threshold is None else threshold
    rows = db.conn().execute("SELECT data FROM events WHERE published <= ? AND published >= date(?, '-60 day')",
                             (config.TODAY.isoformat(), config.TODAY.isoformat())).fetchall()
    scored = []
    for r in rows:
        ev = json.loads(r["data"])
        if not ev.get("factors"):  # событие без влияния на бизнес-факторы в ленту не попадает
            continue
        sc = score(ev, pr, role)
        if sc["score"] >= threshold:
            scored.append({"event": ev, **sc})
    scored.sort(key=lambda x: -x["score"])
    out, per_factor = [], {}
    for x in scored:  # разнообразие ленты
        f = x["main_factor"]
        if per_factor.get(f, 0) >= 2:
            continue
        per_factor[f] = per_factor.get(f, 0) + 1
        out.append(x)
        if len(out) >= limit:
            break
    return out
