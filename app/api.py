"""HTTP API и веб-интерфейс «Альфа Пульс». Запуск: uvicorn app.api:app --port 8000

Вход - выбор тестового пользователя (имитация SSO интернет-банка). Пользователь видит только свои группы компаний.
"""
import json
import threading
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, db, profiles
from .agent import chat as chat_mod
from .agent.agent import run as run_agent
from .parser import extract as ex
from .parser.pipeline import ingest
from .parser.sources import nid, now_iso
from .ranking import feed as rank_feed
from .research import index as research
from .taxonomy import FACTORS, ROLES

app = FastAPI(title="Альфа Пульс API", version="1.0.0",
              description="Персональная новостная аналитика для руководителей крупного бизнеса в Альфа-Бизнесе")
WEB = Path(__file__).parent / "web"
app.mount("/static", StaticFiles(directory=WEB), name="static")


def users():
    return {u["user_id"]: u for u in json.loads(config.USERS.read_text(encoding="utf-8"))}


def auth(user: str, group: str | None = None):
    u = users().get(user)
    if not u:
        raise HTTPException(401, "неизвестный пользователь")
    if group and group not in u["groups"]:
        raise HTTPException(403, "нет доступа к группе")
    return u


def group_label(p):
    return f"{p['main_sector']}, {p['revenue_bn_rub']:g} млрд руб., {p['companies']} компаний"


@app.on_event("startup")
def startup():
    c = db.conn()
    if not c.execute("SELECT COUNT(*) FROM profiles").fetchone()[0]:
        profiles.load_to_db()
    if not c.execute("SELECT COUNT(*) FROM research_docs").fetchone()[0]:
        research.build()
    if not c.execute("SELECT COUNT(*) FROM events").fetchone()[0]:
        ingest(online=False)
    if config.FETCH_NEWS:  # фоновый сбор RSS
        def loop():
            while True:
                try:
                    ingest(online=True)
                except Exception:  # сбой источника не останавливает сервис
                    pass
                time.sleep(config.FETCH_INTERVAL_MIN * 60)
        threading.Thread(target=loop, daemon=True).start()


@app.get("/", response_class=HTMLResponse)
def index():
    return (WEB / "index.html").read_text(encoding="utf-8")


@app.get("/api/health")
def health():
    c = db.conn()
    return {"ok": True, "mode": "llm" if config.llm_enabled() else "rules", "today": config.TODAY.isoformat(),
            "profiles": c.execute("SELECT COUNT(*) FROM profiles").fetchone()[0], "events": c.execute("SELECT COUNT(*) FROM events").fetchone()[0]}


@app.get("/api/users")
def list_users():
    out = []
    for u in users().values():
        g = profiles.get(u["groups"][0])
        out.append({"user_id": u["user_id"], "name": u["name"], "role": u["role"], "groups": len(u["groups"]), "group_label": group_label(g) if g else ""})
    return out


@app.get("/api/me")
def me(user: str):
    u = auth(user)
    gs = []
    for g in u["groups"]:
        p = profiles.get(g)
        gs.append({"group_id": g, "label": group_label(p), "main_sector": p["main_sector"]})
    return {"user": u, "groups": gs, "roles": ROLES, "mode": "llm" if config.llm_enabled() else "rules", "today": config.TODAY.isoformat()}


def _impact_type(ev, main):
    v = ev["factors"].get(main, 0) if main else 0
    return "возможность" if v > 0 else "риск" if v < 0 else "нейтрально"


@app.get("/api/feed")
def feed(user: str, group: str, role: str | None = None):
    u = auth(user, group)
    pr = profiles.get(group)
    role = role or u["role"]
    items = rank_feed(pr, role)
    return {"group": group, "role": role, "items": [{
        "event_id": x["event"]["id"], "title": x["event"]["title"], "date": x["event"]["date"], "source": x["event"]["source"],
        "kind": x["event"]["kind"], "synthetic": x["event"].get("synthetic", False), "score": x["score"], "why": x["why"],
        "factor": x["main_factor"], "factor_title": FACTORS.get(x["main_factor"], {}).get("title", ""), "impact": _impact_type(x["event"], x["main_factor"]),
        "summary": x["event"].get("summary", "")} for x in items]}


@app.get("/api/insight")
def insight(user: str, group: str, event: str, role: str | None = None, refresh: bool = False):
    u = auth(user, group)
    try:
        return run_agent(user, group, event, role or u["role"], u["groups"], force=refresh)
    except PermissionError as e:
        raise HTTPException(403, str(e))


class ChatIn(BaseModel):
    user: str
    group: str
    event: str
    role: str | None = None
    question: str


@app.post("/api/chat")
def chat(q: ChatIn):
    u = auth(q.user, q.group)
    card = run_agent(q.user, q.group, q.event, q.role or u["role"], u["groups"])
    db.log_tool(card["trace_id"], q.user, "chat", {"question": q.question[:200]}, 0)
    return chat_mod.answer(q.question, card)


class FeedbackIn(BaseModel):
    user: str
    group: str
    event: str
    kind: str  # полезно | не про нас | ошибка в факте
    comment: str = ""


@app.post("/api/feedback")
def feedback(f: FeedbackIn):
    auth(f.user, f.group)
    db.conn().execute("INSERT INTO feedback(ts,user_id,group_id,event_id,kind,comment) VALUES(?,?,?,?,?,?)",
                      (time.time(), f.user, f.group, f.event, f.kind, f.comment[:500]))
    db.conn().commit()
    return {"ok": True}


@app.get("/api/trace/{trace_id}")
def trace(trace_id: str, user: str):
    r = db.conn().execute("SELECT data FROM traces WHERE trace_id=?", (trace_id,)).fetchone()
    if not r:
        raise HTTPException(404, "трасса не найдена")
    t = json.loads(r["data"])
    auth(user, t["group_id"])
    return t


@app.get("/api/profile")
def profile(user: str, group: str):
    auth(user, group)
    p = profiles.get(group)
    return p | {"factor_titles": {k: v["title"] for k, v in FACTORS.items()}}


class CorrectionIn(BaseModel):
    user: str
    group: str
    key: str  # например: «плавающая ставка» = нет; «хеджирование валюты» = есть
    value: str


@app.post("/api/profile/correction")
def correction(cin: CorrectionIn):
    auth(cin.user, cin.group)
    c = db.conn()
    c.execute("INSERT OR REPLACE INTO profile_overrides VALUES(?,?,?,?)", (cin.group, cin.key, cin.value[:200], time.time()))
    c.execute("DELETE FROM insights WHERE key LIKE ?", (cin.group + "|%",))  # карточки группы пересчитаются
    c.commit()
    return {"ok": True}


class NewsIn(BaseModel):
    user: str
    title: str
    text: str
    source: str = "Добавлено пользователем"
    date: str | None = None


@app.post("/api/news")
def add_news(n: NewsIn):
    """Проверка на ранее не показанных новостях: вставить любой текст и увидеть, кому и почему он важен."""
    u = auth(n.user)
    it = {"id": nid("user", n.title, str(time.time())), "source": n.source, "url": "", "title": n.title.strip(), "text": n.text.strip(),
          "published": n.date or config.TODAY.isoformat(), "synthetic": 0, "kind": "новость"}
    c = db.conn()
    c.execute("INSERT INTO news VALUES(?,?,?,?,?,?,?,?,?,?)", (it["id"], it["source"], "", it["title"], it["text"], it["published"], now_iso(), 0, it["id"], "новость"))
    ev, extractor = ex.extract(it)
    ev.update({"id": "ev_" + it["id"], "news_id": it["id"], "title": it["title"], "date": it["published"], "source": it["source"], "url": "", "kind": "новость", "synthetic": False})
    for f in ev["facts"]:
        f["source"] = it["source"]
    c.execute("INSERT INTO events VALUES(?,?,?,?,?)", (ev["id"], it["id"], it["published"], db.jdump(ev), extractor))
    c.commit()
    from .ranking import score
    res = []
    for g in u["groups"]:
        sc = score(ev, profiles.get(g), u["role"])
        res.append({"group_id": g, "score": sc["score"], "why": sc["why"], "main_factor": sc["main_factor"]})
    return {"event": ev, "extractor": extractor, "relevance": res}


@app.post("/api/admin/ingest")
def admin_ingest(online: bool = False):
    return ingest(online=online)


@app.get("/api/research/page/{doc_id}/{page}")
def research_page(doc_id: str, page: int):
    if ".." in doc_id or "/" in doc_id:
        raise HTTPException(400, "некорректный документ")
    f = config.DATA / "research" / "pages" / f"{doc_id}__p{page:02d}.png"
    if f.exists():
        return FileResponse(f)
    t = config.RESEARCH_TEXT / f"{doc_id}__p{page:02d}.txt"  # без отрисованных страниц показываем распознанный текст
    if t.exists():
        return PlainTextResponse(t.read_text(encoding="utf-8-sig"))
    raise HTTPException(404, "страница не найдена")


@app.get("/api/research/docs")
def research_docs():
    return research.docs()


@app.get("/api/quality-stats")  # путь без слова metrics: его блокируют некоторые антивирусы
def metrics():
    c = db.conn()
    fb = {r["kind"]: r["n"] for r in c.execute("SELECT kind, COUNT(*) n FROM feedback GROUP BY kind")}
    tools = [dict(r) for r in c.execute("SELECT tool, COUNT(*) calls, ROUND(AVG(ms),1) avg_ms, SUM(1-ok) errors, SUM(tokens) tokens FROM tool_log GROUP BY tool ORDER BY calls DESC")]
    q = [json.loads(r["data"])["quality"] for r in c.execute("SELECT data FROM insights")]
    checks = {}
    for x in q:
        for ch in x["checks"]:
            checks.setdefault(ch["name"], []).append(ch["ok"])
    ev = {r["extractor"]: r["n"] for r in c.execute("SELECT extractor, COUNT(*) n FROM events GROUP BY extractor")}
    return {"cards": len(q), "checks_pass_rate": {k: round(sum(v) / len(v), 3) for k, v in checks.items()},
            "avg_iterations": round(sum(x["iterations"] for x in q) / len(q), 2) if q else None,
            "avg_card_ms": round(sum(x["ms"] for x in q) / len(q)) if q else None, "feedback": fb, "tools": tools,
            "tokens_24h": db.tokens_today(), "token_limit": config.LLM_DAILY_TOKEN_LIMIT, "events_by_extractor": ev,
            "news": c.execute("SELECT COUNT(*) FROM news").fetchone()[0], "duplicates": c.execute("SELECT COUNT(*) FROM news WHERE cluster_id<>id").fetchone()[0]}
