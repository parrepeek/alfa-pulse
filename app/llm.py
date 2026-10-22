"""Клиент разрешенных LLM через OpenAI-совместимый шлюз организаторов.

- роль -> модель (config.MODELS), только модели из списка кейса;
- учет токенов в журнале и дневной лимит (бюджет 500 руб./день);
- json_call: строгий JSON с одной попыткой исправления;
- без ключа (LLM_MODE=rules или нет LLM_API_KEY) вызывающий код использует детерминированный режим.
"""
import json
import re
import time

import httpx

from . import config, db


class LLMUnavailable(RuntimeError):
    pass


def chat(role: str, messages: list, max_tokens: int = 1200, temperature: float = 0.2, trace_id: str = "", user_id: str = "") -> dict:
    if not config.llm_enabled():
        raise LLMUnavailable("LLM не настроен (нет LLM_BASE_URL/LLM_API_KEY или LLM_MODE=rules)")
    if db.tokens_today() > config.LLM_DAILY_TOKEN_LIMIT:
        raise LLMUnavailable("Дневной лимит токенов исчерпан")
    model = config.MODELS[role]
    t0 = time.time()
    r = httpx.post(config.LLM_BASE_URL.rstrip("/") + "/chat/completions",
                   headers={"Authorization": f"Bearer {config.LLM_API_KEY}"},
                   json={"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": temperature}, timeout=120)
    r.raise_for_status()
    data = r.json()
    usage = data.get("usage", {})
    tokens = int(usage.get("total_tokens", 0))
    db.log_tool(trace_id, user_id, f"llm:{role}", {"model": model}, (time.time() - t0) * 1000, True, tokens, model)
    return {"text": data["choices"][0]["message"]["content"], "tokens": tokens, "model": model}


def _parse_json(text: str):
    m = re.search(r"\{.*\}|\[.*\]", text, re.S)
    if not m:
        raise ValueError("в ответе нет JSON")
    return json.loads(m.group(0))


def json_call(role: str, system: str, user: str, required: list[str] | None = None, **kw):
    """Вызов с ответом строго в JSON; проверка обязательных полей; одна попытка исправления."""
    msgs = [{"role": "system", "content": system + "\nОтвечай только валидным JSON без пояснений."},
            {"role": "user", "content": user}]
    last = None
    for _ in range(2):
        res = chat(role, msgs, **kw)
        try:
            obj = _parse_json(res["text"])
            missing = [k for k in (required or []) if k not in obj]
            if missing:
                raise ValueError(f"нет полей {missing}")
            return obj, res
        except (ValueError, json.JSONDecodeError) as e:
            last = e
            msgs += [{"role": "assistant", "content": res["text"]}, {"role": "user", "content": f"Ошибка формата: {e}. Верни исправленный JSON."}]
    raise ValueError(f"LLM не вернула корректный JSON: {last}")
