"""Источники информационных материалов. Новый источник = новый адаптер с методом fetch() -> list[dict]."""
import hashlib
import json
import xml.etree.ElementTree as ET
from datetime import date, datetime
from email.utils import parsedate_to_datetime

import httpx

from .. import config, db
from ..taxonomy import ECON_WORDS

RSS = [  # открытые RSS деловых и официальных источников
    ("ЦБ РФ", "https://www.cbr.ru/rss/eventrss"),
    ("Интерфакс", "https://www.interfax.ru/rss.asp"),
    ("ТАСС", "https://tass.ru/rss/v2.xml"),
    ("РБК", "https://rssexport.rbc.ru/rbcnews/news/30/full.rss"),
    ("Коммерсантъ", "https://www.kommersant.ru/RSS/news.xml"),
]


def nid(*parts) -> str:
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:12]


def is_business(text: str) -> bool:
    t = text.lower()
    return sum(w in t for w in ECON_WORDS) >= 2


class RSSSource:
    def __init__(self, name, url):
        self.name, self.url = name, url

    def fetch(self, timeout=20) -> list[dict]:
        r = httpx.get(self.url, timeout=timeout, follow_redirects=True, headers={"User-Agent": "AlfaPulse/1.0"})
        r.raise_for_status()
        out = []
        for it in ET.fromstring(r.content).iter("item"):
            title = (it.findtext("title") or "").strip()
            desc = (it.findtext("description") or it.findtext("{http://purl.org/rss/1.0/modules/content/}encoded") or "").strip()
            link = (it.findtext("link") or "").strip()
            try:
                pub = parsedate_to_datetime(it.findtext("pubDate")).date().isoformat()
            except Exception:  # дата не распознана - берем текущую
                pub = date.today().isoformat()
            text = _strip_html(desc)
            if title and is_business(title + " " + text):
                out.append({"id": nid(link or title), "source": self.name, "url": link, "title": title, "text": text,
                            "published": pub, "synthetic": 0, "kind": "новость"})
        return out


class FileSource:
    """Тестовые и синтетические новости (JSONL) - для демо и регрессионных тестов."""

    def __init__(self, path=config.SYNTHETIC_NEWS):
        self.path = path

    def fetch(self) -> list[dict]:
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                n = json.loads(line)
                n.setdefault("id", nid(n["title"]))
                n.setdefault("kind", "новость")
                out.append(n)
        return out


class BankResearchSource:
    """Публикации Альфа-Банка тоже попадают в ленту как события (кроме устаревших)."""

    def fetch(self) -> list[dict]:
        out = []
        for d in db.conn().execute("SELECT * FROM research_docs WHERE stale=0"):
            pages = sorted(config.RESEARCH_TEXT.glob(f"{d['doc_id']}__p01.txt"))
            text = " ".join(pages[0].read_text(encoding="utf-8-sig").split())[:2500] if pages else ""
            out.append({"id": nid("bank", d["doc_id"]), "source": "Альфа-Банк, Центр макроэкономического анализа", "url": f"research://{d['doc_id']}/1",
                        "title": d["title"], "text": text, "published": d["date"], "synthetic": 0, "kind": "аналитика Альфа-Банка"})
        return out


def _strip_html(s: str) -> str:
    import re
    return " ".join(re.sub(r"<[^>]+>", " ", s).split())


def all_sources(online: bool):
    srcs = [FileSource(), BankResearchSource()]
    if online:
        srcs += [RSSSource(n, u) for n, u in RSS]
    return srcs


def now_iso():
    return datetime.now().isoformat(timespec="seconds")
