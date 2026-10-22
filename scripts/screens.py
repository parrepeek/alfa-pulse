"""Скриншоты сквозного сценария (для README и презентации): python scripts/screens.py [url]"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765"
OUT = Path(__file__).resolve().parents[1] / "docs" / "screens"
OUT.mkdir(parents=True, exist_ok=True)

with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True)
    pg = b.new_page(viewport={"width": 1440, "height": 900})
    pg.goto(URL)
    pg.wait_for_selector(".user-card")
    pg.screenshot(path=OUT / "01_login.png")
    pg.click(".user-card[data-u='u_cfo']")
    pg.wait_for_function("document.querySelector('#w-head').textContent !== 'Загрузка…'")
    pg.screenshot(path=OUT / "02_home.png")
    pg.click(".side a[data-view='pulse']")
    pg.wait_for_selector(".item")
    pg.screenshot(path=OUT / "03_feed.png", full_page=True)
    pg.click(".item >> nth=1")
    pg.wait_for_selector(".block h4")
    pg.screenshot(path=OUT / "04_card_top.png")
    pg.screenshot(path=OUT / "05_card_full.png", full_page=False)
    pg.click("text=Как получен вывод")
    pg.wait_for_selector(".trace-step")
    pg.click("text=А если ставка снизится?")
    pg.wait_for_selector(".msg.a")
    pg.evaluate("document.querySelector('.drawer-body').scrollTo(0, document.querySelector('.drawer-body').scrollHeight)")
    pg.screenshot(path=OUT / "06_card_trace_chat.png")
    pg.click("#drawer-close")
    pg.click(".side a[data-view='profile']")
    pg.wait_for_selector(".expo")
    pg.screenshot(path=OUT / "07_profile.png", full_page=True)
    pg.click(".side a[data-view='quality']")
    pg.wait_for_selector("#quality table")
    pg.screenshot(path=OUT / "08_quality.png", full_page=True)
    pg.set_viewport_size({"width": 390, "height": 844})
    pg.click(".side a[data-view='pulse']") if pg.is_visible(".side") else None
    pg.evaluate("show('pulse')")
    pg.screenshot(path=OUT / "09_mobile_feed.png")
    b.close()
print("ok", OUT)
