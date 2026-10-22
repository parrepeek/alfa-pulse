"""Сбор -> дедупликация -> извлечение событий -> сохранение. Запуск: python -m app.cli ingest [--online]"""
import re

from .. import db
from . import extract as ex
from .sources import all_sources, now_iso


def _shingles(text: str, k: int = 4):
    w = re.findall(r"[а-яёa-z0-9]+", text.lower())
    return {" ".join(w[i:i + k]) for i in range(max(1, len(w) - k + 1))}


def _dup_of(item, existing):
    sh = _shingles(item["title"] + " " + item.get("text", "")[:600])
    for e in existing:
        j = len(sh & e["sh"]) / max(1, len(sh | e["sh"]))
        if j >= 0.5:
            return e["id"]
    return None


def ingest(online: bool = False) -> dict:
    c = db.conn()
    known = {r["id"] for r in c.execute("SELECT id FROM news")}
    existing = [{"id": r["cluster_id"], "sh": _shingles(r["title"] + " " + (r["text"] or "")[:600])} for r in c.execute("SELECT * FROM news")]
    stats = {"fetched": 0, "new": 0, "duplicates": 0, "events": 0, "errors": []}
    for src in all_sources(online):
        try:
            items = src.fetch()
        except Exception as e:  # недоступный источник не ломает сбор
            stats["errors"].append(f"{type(src).__name__}: {e}")
            continue
        stats["fetched"] += len(items)
        for it in items:
            if it["id"] in known:
                continue
            cluster = _dup_of(it, existing)
            stats["duplicates"] += int(cluster is not None)
            cluster = cluster or it["id"]
            c.execute("INSERT INTO news VALUES(?,?,?,?,?,?,?,?,?,?)", (it["id"], it["source"], it.get("url", ""), it["title"], it.get("text", ""),
                      it["published"], now_iso(), int(it.get("synthetic", 0)), cluster, it.get("kind", "новость")))
            known.add(it["id"])
            existing.append({"id": cluster, "sh": _shingles(it["title"] + " " + it.get("text", "")[:600])})
            stats["new"] += 1
            if cluster == it["id"]:  # событие создается по первоисточнику кластера
                ev, extractor = ex.extract(it)
                if it.get("kind") == "аналитика Альфа-Банка":  # факторы публикации банка заданы в реестре документов
                    meta = c.execute("SELECT data FROM research_docs WHERE doc_id=?", (it["url"].split("//")[1].split("/")[0],)).fetchone()
                    if meta:
                        ev["factors"] = dict(__import__("json").loads(meta["data"]).get("factors", {}))  # факторы и знак из реестра
                        ev["confidence"] = 0.9
                        extractor += "+реестр"
                ev.update({"id": "ev_" + it["id"], "news_id": it["id"], "title": it["title"], "date": it["published"], "source": it["source"],
                           "url": it.get("url", ""), "kind": it.get("kind", "новость"), "synthetic": bool(it.get("synthetic", 0))})
                for f in ev["facts"]:
                    f["source"] = it["source"] + (f", {it['url']}" if it.get("url") else "")
                c.execute("INSERT OR REPLACE INTO events VALUES(?,?,?,?,?)", (ev["id"], it["id"], it["published"], db.jdump(ev), extractor))
                stats["events"] += 1
    c.commit()
    return stats
