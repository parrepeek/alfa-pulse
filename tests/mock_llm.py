"""Имитация OpenAI-совместимого шлюза организаторов для тестов LLM-режима (без реальных моделей и токенов)."""
import json
import re

from fastapi import FastAPI, Request

app = FastAPI()
CALLS = []


def _json_in(text):
    m = re.search(r"\{.*\}", text, re.S)
    return json.loads(m.group(0)) if m else {}


@app.post("/chat/completions")
async def completions(req: Request):
    body = await req.json()
    model = body["model"]
    system = body["messages"][0]["content"] if body["messages"][0]["role"] == "system" else ""
    user = body["messages"][-1]["content"]
    CALLS.append(model)
    if "Извлеки из новости" in system:
        news = _json_in(user)
        out = {"event_type": "Ключевая ставка", "summary": news.get("title", ""), "factors": {"ставка": -0.8}, "sectors": [], "countries": [],
               "commodities": [], "regions": [], "companies": [], "facts": [{"text": news.get("title", ""), "quote": news.get("title", "")}], "confidence": 0.9}
    elif "планировщик" in system:
        out = {"factors": ["ставка"], "steps": [{"tool": "search_bank_research", "args": {"query": "ключевая ставка", "factor": "ставка"}, "why": "позиция банка"},
                                                 {"tool": "get_bank_forecast", "args": {"factor": "ставка"}, "why": "прогноз"},
                                                 {"tool": "find_related_events", "args": {}, "why": "контекст"}]}
    elif "персональную интерпретацию" in system:
        ctx = _json_in(user.split("\nИсправь")[0])
        basis = [b for f in ctx.get("profile", {}).get("exposure", {}).values() for b in f.get("because", []) if b][:3]
        out = {"why_it_matters": [{"text": "Высокая долговая нагрузка группы: " + "; ".join(basis), "basis": basis}],
               "impacts": [{"type": "риск", "text": "Стоимость обслуживания долга не снизится до конца года.", "mechanism": "ставка", "horizon": "до конца года", "basis": basis[:1]}],
               "actions": [{"text": "Проверить долю плавающих ставок.", "product": None}], "questions": ["Какая доля долга по плавающей ставке?"]}
    elif "строгий редактор" in system:
        out = {"issues": []}
    else:
        out = {"answer": "Ответ по данным карточки."}
    return {"choices": [{"message": {"role": "assistant", "content": json.dumps(out, ensure_ascii=False)}}],
            "usage": {"total_tokens": 321}, "model": model}
