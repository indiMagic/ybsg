"""Drives the real GUI in headless Chromium: walks every screen, fails on console/page errors, saves screenshots."""
import sys, os
from playwright.sync_api import sync_playwright
URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765"
OUT = sys.argv[2] if len(sys.argv) > 2 else "docs/screenshots"
errs = []
with sync_playwright() as p:
    b = p.chromium.launch(executable_path=os.environ.get("YBSG_CHROMIUM") or None)  # e.g. /usr/bin/google-chrome
    pg = b.new_page(viewport={"width": 1440, "height": 900})
    pg.on("console", lambda m: errs.append(("console", m.text)) if m.type == "error" else None)
    pg.on("response", lambda r: errs.append(("404", r.url)) if r.status == 404 else None)
    pg.on("pageerror", lambda e: errs.append(("pageerror", str(e))))
    pg.goto(URL + "/"); pg.wait_for_timeout(150); pg.screenshot(path=f"{OUT}/01-splash.png")
    pg.wait_for_selector("#app:not([hidden])"); pg.wait_for_timeout(500)
    def shot(name, route, wait=600):
        pg.evaluate(f"location.hash='#/{route}'"); pg.wait_for_timeout(wait); pg.screenshot(path=f"{OUT}/{name}.png")
    shot("02-dashboard", "dashboard")
    shot("03-boards", "boards")
    shot("04-board-detail", "board/imx8mm-evk")
    shot("05-socs", "socs")
    shot("06-drivers", "drivers")
    pg.evaluate("document.querySelector('[data-act=use-board]')") 
    pg.goto(URL + "/#/board/imx8mm-evk?nosplash=1"); pg.wait_for_timeout(500)
    pg.click("[data-act=use-board]"); pg.wait_for_timeout(900)
    pg.screenshot(path=f"{OUT}/07-peripherals.png")
    shot("08-software", "software"); shot("09-review", "review"); shot("10-database", "database", 900)
    pg.evaluate("location.hash='#/generate'"); pg.wait_for_timeout(500)
    pg.click("#gen-go"); pg.wait_for_timeout(4000); pg.screenshot(path=f"{OUT}/11-generate.png")
    shot("12-reports", "reports", 800); shot("13-settings", "settings")
    pg.click("#help-btn"); pg.click("[data-act=about]"); pg.wait_for_timeout(300); pg.screenshot(path=f"{OUT}/14-about.png")
    b.close()
for e in errs: print("ERR", e)
sys.exit(1 if errs else 0)
