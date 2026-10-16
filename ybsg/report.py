"""PDF / Markdown / JSON report built ONLY from the pinned manifest (RPT-1, RPT-2): offline, no catalog access."""
from __future__ import annotations

import json
import shutil
import zlib
import subprocess
from pathlib import Path

from reportlab.graphics.shapes import Drawing, Line, Rect, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (BaseDocTemplate, Frame, PageBreak, PageTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether)
from reportlab.platypus.tableofcontents import TableOfContents

from .branding import brand

INK = colors.HexColor("#1b2430")
ACCENT = colors.HexColor("#1f6feb")
SOFT = colors.HexColor("#eef2f7")


def _esc(s) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class _Doc(BaseDocTemplate):
    def __init__(self, path: str, manifest: dict, **kw):
        super().__init__(path, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=20 * mm, bottomMargin=20 * mm,
                         title=f"{manifest['project']['name']} report", author=manifest["project"]["author"],
                         subject="Yocto board support package report", creator=f"{brand()['product']} {manifest['tool']['version']}", **kw)
        self.m = manifest
        frame = Frame(self.leftMargin, self.bottomMargin, self.width, self.height, id="f")
        self.addPageTemplates([PageTemplate(id="body", frames=[frame], onPage=self._footer)])
        self._bm = 0

    def _footer(self, canv, doc) -> None:
        b = brand()
        canv.saveState()
        canv.setFont("Helvetica", 7.5)
        canv.setFillColor(colors.HexColor("#5b6675"))
        canv.drawString(18 * mm, 11 * mm, f"{b['company']}  |  {b['website']}  |  {b['rights']}")
        canv.drawRightString(A4[0] - 18 * mm, 11 * mm, f"Page {doc.page}")
        canv.setStrokeColor(colors.HexColor("#d5dbe3"))
        canv.line(18 * mm, 14.5 * mm, A4[0] - 18 * mm, 14.5 * mm)
        canv.restoreState()

    def afterFlowable(self, fl) -> None:
        if isinstance(fl, Paragraph) and fl.style.name in ("H1", "H2"):
            lvl = 0 if fl.style.name == "H1" else 1
            text = fl.getPlainText()
            key = "bm%08x" % zlib.crc32(f"{lvl}:{text}".encode())  # deterministic across passes
            self.canv.bookmarkPage(key)
            self.canv.addOutlineEntry(text, key, lvl, closed=False)
            self.notify("TOCEntry", (lvl, text, self.page, key))


def _styles() -> dict:
    ss = getSampleStyleSheet()
    return {
        "H1": ParagraphStyle("H1", parent=ss["Heading1"], fontName="Helvetica-Bold", fontSize=16, textColor=INK, spaceBefore=10, spaceAfter=6),
        "H2": ParagraphStyle("H2", parent=ss["Heading2"], fontName="Helvetica-Bold", fontSize=12, textColor=ACCENT, spaceBefore=8, spaceAfter=4),
        "B": ParagraphStyle("B", parent=ss["BodyText"], fontName="Helvetica", fontSize=9, leading=12.5, textColor=INK),
        "S": ParagraphStyle("S", parent=ss["BodyText"], fontName="Helvetica", fontSize=7.5, leading=9.5, textColor=INK, splitLongWords=True, wordWrap="CJK"),
        "M": ParagraphStyle("M", parent=ss["BodyText"], fontName="Courier", fontSize=6.6, leading=8.2, textColor=INK, splitLongWords=True, wordWrap="CJK"),
        "T": ParagraphStyle("T", fontName="Helvetica-Bold", fontSize=26, leading=30, textColor=INK),
        "Sub": ParagraphStyle("Sub", fontName="Helvetica", fontSize=12, leading=16, textColor=colors.HexColor("#5b6675")),
        "TH": ParagraphStyle("TH", fontName="Helvetica-Bold", fontSize=7.5, leading=9.5, textColor=colors.white),
    }


def _table(rows: list[list], widths: list[float], st: dict, mono_cols: tuple = ()) -> Table:
    head = [Paragraph(_esc(h), st["TH"]) for h in rows[0]]
    body = [[Paragraph(_esc(c), st["M"] if i in mono_cols else st["S"]) for i, c in enumerate(r)] for r in rows[1:]] or [[Paragraph("none", st["S"])] + [""] * (len(rows[0]) - 1)]
    t = Table([head] + body, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), INK), ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, SOFT]),
                           ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d5dbe3")), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                           ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3), ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
    return t


def _diagram(m: dict, width: float) -> Drawing:
    """SoC in the middle, peripherals on both sides, straight connectors that never cross a box."""
    blocks = m["block_diagram"]["blocks"]
    left, right = blocks[0::2], blocks[1::2]
    rows = max(len(left), len(right), 1)
    pitch = 40
    h = rows * pitch + 20
    d = Drawing(width, h)
    soc_w = 130
    cx = width / 2
    d.add(Rect(cx - soc_w / 2, 8, soc_w, h - 16, rx=6, ry=6, fillColor=ACCENT, strokeColor=INK))
    d.add(String(cx, h / 2 + 2, m["block_diagram"]["soc"][:22], fontName="Helvetica-Bold", fontSize=11, fillColor=colors.white, textAnchor="middle"))
    d.add(String(cx, h / 2 - 11, "SoC", fontName="Helvetica", fontSize=7.5, fillColor=colors.white, textAnchor="middle"))
    bw = (width - soc_w) / 2 - 24
    for side, items in ((0, left), (1, right)):
        for i, b in enumerate(items):
            y = h - 12 - (i + 1) * pitch + 8
            x = 4 if side == 0 else width - bw - 4
            x_edge = x + bw if side == 0 else x
            soc_edge = cx - soc_w / 2 if side == 0 else cx + soc_w / 2
            d.add(Line(x_edge, y + 15, soc_edge, y + 15, strokeColor=colors.HexColor("#8a97a8"), strokeWidth=0.9))
            d.add(Rect(x, y, bw, 30, rx=4, ry=4, fillColor=SOFT, strokeColor=colors.HexColor("#8a97a8")))
            d.add(String(x + bw / 2, y + 18, b["label"][:40], fontName="Helvetica", fontSize=7, fillColor=INK, textAnchor="middle"))
            d.add(String(x + bw / 2, y + 7, b["instance"][:40], fontName="Helvetica-Oblique", fontSize=6, fillColor=colors.HexColor("#5b6675"), textAnchor="middle"))
    return d


def build_pdf(manifest: dict, out: Path, flashing_text: str = "") -> Path:
    st = _styles()
    doc = _Doc(str(out), manifest)
    m, W = manifest, doc.width
    s: list = []
    b = brand()
    # cover
    s += [Spacer(1, 45 * mm), Paragraph("BSP", ParagraphStyle("k", fontName="Helvetica-Bold", fontSize=11, textColor=ACCENT)), Spacer(1, 3 * mm),
          Paragraph(_esc(m["project"]["name"]), st["T"]), Paragraph("Board support package report", st["Sub"]), Spacer(1, 14 * mm)]
    cover = [["Date (manifest)", m["tool"]["database_synced"][:10] or "n/a"], ["Tool", f"{m['tool']['name']} {m['tool']['version']}"], ["Database version", m["tool"]["database_version"]],
             ["Author", m["project"]["author"]], ["Company", b["company"]], ["Website", b["website"]], ["Project hash", m["project"]["hash"][:24] + "..."]]
    t = Table([[Paragraph(_esc(a), st["S"]), Paragraph(_esc(v), st["B"])] for a, v in cover], colWidths=[40 * mm, W - 40 * mm])
    t.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#d5dbe3")), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    s += [t, Spacer(1, 10 * mm), Paragraph(_esc(b["rights"]), st["S"]), PageBreak()]
    # contents
    toc = TableOfContents()
    toc.levelStyles = [ParagraphStyle("t1", fontName="Helvetica-Bold", fontSize=10, leftIndent=0, spaceBefore=4), ParagraphStyle("t2", fontName="Helvetica", fontSize=9, leftIndent=12)]
    s += [Paragraph("Contents", st["H1"]), toc, PageBreak()]
    # summary
    sm = m["summary"]
    s += [Paragraph("1. Summary", st["H1"])]
    rows = [["Item", "Value"], ["Target", f"{m['project']['board']} ({m['project']['soc']}, {m['project']['soc_vendor']})"], ["Yocto release", sm["release"]], ["Kernel", sm["kernel"]],
           ["Bootloader", sm["bootloader"]], ["Image type", sm["image"]], ["Machine", sm["machine"]], ["Architecture", sm["arch"]], ["Baseline", sm["yocto_baseline"]],
           ["Generation mode", "thin overlay on vendor BSP" if m["project"]["overlay"] else "from scratch"]]
    s += [_table(rows, [40 * mm, W - 40 * mm], st)]
    # diagram
    s += [Paragraph("2. Block diagram", st["H1"]), _diagram(m, W)]
    # peripherals
    s += [Paragraph("3. Peripheral table", st["H1"])]
    rows = [["Category", "Instance", "IC part", "Vendor", "Interface", "Speed/mode", "Pins used", "Conf."]]
    for p in m["peripherals"]:
        rows.append([p["category"], p["instance"], p["ic"], p["vendor"], p["interface"], p["speed"], p["pins"], p["confidence"]])
    s += [_table(rows, [20 * mm, 27 * mm, 25 * mm, 18 * mm, 18 * mm, 16 * mm, W - 144 * mm, 20 * mm], st)]
    # drivers
    s += [Paragraph("4. Driver table", st["H1"])]
    rows = [["Driver", "IC", "Source", "Version", "Kernel range", "Compatible", "CONFIG symbols", "Licence"]]
    for d in m["drivers"]:
        rows.append([d["name"], d["ic"], d["source"], d["version"], d["kernel_range"], d["compatible"], ", ".join(d["kconfig"]), d["licence"]])
    s += [_table(rows, [24 * mm, 18 * mm, 14 * mm, 13 * mm, 18 * mm, 26 * mm, W - 133 * mm, 20 * mm], st)]
    # memory
    r = m["ram"]
    s += [Paragraph("5. Memory and storage map", st["H1"]), Paragraph(f"RAM: {_esc(r.get('type'))}, {r.get('size_mb')} MB, {r.get('bus_width')}-bit bus, {r.get('ranks', 1)} rank(s). Boot order: {', '.join(m['boot_order'])}.", st["B"]), Spacer(1, 3 * mm)]
    rows = [["Partition", "Offset", "Size"]] + [[q["name"], hex(q["offset"]), hex(q["size"])] for q in m["flash_partitions"]]
    s += [_table(rows, [50 * mm, 40 * mm, 40 * mm], st)]
    # software
    s += [Paragraph("6. Software stack", st["H1"])]
    rows = [["Repository", "URL", "Branch", "Pinned commit"]] + [[l["repo"], l["url"], l["branch"], l["commit"]] for l in m["layers"]]
    s += [_table(rows, [32 * mm, 62 * mm, 28 * mm, W - 122 * mm], st, mono_cols=(3,))]
    if any(l["commit"] == "UNPINNED" for l in m["layers"]):
        s += [Spacer(1, 2 * mm), Paragraph("Some sources are UNPINNED because they could not be resolved offline. Re-run generation online to pin them (GEN-2).", st["B"])]
    # files
    s += [Paragraph("7. File manifest", st["H1"])]
    rows = [["Path", "Purpose", "SHA-256"]] + [[k, v["purpose"], v["sha256"]] for k, v in m["files"].items()]
    s += [_table(rows, [64 * mm, 34 * mm, W - 98 * mm], st, mono_cols=(0, 2))]
    # warnings
    s += [Paragraph("8. Warnings and waivers", st["H1"])]
    rows = [["Severity", "Code", "Message", "Waived"]] + [[w["severity"], w["code"], w["message"], "yes" if w["waived"] else ""] for w in m["warnings"]]
    s += [_table(rows, [16 * mm, 32 * mm, W - 62 * mm, 14 * mm], st)]
    extra = []
    if m["no_driver"]: extra.append("ICs marked 'no driver': " + ", ".join(m["no_driver"]))
    if m["unverified"]: extra.append("Unverified records: " + ", ".join(m["unverified"]))
    if m["distro_note"]: extra.append(m["distro_note"])
    for e in extra:
        s += [Spacer(1, 2 * mm), Paragraph(_esc(e), st["B"])]
    # provenance
    s += [Paragraph("9. Provenance", st["H1"])]
    rows = [["Kind", "Record", "Source", "Fetched", "Confidence", "Disputed"]] + [[p["kind"], p["name"], p["source"], p["fetched"][:10], p["confidence"], "yes" if p["disputed"] else ""] for p in m["provenance"]]
    s += [_table(rows, [14 * mm, 40 * mm, W - 114 * mm, 20 * mm, 22 * mm, 18 * mm], st)]
    s += [Spacer(1, 2 * mm), Paragraph("Confidence: high = parsed from device tree/upstream; medium = curated or parsed from a web page; unverified = user-entered (DB-8).", st["S"])]
    # licences
    s += [Paragraph("10. Licence summary", st["H1"]), Paragraph("Licences present: " + _esc("; ".join(m["licences"])), st["B"])]
    if m["licence_gates"]:
        s += [Paragraph("Licence acceptance required by you (YBSG never accepts on your behalf): " + _esc("; ".join(m["licence_gates"])), st["B"])]
    # build
    s += [Paragraph("11. Build results", st["H1"])]
    rows = [["Stage", "State", "Detail"]] + [[k, v["state"], v["message"]] for k, v in m["stages"].items()]
    s += [_table(rows, [24 * mm, 18 * mm, W - 42 * mm], st)]
    bi = m.get("build") or {}
    s += [Spacer(1, 2 * mm), Paragraph("Build ran: " + ("yes. Artefacts: " + _esc(", ".join(bi.get("images", [])[:12])) if bi.get("ran") else "no (files generated without a BitBake build)."), st["B"])]
    # flashing
    s += [Paragraph("12. Flashing guide", st["H1"])]
    for ln in (flashing_text or "See FLASHING.md in the output folder.").splitlines():
        s.append(Paragraph(_esc(ln) if ln.strip() else "&nbsp;", st["M"] if ln.startswith(("sudo", "uuu", "flashcp", "rk", "dfu", "STM32", "zcat", "dd")) else st["S"]))
    from . import plugins as _pl
    for n, (title, fn) in enumerate(_pl.REPORT_SECTIONS, 13):  # NFR-11: third-party report sections
        try:
            lines = list(fn(m))
        except Exception as e:  # noqa: BLE001
            lines = [f"Section failed: {e}"]
        s += [Paragraph(f"{n}. {_esc(title)}", st["H1"])] + [Paragraph(_esc(l), st["S"]) for l in lines]
    doc.multiBuild(s)
    return out


def to_markdown(m: dict) -> str:
    sm = m["summary"]
    L = [f"# {m['project']['name']}: BSP report", "", f"{brand()['company']} | {brand()['website']} | {brand()['rights']}", "", "## Summary", "",
         f"- Target: {m['project']['board']} ({m['project']['soc']})", f"- Release: {sm['release']}", f"- Kernel: {sm['kernel']}", f"- Image: {sm['image']}", f"- Machine: `{sm['machine']}`", "", "## Peripherals", "",
         "| Category | Instance | IC | Vendor | Interface | Pins |", "|---|---|---|---|---|---|"]
    L += [f"| {p['category']} | {p['instance']} | {p['ic']} | {p['vendor']} | {p['interface']} | {p['pins']} |" for p in m["peripherals"]]
    L += ["", "## Drivers", "", "| Driver | IC | Source | Version | Kernel range | CONFIG | Licence |", "|---|---|---|---|---|---|---|"]
    L += [f"| {d['name']} | {d['ic']} | {d['source']} | {d['version']} | {d['kernel_range']} | {', '.join(d['kconfig'])} | {d['licence']} |" for d in m["drivers"]]
    L += ["", "## Software stack", "", "| Repo | Branch | Commit |", "|---|---|---|"] + [f"| {l['repo']} | {l['branch']} | `{l['commit']}` |" for l in m["layers"]]
    L += ["", "## Files", "", "| Path | Purpose | SHA-256 |", "|---|---|---|"] + [f"| `{k}` | {v['purpose']} | `{v['sha256']}` |" for k, v in m["files"].items()]
    L += ["", "## Warnings and waivers", ""] + [f"- **{w['severity']}** {w['code']}: {w['message']}{' (waived)' if w['waived'] else ''}" for w in m["warnings"]]
    return "\n".join(L) + "\n"


def try_pdfa(pdf: Path) -> str:
    """RPT-3: PDF/A-2 conversion via Ghostscript when installed. Returns a status string for the log."""
    gs = shutil.which("gs") or shutil.which("gswin64c")
    if not gs:
        return "PDF/A-2: Ghostscript not installed; produced a standard PDF with TOC, bookmarks, page numbers and searchable text"
    tmp = pdf.with_suffix(".pdfa.pdf")
    r = subprocess.run([gs, "-dPDFA=2", "-dBATCH", "-dNOPAUSE", "-dQUIET", "-sColorConversionStrategy=RGB", "-sDEVICE=pdfwrite", "-dPDFACompatibilityPolicy=1", f"-sOutputFile={tmp}", str(pdf)], capture_output=True, text=True)
    if r.returncode == 0 and tmp.exists():
        tmp.replace(pdf)
        return "PDF/A-2 conversion applied"
    return "PDF/A-2 conversion failed; kept the standard PDF"


def build_all(outdir: str | Path, log=None) -> list[str]:
    """Read bsp-manifest.json only, then write report/*.pdf, .md and .json. `log(level, message)` receives the PDF/A status."""
    outdir = Path(outdir)
    m = json.loads((outdir / "bsp-manifest.json").read_text())
    rd = outdir / "report"
    rd.mkdir(exist_ok=True)
    flash = (outdir / "FLASHING.md").read_text() if (outdir / "FLASHING.md").exists() else ""
    pdf = build_pdf(m, rd / f"{m['project']['slug']}-report.pdf", flash)
    status = try_pdfa(pdf)
    if log:
        log("warning" if "failed" in status else "info", status)
    md = rd / f"{m['project']['slug']}-report.md"
    md.write_text(to_markdown(m))
    js = rd / f"{m['project']['slug']}-report.json"
    js.write_text(json.dumps(m, indent=2, sort_keys=True))
    return [str(pdf), str(md), str(js)]
