"""Команды обслуживания.

python -m app.cli build-profiles   # профили групп из CSV кейса (data/raw) -> data/profiles/group_profiles.json
python -m app.cli init             # загрузка профилей, индекс аналитики банка, сбор новостей из тестовой ленты
python -m app.cli ingest [--online]# сбор новостей (+ RSS при --online)
python -m app.cli demo             # пройти сквозной сценарий в консоли и показать карточку
python -m app.cli eval             # метрики качества на тестовой ленте
"""
import json
import sys

from . import config, profiles
from .parser.pipeline import ingest
from .research import index as research


def init():
    n = profiles.load_to_db()
    r = research.build()
    s = ingest(online=False)
    print(f"профилей: {n}; аналитика: {r}; новости: {s}")


def demo(user_id="u_treasury"):
    from .agent.agent import run
    from .ranking import feed
    users = {u["user_id"]: u for u in json.loads(config.USERS.read_text(encoding="utf-8"))}
    u = users[user_id]
    g = u["groups"][0]
    pr = profiles.get(g)
    items = feed(pr, u["role"])
    print(f"\nЛента для {u['role']} ({g}, {pr['main_sector']}, {pr['revenue_bn_rub']} млрд руб.):")
    for i, x in enumerate(items, 1):
        print(f" {i}. [{x['score']:.2f}] {x['event']['title']}  <- {'; '.join(x['why'][:2])}")
    card = run(user_id, g, items[0]["event"]["id"], u["role"], u["groups"], force=True)
    print("\nКарточка:", json.dumps(card, ensure_ascii=False, indent=1)[:6000])


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "init"
    if cmd == "build-profiles":
        profiles.build()
    elif cmd == "init":
        init()
    elif cmd == "ingest":
        print(ingest(online="--online" in argv))
    elif cmd == "demo":
        demo(argv[2] if len(argv) > 2 else "u_treasury")
    elif cmd == "eval":
        from .evaluation import evaluate
        print(json.dumps(evaluate(), ensure_ascii=False, indent=1))
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv)
