"""Уточняющие вопросы по карточке: другой разрез данных, сценарий, первоисточник.

Ответ строится только из контекста карточки и инструментов. С ключом LLM формулирует Kimi-K2.6,
без ключа - шаблоны по намерению вопроса. Каждый ответ содержит ссылки на основания.
"""
import json

from .. import config, profiles
from ..llm import LLMUnavailable, json_call
from ..research import index as research


def _intent(q: str) -> str:
    q = q.lower()
    if any(w in q for w in ("компани", "юрлиц", "по группе", "дочер")):
        return "companies"
    if any(w in q for w in ("контрагент", "поставщ", "покупат", "клиент")):
        return "counterparties"
    if any(w in q for w in ("источник", "откуда", "первоисточ", "основан", "ссылк")):
        return "sources"
    if any(w in q for w in ("если", "сценар", "а что", "что будет")):
        return "scenario"
    if any(w in q for w in ("прогноз", "банк", "альфа", "ставк", "курс", "инфляц")):
        return "bank"
    return "general"


def answer(question: str, card: dict) -> dict:
    pr = profiles.get(card["client"]["group_id"])
    intent = _intent(question)
    refs = []
    if intent == "companies":
        rows = [f"• {c['id']}: {c['okved']}, {c['region']}, {c['revenue_share']:.0%} выручки группы"
                + (", кредит" if c["credit"] else "") + (", лизинг" if c["leasing"] else "") + (", ВЭД" if c["fts"] else "")
                for c in pr.get("top_companies", [])]
        text = "Крупнейшие компании группы и их признаки, влияющие на это событие:\n" + "\n".join(rows)
        refs = ["данные клиента: справочник компаний группы"]
    elif intent == "counterparties":
        sup = ", ".join(f"{k} {v:.0%}" for k, v in pr["suppliers_by_sector"].items())
        cus = ", ".join(f"{k} {v:.0%}" for k, v in pr["customers_by_sector"].items())
        text = (f"Закупки по отраслям поставщиков: {sup}.\nПоступления по отраслям покупателей: {cus}.\n"
                f"Крупнейший поставщик: {pr['top_supplier_share']:.0%} платежей, крупнейший покупатель: {pr['top_customer_share']:.0%} поступлений. "
                f"Доля контрагентов вне данных банка: {pr['data_quality']['counterparties_outside_dataset']:.0%}, поэтому оценка неполная.")
        refs = ["данные клиента: граф платежей (без внутригрупповых)"]
    elif intent == "sources":
        text = "Вывод основан на:\n" + "\n".join(f"• {s['type']}: {s['title']} ({s['ref']}{', ' + s['date'] if s['date'] else ''})" for s in card["sources"])
        refs = [s["ref"] for s in card["sources"]]
    elif intent in ("scenario", "bank"):
        fac = card["relevance"]["main_factor"]
        from .tools import FORECAST_FOR
        fc = research.forecasts(FORECAST_FOR.get(fac, "ключевая ставка"))
        lines = [f"• {f['indicator']} {f['value']} ({f['period']}), документ {f['doc_id']}, с. {f['page']}, {f['date']}" + (f" - {f['note'].split(' | ')[0]}" if f["note"] and not f["note"].startswith(" |") else "")
                 for f in fc[:4]]
        text = ("Прогнозы Альфа-Банка по теме события:\n" + "\n".join(lines)) if lines else "Отдельного прогноза банка по этой теме в материалах нет."
        if intent == "scenario":
            text += ("\n\nСценарный разбор (вывод AI): если показатель пойдет по более мягкому сценарию банка, нагрузка, описанная в блоке «Последствия», "
                     "снизится; если отклонится в худшую сторону - риски из карточки усилятся. Для точного расчета нужны ваши параметры: "
                     + "; ".join(card.get("questions", [])[:2]))
        refs = [f"{f['doc_id']}, с. {f['page']}" for f in fc[:4]]
    else:
        text = ("Коротко: " + " ".join(i["text"] for i in card["impacts"][:2]) +
                "\nМожно спросить: «покажи по компаниям группы», «какие контрагенты затронуты», «а если ставка снизится?», «на чем основан вывод?»")
        refs = [s["ref"] for s in card["sources"][:3]]

    if config.llm_enabled():  # LLM формулирует ответ по тем же данным, без новых фактов
        try:
            obj, _ = json_call("chat", "Ответь руководителю компании кратко и по делу, используя только данные из context. "
                               "Разделяй позицию банка и свои выводы. Верни JSON {answer}.",
                               json.dumps({"question": question, "context": text, "card": {k: card[k] for k in ("event", "impacts", "bank_view")}},
                                          ensure_ascii=False), required=["answer"], max_tokens=600)
            text = obj["answer"]
        except (LLMUnavailable, ValueError):
            pass
    return {"intent": intent, "answer": text, "refs": refs}
