"""Command line (PKG-6): the same binary runs headless for scripts and CI, e.g. `ybsg generate project.ybsg`."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__, project as P
from .branding import about_text, brand
from .catalog import Catalog
from .graph import CapabilityGraph
from .orchestrator import Job, find_build_backend, resource_check
from .sync import SyncService


def _print_issues(v: dict) -> None:
    for i in v["issues"]:
        if i["severity"] != "info":
            tag = "WAIVED" if i.get("waived") else i["severity"].upper()
            print(f"  [{tag}] {i['code']}: {i['message']}" + (f"\n          fix: {i['fix']}" if i.get("fix") and i["severity"] == "error" else ""))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ybsg", description=f"{brand()['product_long']} {__version__}")
    sub = ap.add_subparsers(dest="cmd")
    g = sub.add_parser("gui", help="open the desktop app (default)")
    g.add_argument("--port", type=int, default=0); g.add_argument("--no-window", action="store_true")
    n = sub.add_parser("new", help="create a project file from a board or SoC")
    n.add_argument("--board"); n.add_argument("--soc"); n.add_argument("--name"); n.add_argument("-o", "--out", required=True)
    v = sub.add_parser("validate", help="check a project against the capability graph"); v.add_argument("project")
    gen = sub.add_parser("generate", help="generate the BSP and report from a project file")
    gen.add_argument("project"); gen.add_argument("-o", "--out", default=None)
    gen.add_argument("--build", action="store_true", help="also run the BitBake build (needs kas + Docker/Podman or WSL2)")
    gen.add_argument("--policy", choices=["merge", "keep", "replace"], default="merge", help="what to do with files you edited (GEN-12)")
    gen.add_argument("--offline", action="store_true"); gen.add_argument("--fetch", action="store_true")
    gen.add_argument("--waive-all", action="store_true", help="waive every waivable conflict (recorded in the report)")
    s = sub.add_parser("search", help="search the catalog"); s.add_argument("query"); s.add_argument("--type", default="soc,board,ic,driver")
    d = sub.add_parser("db", help="local database"); d.add_argument("action", choices=["status", "check", "update", "find", "links"])
    r = sub.add_parser("report", help="rebuild the PDF/Markdown/JSON report from an output folder"); r.add_argument("outdir")
    x = sub.add_parser("export", help="export generated output as zip, git repo or kas file"); x.add_argument("outdir"); x.add_argument("--zip"); x.add_argument("--git"); x.add_argument("--kas")
    sub.add_parser("about"); sub.add_parser("doctor", help="show build-backend and host resources")
    a = ap.parse_args(argv)

    if a.cmd in (None, "gui"):
        from .launcher import run_gui
        return run_gui(getattr(a, "port", 0), not getattr(a, "no_window", False))
    if a.cmd == "about":
        print(about_text()); return 0
    if a.cmd == "doctor":
        print(json.dumps({"build_backend": find_build_backend(), "resources": resource_check()}, indent=2)); return 0
    if a.cmd == "report":
        from . import report
        for f in report.build_all(a.outdir): print(f)
        return 0
    if a.cmd == "export":
        from . import exporter
        if a.zip: print(exporter.export_zip(a.outdir, a.zip))
        if a.git: print(exporter.export_git(a.outdir, a.git))
        if a.kas: print(exporter.export_kas(a.outdir, a.kas))
        return 0
    cat = Catalog()
    from . import plugins as _pl
    _pl.load_all(cat)
    if a.cmd == "new":
        p = P.from_board(cat, a.board, a.name) if a.board else P.from_soc(cat, a.soc, a.name)
        print(P.save(p, a.out)); return 0
    if a.cmd == "search":
        for r_ in cat.search(a.query, a.type.split(",")):
            print(f"{r_['type']:7} {r_['id']:22} {r_['name']}  [{r_['confidence']}{', user' if r_['user'] else ''}]")
        return 0
    if a.cmd == "db":
        sv = SyncService(cat, on_status=lambda s: print(f"  {s['state']}: {s['message']}"))
        if a.action == "status":
            print(json.dumps({"counts": cat.counts(), "size_bytes": cat.size_bytes(), "last_checked": cat.get_meta("last_checked"), "version": cat.get_meta("seed_version")}, indent=2))
        elif a.action in ("check", "update"):
            print(json.dumps(sv.check_updates(apply=a.action == "update"), indent=2))
        elif a.action == "links":
            from .sync import check_links
            for r_ in check_links(cat): print(f"{r_['state']:8} {r_['entity']}/{r_['id']} {r_['url']} {r_['detail']}")
        else:
            f = sv.find_more_boards(); print(f"{len(f)} new boards")
        return 0
    p = P.load(a.project)
    gr = CapabilityGraph(cat)
    if a.cmd == "validate":
        res = gr.validate(p); _print_issues(res)
        print("OK: can generate" if res["can_generate"] else f"BLOCKED: {res['blocking']} blocking issue(s)")
        return 0 if res["can_generate"] else 2
    if a.cmd == "generate":
        if a.waive_all:
            for i in gr.validate(p)["issues"]:
                if i["severity"] == "error" and i["waivable"]:
                    gr.waive(p, i["id"])
        out = a.out or str(Path(a.project).with_suffix("")) + "-bsp"
        job = Job(cat, p, out, build=a.build, policy=a.policy, fetch=a.fetch, offline=a.offline,
                  on_event=lambda e: print(f"[{e['stage']}] {e['level']}: {e['msg']}", flush=True) if e.get("type") == "log" and e["level"] in ("warning", "error") else None)
        snap = job.run()
        for s_ in snap["stages"]:
            print(f"  {s_['label']:28} {s_['state']:8} {s_['message']}")
        if snap["state"] != "done":
            print(f"FAILED at {snap['failure']['stage']}: {snap['failure']['message']}", file=sys.stderr); return 1
        P.save(p, a.project)  # keep newly resolved pins in the project file
        print(f"Done: {out}  ({len(snap['artefacts'])} artefacts)")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
