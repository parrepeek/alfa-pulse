"""Рендер страниц PDF аналитики банка в PNG для OCR (Windows OCR локально или Qwen3-VL)."""
import sys
from pathlib import Path

import pymupdf

SRC = Path(sys.argv[1])  # папка с PDF
DST = Path(sys.argv[2])  # data/research/pages
DST.mkdir(parents=True, exist_ok=True)
for pdf in sorted(SRC.glob("*.pdf")):
    doc = pymupdf.open(pdf)
    for i, page in enumerate(doc, 1):
        long_side = max(page.rect.width, page.rect.height)
        dpi = int(min(220, 2300 / (long_side / 72)))  # длинная сторона ~2300 px (лимит OCR ~10000)
        page.get_pixmap(dpi=dpi).save(DST / f"{pdf.stem}__p{i:02d}.png")
    print(pdf.name, doc.page_count)
