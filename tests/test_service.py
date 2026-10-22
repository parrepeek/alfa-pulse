"""Автотесты сервиса: python -m unittest discover -s tests -v

Работают на отдельной тестовой БД, готовых профилях (data/profiles) и тестовой ленте новостей.
LLM-режим проверяется на имитации шлюза моделей (tests/mock_llm.py), реальные токены не тратятся.
"""
import os
import tempfile
import threading
import time
import unittest

TMP = tempfile.mkdtemp()
os.environ["ALFA_DB"] = os.path.join(TMP, "test.sqlite3")
os.environ["LLM_MODE"] = "rules"

from fastapi.testclient import TestClient  # noqa: E402

from app import config, db, profiles  # noqa: E402
from app.agent import verify as V  # noqa: E402
from app.agent.agent import run  # noqa: E402
from app.api import app  # noqa: E402
from app.cli import init  # noqa: E402
from app.ranking import feed  # noqa: E402
from app.research import index as research  # noqa: E402

init()
client = TestClient(app)


class TestProfilesAndFeed(unittest.TestCase):
    def test_profiles_loaded(self):
        n = db.conn().execute("SELECT COUNT(*) FROM profiles").fetchone()[0]
        self.assertEqual(n, 341)  # группы с выручкой > 15 млрд руб.

    def test_feed_is_personal(self):
        cfo = [x["event"]["id"] for x in feed(profiles.get("group_133188_sb"), "финансовый директор")[:3]]
        dev = [x["event"]["id"] for x in feed(profiles.get("group_134409_sb"), "собственник бизнеса")[:3]]
        self.assertNotEqual(cfo, dev)

    def test_feed_explains_why(self):
        for x in feed(profiles.get("group_134402_sb"), "руководитель казначейства"):
            self.assertTrue(x["why"], x["event"]["title"])

    def test_non_business_news_filtered(self):
        titles = [x["event"]["title"] for x in feed(profiles.get("group_134402_sb"), threshold=0.0, limit=50)]
        self.assertFalse(any("хоккею" in t for t in titles))

    def test_duplicates_clustered(self):
        d = db.conn().execute("SELECT COUNT(*) FROM news WHERE cluster_id<>id").fetchone()[0]
        self.assertGreaterEqual(d, 1)

    def test_role_changes_order(self):
        pr = profiles.get("group_134460_sb")
        a = [x["score"] for x in feed(pr, "руководитель казначейства")]
        b = [x["score"] for x in feed(pr, "генеральный директор")]
        self.assertNotEqual(a, b)


class TestResearch(unittest.TestCase):
    def test_stale_doc_excluded(self):
        res = research.search("сталь металлургия прокат", factor="сырье", limit=10)
        self.assertFalse(any("2021" in r["doc_id"] for r in res))

    def test_newest_forecast_first(self):
        fc = [f for f in research.forecasts("ключевая ставка") if f["period"] == "конец 2026"]
        self.assertEqual(fc[0]["value"], "14,0%")


class TestAgent(unittest.TestCase):
    def test_card_passes_all_checks(self):
        ev = feed(profiles.get("group_133188_sb"), "финансовый директор")[0]["event"]["id"]
        card = run("u_cfo", "group_133188_sb", ev, "финансовый директор", ["group_133188_sb"], force=True)
        failed = [c for c in card["quality"]["checks"] if not c["ok"]]
        self.assertEqual(failed, [])
        self.assertTrue(card["bank_view"] or card["impacts"])
        tr = db.conn().execute("SELECT data FROM traces WHERE trace_id=?", (card["trace_id"],)).fetchone()
        self.assertIsNotNone(tr)

    def test_bank_forecast_update_noted(self):
        ev = db.conn().execute("SELECT id FROM events WHERE data LIKE '%Ключевая ставка ЦБ РФ%'").fetchone()["id"]
        card = run("u_cfo", "group_133188_sb", ev, "финансовый директор", ["group_133188_sb"], force=True)
        self.assertTrue(any("обновил прогноз" in n for n in card["bank_notes"]))

    def test_verify_catches_invented_number(self):
        card = {"what_happened": {"facts": [{"text": "x", "quote": "x", "source": "s"}]}, "why_it_matters": [{"text": "Выручка упадет на 37%", "basis": ["a", "b"]}],
                "impacts": [], "bank_view": [], "actions": [], "sources": [1]}
        checks = {c["name"]: c["ok"] for c in V.verify(card, {"grounding_texts": ["кредиты у 97%"], "bank_docs": [], "bank_texts": []})}
        self.assertFalse(checks["числа_подтверждены"])

    def test_verify_catches_bank_attribution(self):
        card = {"what_happened": {"facts": []}, "why_it_matters": [], "impacts": [{"text": "Альфа-Банк считает, что ставка вырастет"}], "bank_view": [], "actions": [], "sources": []}
        checks = {c["name"]: c["ok"] for c in V.verify(card, {"grounding_texts": [], "bank_docs": [], "bank_texts": []})}
        self.assertFalse(checks["разделение_банк_и_AI"])


class TestAPI(unittest.TestCase):
    def test_access_control(self):
        r = client.get("/api/feed", params={"user": "u_cfo", "group": "group_134402_sb"})
        self.assertEqual(r.status_code, 403)

    def test_unseen_news(self):
        r = client.post("/api/news", json={"user": "u_ceo", "title": "Продажи новых автомобилей в сентябре упали на 12%",
                                           "text": "Продажи новых легковых автомобилей в сентябре снизились на 12% год к году. Автодилеры снижают закупки у импортеров из Китая."})
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertTrue(d["event"]["factors"])
        self.assertGreater(d["relevance"][0]["score"], 0)

    def test_chat_intents(self):
        ev = feed(profiles.get("group_133188_sb"), "финансовый директор")[0]["event"]["id"]
        for q, intent in [("Покажи по компаниям группы", "companies"), ("Какие контрагенты затронуты?", "counterparties"), ("На чем основан вывод?", "sources")]:
            r = client.post("/api/chat", json={"user": "u_cfo", "group": "group_133188_sb", "event": ev, "question": q}).json()
            self.assertEqual(r["intent"], intent)
            self.assertTrue(r["refs"])

    def test_profile_correction_changes_exposure(self):
        before = profiles.get("group_134460_sb")["exposure"]["ставка"]["score"]
        client.post("/api/profile/correction", json={"user": "u_holding", "group": "group_134460_sb", "key": "плавающая ставка", "value": "нет"})
        after = profiles.get("group_134460_sb")["exposure"]["ставка"]["score"]
        self.assertLess(after, before)


class TestLLMMode(unittest.TestCase):
    """Сквозной LLM-путь (план -> черновик -> критик) на имитации шлюза моделей."""

    @classmethod
    def setUpClass(cls):
        import uvicorn

        from tests import mock_llm
        cls.mock = mock_llm
        cls.server = uvicorn.Server(uvicorn.Config(mock_llm.app, port=8799, log_level="error"))
        threading.Thread(target=cls.server.run, daemon=True).start()
        time.sleep(1.5)
        config.LLM_BASE_URL, config.LLM_API_KEY, config.LLM_MODE = "http://127.0.0.1:8799", "test", "llm"

    @classmethod
    def tearDownClass(cls):
        config.LLM_BASE_URL, config.LLM_API_KEY, config.LLM_MODE = "", "", "rules"
        cls.server.should_exit = True

    def test_llm_card(self):
        os.environ["NO_PROXY"] = "127.0.0.1,localhost"
        ev = db.conn().execute("SELECT id FROM events WHERE data LIKE '%Банк России сохранил%'").fetchone()["id"]
        card = run("u_cfo", "group_133188_sb", ev, "финансовый директор", ["group_133188_sb"], force=True)
        self.assertEqual(card["quality"]["mode"], "llm")
        self.assertIn(config.MODELS["reason"], self.mock.CALLS)
        self.assertIn(config.MODELS["critic"], self.mock.CALLS)
        self.assertTrue(all(w["label"] == "ai" for w in card["why_it_matters"]))
        self.assertGreater(db.tokens_today(), 0)


if __name__ == "__main__":
    unittest.main()
