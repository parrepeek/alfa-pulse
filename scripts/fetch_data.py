"""Загрузка данных кейса (Google Drive организаторов) и подготовка страниц аналитики.

python scripts/fetch_data.py            # CSV -> data/raw, PDF аналитики -> data/raw/research, страницы -> data/research/pages
"""
import subprocess
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
FILES = {
    "df_Fid_Uid_link_hack.csv": "1ZJCiqgY1ma4iQ1_ltabtUuaFIBkrqnOe",
    "df_Uid_data_hack.csv": "12H7sbLwBcpur0fKaGHPqwPwfXfgQq10b",
    "df_Uid_payment_hack.csv": "1zkvWkAS0kZqijD-og_0Cv3o01SoUMY_Z",
    "research/ALFA-7M26.pdf": "1HZDq3WGRFdzgm1TZ5hWL23Q3I9c2Eq64",
    "research/ALFA-consumption.pdf": "1riR1lhsb6EY1MwGMxdBlS_tI5ed9mIiz",
    "research/ALFA-Macro_Insights-Policy_Rate_11Sep.pdf": "1shHG89LtMgHT1_uEebHmQSFZe_zbeq7-",
    "research/ALFA-macro_stat1H26.pdf": "1khcHwoS0FkG9p_0J4XoHGaGq3B4V0ime",
    "research/ALFA_Russian_Steels_1К21PreviewUpdate_15Apr2021.pdf": "1yLCBrIoyIsubLTEBvBHh6io48ysnW-wn",
    "research/FI_Strategy_3Q26_22072026.pdf": "1hq3Fhyz9ce_sRyGUQB1MdoHeQdfAJfhK",
    "research/Gold_digest_August26.pdf": "1a7TXOCHARfKzUR0hn9Cm9gQSrIldxTDE",
    "research/Inflation_tracker_02.pdf": "19iW3Ph8_wO0HHQY7Ik4lTfC3jRc9gjWV",
    "research/Бонд_Бриф_14092026.pdf": "1bPzix_332N2CyMY0tqCHuYtGsfJWnFGv",
}


def main():
    for name, fid in FILES.items():
        dst = RAW / name
        if dst.exists():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        url = f"https://drive.usercontent.google.com/download?id={fid}&export=download&confirm=t"
        with httpx.stream("GET", url, follow_redirects=True, timeout=300) as r:
            r.raise_for_status()
            with open(dst, "wb") as f:
                for chunk in r.iter_bytes():
                    f.write(chunk)
        print("downloaded", name, dst.stat().st_size)
    subprocess.run([sys.executable, str(ROOT / "scripts" / "render_pages.py"), str(RAW / "research"), str(ROOT / "data" / "research" / "pages")], check=True)


if __name__ == "__main__":
    main()
