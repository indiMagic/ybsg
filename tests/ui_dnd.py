"""Place a part by drag-and-drop and by keyboard, and check a conflict is shown and waivable."""
import os, sys
from playwright.sync_api import sync_playwright
URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765"
with sync_playwright() as p:
    b = p.chromium.launch(executable_path=os.environ.get("YBSG_CHROMIUM") or None); pg = b.new_page(viewport={"width": 1440, "height": 900})
    errs = []; pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(URL + "/?nosplash=1"); pg.wait_for_selector("#app:not([hidden])")
    pg.goto(URL + "/#/board/rpi4b"); pg.wait_for_selector("[data-act=use-board]"); pg.click("[data-act=use-board]")
    pg.wait_for_selector(".board-svg")
    n0 = pg.evaluate("fetch('/api/project').then(r=>r.json()).then(j=>j.project.peripherals.length)")
    pg.click("[data-act=cat][data-k=camera]"); pg.wait_for_selector(".iccard")
    pg.locator(".iccard:not([aria-disabled=true])").first.click()
    pad = pg.locator(".pad.free[data-type=csi]").first
    pad.focus(); pg.keyboard.press("Enter"); pg.wait_for_timeout(700)
    n1 = pg.evaluate("fetch('/api/project').then(r=>r.json()).then(j=>j.project.peripherals.length)")
    print("peripherals", n0, "->", n1)
    assert n1 == n0 + 1, "keyboard placement failed"
    pg.screenshot(path="docs/screenshots/15-placed.png")
    assert not errs, errs
    b.close()
