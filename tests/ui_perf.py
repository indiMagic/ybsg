"""UI-5: frame rate and render time of the board views with the local database as it is (run with 500+ boards).
    python tests/ui_perf.py http://127.0.0.1:8765
Reports frames per second while scrolling each view, the time to switch to it, and the time to re-filter it."""
import os, sys
from playwright.sync_api import sync_playwright
URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765"
FPS = """() => new Promise((done) => { const sc = document.scrollingElement; const main = document.querySelector('main') || sc;
  const el = (main.scrollHeight > main.clientHeight + 10) ? main : sc; let n = 0, worst = 0, last = performance.now(); const t0 = last;
  const step = (now) => { n++; worst = Math.max(worst, now - last); last = now; el.scrollTop += 40; if (now - t0 < 2000) requestAnimationFrame(step); else done({fps: n / ((now - t0) / 1000), worst_ms: worst, height: el.scrollHeight}); };
  requestAnimationFrame(step); })"""
out = []
with sync_playwright() as p:
    b = p.chromium.launch(executable_path=os.environ.get("YBSG_CHROMIUM") or None)
    pg = b.new_page(viewport={"width": 1440, "height": 900})
    pg.goto(URL + "/?nosplash=1"); pg.wait_for_selector("#app:not([hidden])")
    pg.evaluate("location.hash='#/boards'"); pg.wait_for_selector("#bbody > *", timeout=20000); pg.wait_for_timeout(500)
    n = pg.evaluate("document.querySelector('#bcount').textContent")
    for v in ("cards", "table", "graph"):
        ms = pg.evaluate(f"""() => {{ const t = performance.now(); document.querySelector('[data-act=bview][data-v={v}]').click();
            return new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(() => r(performance.now() - t)))); }}""")
        pg.wait_for_timeout(300)
        nodes = pg.evaluate("document.querySelectorAll('#bbody .bcard, #bbody tr, #bbody svg rect').length")
        f = pg.evaluate(FPS)
        filt = pg.evaluate("""() => { const q = document.querySelector('#bq'); const t = performance.now(); q.value = 'imx'; q.dispatchEvent(new Event('input'));
            return new Promise((r) => setTimeout(() => requestAnimationFrame(() => { const d = performance.now() - t - 80; q.value = ''; q.dispatchEvent(new Event('input')); r(d); }), 81)); }""")
        pg.wait_for_timeout(300)
        out.append((v, nodes, ms, f["fps"], f["worst_ms"], filt))
    b.close()
print(n)
for v, nodes, ms, fps, worst, filt in out:
    print(f"{v:6} {nodes:5} nodes  switch {ms:6.0f} ms  scroll {fps:5.1f} fps (worst frame {worst:5.1f} ms)  filter {filt:5.0f} ms")
sys.exit(0 if all(o[3] >= 55 for o in out) else 1)
