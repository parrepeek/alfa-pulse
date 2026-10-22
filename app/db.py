"""SQLite: профили, новости, события, аналитика банка (FTS5/BM25), кэш карточек, трассы, журнал."""
import json
import sqlite3
import threading
import time

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS profiles(group_id TEXT PRIMARY KEY, main_sector TEXT, revenue_bn REAL, data TEXT);
CREATE TABLE IF NOT EXISTS news(id TEXT PRIMARY KEY, source TEXT, url TEXT, title TEXT, text TEXT, published TEXT,
  fetched TEXT, synthetic INTEGER, cluster_id TEXT, kind TEXT);
CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY, news_id TEXT, published TEXT, data TEXT, extractor TEXT);
CREATE TABLE IF NOT EXISTS research_docs(doc_id TEXT PRIMARY KEY, title TEXT, date TEXT, kind TEXT, stale INTEGER, data TEXT);
CREATE VIRTUAL TABLE IF NOT EXISTS research_fts USING fts5(text, doc_id UNINDEXED, page UNINDEXED, chunk UNINDEXED,
  tokenize='unicode61 remove_diacritics 2');
CREATE TABLE IF NOT EXISTS forecasts(indicator TEXT, value TEXT, period TEXT, doc_id TEXT, page INTEGER, date TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS insights(key TEXT PRIMARY KEY, created REAL, data TEXT);
CREATE TABLE IF NOT EXISTS traces(trace_id TEXT PRIMARY KEY, created REAL, data TEXT);
CREATE TABLE IF NOT EXISTS feedback(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, user_id TEXT, group_id TEXT,
  event_id TEXT, kind TEXT, comment TEXT);
CREATE TABLE IF NOT EXISTS tool_log(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, trace_id TEXT, user_id TEXT,
  tool TEXT, args TEXT, ms REAL, ok INTEGER, tokens INTEGER, model TEXT);
CREATE TABLE IF NOT EXISTS profile_overrides(group_id TEXT, key TEXT, value TEXT, ts REAL, PRIMARY KEY(group_id, key));
"""

_local = threading.local()


def conn() -> sqlite3.Connection:
    c = getattr(_local, "c", None)
    if c is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(DB_PATH, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.executescript(SCHEMA)
        _local.c = c
    return c


def jdump(x) -> str:
    return json.dumps(x, ensure_ascii=False)


def log_tool(trace_id, user_id, tool, args, ms, ok=True, tokens=0, model=""):
    conn().execute("INSERT INTO tool_log(ts,trace_id,user_id,tool,args,ms,ok,tokens,model) VALUES(?,?,?,?,?,?,?,?,?)",
                   (time.time(), trace_id, user_id, tool, jdump(args)[:2000], ms, int(ok), tokens, model))
    conn().commit()


def tokens_today() -> int:
    start = time.time() - 86400
    r = conn().execute("SELECT COALESCE(SUM(tokens),0) FROM tool_log WHERE ts>?", (start,)).fetchone()
    return int(r[0])
