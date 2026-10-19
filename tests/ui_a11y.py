"""UI-6: automated WCAG 2.1 AA audit with axe-core on every screen, in both themes. Needs axe.min.js (axe-core 4.x):
    python tests/ui_a11y.py http://127.0.0.1:8765 path/to/axe.min.js
Exits non-zero on any serious or critical violation. Automated checks find roughly a third of WCAG issues; a manual
screen-reader pass is still needed."""
import json, os, sys
from playwright.sync_api import sync_playwright
URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765"
AXE = open(sys.argv[2] if len(sys.argv) > 2 else "axe.min.js", encoding="utf-8").read()
ROUTES = ["dashboard", "projects", "boards", "board/imx8mm-evk", "socs", "drivers", "peripherals", "software", "review", "generate", "database", "reports", "settings"]
bad = 0
with sync_playwright() as p:
    b = p.chromium.launch(executable_path=os.environ.get("YBSG_CHROMIUM") or None)
    for theme in ("dark", "light"):
        pg = b.new_page(viewport={"width": 1440, "height": 900}, color_scheme=theme)
        pg.goto(URL + "/?nosplash=1"); pg.wait_for_selector("#app:not([hidden])")
        pg.evaluate("fetch('/api/project/new',{method:'POST',headers:{'Content-Type':'application/json','X-YBSG-Token':document.querySelector('meta[name=ybsg-token]').content},body:JSON.stringify({board:'imx8mm-evk'})})")
        pg.wait_for_timeout(400); pg.reload(); pg.wait_for_selector("#app:not([hidden])")
        for r in ROUTES:
            pg.evaluate(f"location.hash='#/{r}'"); pg.wait_for_timeout(700)
            pg.add_script_tag(content=AXE)
            res = pg.evaluate("axe.run(document, {runOnly: {type: 'tag', values: ['wcag2a','wcag2aa','wcag21a','wcag21aa']}})")
            for v in res["violations"]:
                if v["impact"] in ("serious", "critical"):
                    bad += 1
                print(f"{theme:5} {r:18} {v['impact']:9} {v['id']}: {v['help']} ({len(v['nodes'])}) e.g. {v['nodes'][0]['target']}")
        pg.close()
    b.close()
print(f"{bad} serious/critical violation group(s)")
sys.exit(1 if bad else 0)
