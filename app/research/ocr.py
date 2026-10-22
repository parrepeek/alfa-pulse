"""Распознавание сканов аналитики банка моделью Qwen3-VL (рабочий контур).

Все 9 PDF кейса - изображения без текстового слоя. Страницы рендерятся scripts/render_pages.py,
затем каждая страница отправляется в Qwen3-VL с просьбой вернуть текст как есть (таблицы - в Markdown).
Локально без ключа используется scripts/ocr_windows.ps1 (встроенный OCR Windows); его текст уже лежит в
data/research/text, точные числа для выводов берутся из проверенной таблицы data/research/forecasts.json.
"""
import base64
from pathlib import Path

from .. import config
from ..llm import chat

PROMPT = ("Это страница аналитического материала Альфа-Банка. Перепиши весь текст страницы дословно, сохраняя абзацы. "
          "Таблицы оформи в Markdown. Подписи к графикам - одной строкой «График: ...». Ничего не добавляй от себя.")


def ocr_page(png: Path) -> str:
    b64 = base64.b64encode(png.read_bytes()).decode()
    msg = [{"role": "user", "content": [{"type": "text", "text": PROMPT},
                                          {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}]}]
    return chat("vision", msg, max_tokens=4000, temperature=0)["text"]


def ocr_all(pages_dir: Path = config.DATA / "research" / "pages", out_dir: Path = config.RESEARCH_TEXT, overwrite: bool = False):
    out_dir.mkdir(parents=True, exist_ok=True)
    done = 0
    for png in sorted(pages_dir.glob("*.png")):
        dst = out_dir / (png.stem + ".txt")
        if dst.exists() and not overwrite:
            continue
        dst.write_text(ocr_page(png), encoding="utf-8")
        done += 1
    return done
