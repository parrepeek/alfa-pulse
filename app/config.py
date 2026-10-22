"""Настройки сервиса из переменных окружения (см. .env.example)."""
import os
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DB_PATH = Path(os.getenv("ALFA_DB", DATA / "alfa_pulse.sqlite3"))
RAW_DIR = Path(os.getenv("ALFA_RAW_DIR", DATA / "raw"))  # CSV кейса
RESEARCH_TEXT = DATA / "research" / "text"
RESEARCH_DOCS = DATA / "research" / "docs.json"
FORECASTS = DATA / "research" / "forecasts.json"
SYNTHETIC_NEWS = DATA / "news" / "synthetic_news.jsonl"
USERS = DATA / "users.json"
PROFILES_JSON = DATA / "profiles" / "group_profiles.json"

# "Сегодня" для демо: аналитика кейса датирована сентябрем 2026
TODAY = date.fromisoformat(os.getenv("ALFA_TODAY", "2026-09-15"))

# LLM: OpenAI-совместимый endpoint с ключом организаторов. Без ключа сервис работает в режиме rules.
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODE = os.getenv("LLM_MODE", "auto")  # auto | llm | rules
LLM_DAILY_TOKEN_LIMIT = int(os.getenv("LLM_DAILY_TOKEN_LIMIT", "400000"))
MODELS = {  # только разрешенные кейсом модели; имена задаются так, как их называет шлюз организаторов
    "extract": os.getenv("MODEL_EXTRACT", "gemma-4-26b-a4b"),
    "reason": os.getenv("MODEL_REASON", "glm-5.3-flash"),
    "critic": os.getenv("MODEL_CRITIC", "qwen3.6-35b-a3b"),
    "links": os.getenv("MODEL_LINKS", "deepseek-v4.1-flash"),
    "vision": os.getenv("MODEL_VISION", "qwen3-vl-30b-a3b-instruct"),
    "chat": os.getenv("MODEL_CHAT", "kimi-k2.6"),
}

FETCH_NEWS = os.getenv("FETCH_NEWS", "0") == "1"  # фоновый сбор RSS
FETCH_INTERVAL_MIN = int(os.getenv("FETCH_INTERVAL_MIN", "15"))
STALE_RESEARCH_DAYS = int(os.getenv("STALE_RESEARCH_DAYS", "180"))
FEED_SIZE = int(os.getenv("FEED_SIZE", "7"))
FEED_THRESHOLD = float(os.getenv("FEED_THRESHOLD", "0.08"))


def llm_enabled() -> bool:
    if LLM_MODE == "rules":
        return False
    return bool(LLM_BASE_URL and LLM_API_KEY)
