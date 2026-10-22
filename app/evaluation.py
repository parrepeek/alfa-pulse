"""Оценка качества на эталонной разметке (data/eval/gold.json): персонализация, достоверность, полнота.

Эталон: для пар «тестовый пользователь × событие» эксперт отмечает, должно ли событие быть в топ-3 ленты.
Метрики: Precision@3 и Recall@3 ленты, доля карточек, прошедших каждую проверку, среднее число признаков
клиента в объяснении, среднее время карточки. Запуск: python -m app.cli eval
"""
import json
import statistics
import time

from . import config, profiles
from .agent.agent import run
from .ranking import feed


def evaluate():
    gold = json.loads((config.DATA / "eval" / "gold.json").read_text(encoding="utf-8"))
    users = {u["user_id"]: u for u in json.loads(config.USERS.read_text(encoding="utf-8"))}
    p_at, r_at, checks, feats, ms = [], [], {}, [], []
    per_user = {}
    for uid, must in gold.items():
        u = users[uid]
        g = u["groups"][0]
        items = feed(profiles.get(g), u["role"])
        top = [x["event"]["title"] for x in items[:3]]
        hit = [t for t in top if any(m.lower() in t.lower() for m in must)]
        p_at.append(len(hit) / max(1, len(top)))
        r_at.append(sum(any(m.lower() in t.lower() for t in top) for m in must) / len(must))
        per_user[uid] = {"top3": top, "expected": must}
        for x in items[:3]:
            t0 = time.time()
            card = run(uid, g, x["event"]["id"], u["role"], u["groups"], force=True)
            ms.append((time.time() - t0) * 1000)
            feats.append(len({f for w in card["why_it_matters"] for f in w.get("basis", [])}))
            for c in card["quality"]["checks"]:
                checks.setdefault(c["name"], []).append(c["ok"])
    return {"precision@3": round(statistics.mean(p_at), 2), "recall@3": round(statistics.mean(r_at), 2),
            "checks_pass_rate": {k: round(sum(v) / len(v), 2) for k, v in checks.items()},
            "avg_client_features_in_explanation": round(statistics.mean(feats), 1), "avg_card_ms": round(statistics.mean(ms)),
            "cards": len(ms), "per_user": per_user}
