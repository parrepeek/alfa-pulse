"""Быстрая проверка сквозного сценария в консоли: лента и карточка для нескольких тестовых пользователей."""
import json
import sys

from app import config, db, profiles
from app.agent.agent import run
from app.ranking import feed

users = {u["user_id"]: u for u in json.loads(config.USERS.read_text(encoding="utf-8"))}
for r in db.conn().execute("SELECT e.data FROM events e JOIN news n ON n.id=e.news_id WHERE n.title LIKE '%ставк%' OR n.title LIKE 'Бонд%'"):
    e = json.loads(r["data"])
    print("СОБЫТИЕ", e["title"][:55], e["factors"])
for uid in (sys.argv[1:] or ["u_cfo", "u_owner"]):
    u = users[uid]
    g = u["groups"][0]
    pr = profiles.get(g)
    items = feed(pr, u["role"])
    print(f"\n== {u['role']} {g} {pr['main_sector']} {pr['revenue_bn_rub']} млрд")
    for x in items:
        print(f"  [{x['score']:.2f}] {x['event']['title'][:70]} <- {x['why'][:2]}")
    card = run(uid, g, items[0]["event"]["id"], u["role"], u["groups"], force=True)
    print("  WHY:", [w["text"] for w in card["why_it_matters"]])
    print("  IMPACTS:", [i["text"][:100] for i in card["impacts"]])
    print("  BANK:", [(b["doc_id"], b["text"][:100]) for b in card["bank_view"]], card["bank_notes"])
    print("  ACTIONS:", [(a["text"][:70], a["product"]) for a in card["actions"]])
    print("  CHECKS:", [(c["name"], c["ok"], c["detail"][:60]) for c in card["quality"]["checks"]], "итераций", card["quality"]["iterations"])
