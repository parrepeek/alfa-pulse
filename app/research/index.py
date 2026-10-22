"""База «Позиция банка»: распознанный текст аналитики -> фрагменты в FTS5 (BM25), реестр документов, прогнозы.

Каждый фрагмент хранит документ и страницу, поэтому любой вывод можно связать с первоисточником.
Документы старше STALE_RESEARCH_DAYS помечаются устаревшими и не выдаются как текущая позиция банка.
"""
import json
import re
from datetime import date

from .. import config, db
from ..taxonomy import FACTORS

_WORD = re.compile(r"[а-яёa-z0-9]+", re.I)


def _chunks(text: str, size: int = 700):
    paras = [p.strip() for p in re.split(r"\n(?=[А-ЯA-Z0-9•❑])", text) if p.strip()]
    buf = ""
    for p in paras:
        if len(buf) + len(p) > size and buf:
            yield buf
            buf = ""
        buf += (" " if buf else "") + " ".join(p.split())
    if buf:
        yield buf


def build():
    c = db.conn()
    docs = json.loads(config.RESEARCH_DOCS.read_text(encoding="utf-8"))
    c.execute("DELETE FROM research_docs")
    c.execute("DELETE FROM research_fts")
    c.execute("DELETE FROM forecasts")
    n = 0
    for d in docs:
        stale = (config.TODAY - date.fromisoformat(d["date"])).days > config.STALE_RESEARCH_DAYS
        d["factors"] = d["factors"] if isinstance(d["factors"], dict) else {f: -0.5 for f in d["factors"]}
        c.execute("INSERT INTO research_docs VALUES(?,?,?,?,?,?)", (d["doc_id"], d["title"], d["date"], d["kind"], int(stale), db.jdump(d)))
        for f in sorted(config.RESEARCH_TEXT.glob(f"{d['doc_id']}__p*.txt")):
            page = int(f.stem.rsplit("__p", 1)[1])
            text = f.read_text(encoding="utf-8-sig")
            for i, ch in enumerate(_chunks(text)):
                if len(ch) > 120:  # подписи графиков и номера страниц не индексируем
                    c.execute("INSERT INTO research_fts(text, doc_id, page, chunk) VALUES(?,?,?,?)", (ch, d["doc_id"], page, i))
                    n += 1
    for fc in json.loads(config.FORECASTS.read_text(encoding="utf-8")):
        c.execute("INSERT INTO forecasts VALUES(?,?,?,?,?,?,?)", (fc["indicator"], fc["value"], fc["period"], fc["doc_id"], fc["page"], fc["date"],
                                                               fc["note"] + (" | цитата: " + fc["quote"] if fc.get("quote") else "")))
    c.commit()
    return {"docs": len(docs), "chunks": n}


def _fts_query(q: str) -> str:
    words = [w.lower() for w in _WORD.findall(q) if len(w) > 3]
    return " OR ".join(f'"{w[:6]}"*' for w in words[:12]) or '""'  # префиксный поиск устойчив к окончаниям и ошибкам OCR


def search(query: str, factor: str | None = None, limit: int = 5, include_stale: bool = False):
    """BM25 по фрагментам аналитики + приоритет документов, помеченных фактором, и свежих."""
    q = query + " " + (FACTORS[factor]["research_q"] if factor in FACTORS else "")
    rows = db.conn().execute(
        "SELECT f.text, f.doc_id, f.page, bm25(research_fts) AS s, d.title, d.date, d.stale, d.data FROM research_fts f "
        "JOIN research_docs d ON d.doc_id=f.doc_id WHERE research_fts MATCH ? ORDER BY s LIMIT 40", (_fts_query(q),)).fetchall()
    out = []
    tagged = {r["doc_id"] for r in rows if factor in json.loads(r["data"]).get("factors", {})}
    for r in rows:
        meta = json.loads(r["data"])
        if r["stale"] and not include_stale:
            continue
        if factor and r["doc_id"] not in tagged:  # строго по документам, где банк пишет об этом факторе; нет таких - нет и цитат
            continue
        age = (config.TODAY - date.fromisoformat(r["date"])).days
        bonus = (2.0 if factor in meta.get("factors", []) else 0) + max(0, 2 - age / 30)
        out.append({"doc_id": r["doc_id"], "title": r["title"], "date": r["date"], "page": r["page"], "stale": bool(r["stale"]),
                    "text": r["text"], "score": round(-r["s"] + bonus, 2)})
    out.sort(key=lambda x: -x["score"])
    seen, res = {}, []
    for x in out:  # не больше двух фрагментов с одной страницы
        k = (x["doc_id"], x["page"])
        if seen.get(k, 0) < 2:
            res.append(x)
            seen[k] = seen.get(k, 0) + 1
        if len(res) >= limit:
            break
    return res


def forecasts(indicator: str | None = None):
    sql = "SELECT * FROM forecasts" + (" WHERE indicator LIKE ?" if indicator else "") + " ORDER BY date DESC"
    rows = db.conn().execute(sql, (f"%{indicator}%",) if indicator else ()).fetchall()
    return [dict(r) for r in rows]


def docs():
    return [dict(r) | {"data": None} for r in db.conn().execute("SELECT doc_id,title,date,kind,stale FROM research_docs ORDER BY date DESC")]
