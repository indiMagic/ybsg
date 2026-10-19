"""Local application server: JSON API + static GUI. Binds to 127.0.0.1 only; mutating calls need a per-run token."""
from __future__ import annotations

import json
import mimetypes
import secrets
import shutil
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import __version__, paths, project as P
from .boot import Boot, log_path
from .branding import brand
from .catalog import Catalog, driver_binds
from .generator import plan, STAGES
from .graph import CapabilityGraph
from .orchestrator import Job, history, resource_check, find_build_backend
from .sync import SyncService, ensure_image
from . import report as R

WEB = paths.resource_dir() / "web"


class App:
    def __init__(self, boot: Boot):
        self.boot = boot
        self.token = secrets.token_urlsafe(24)
        self.project: dict | None = None
        self.job: Job | None = None
        self.outdirs: dict[str, str] = {}
        self._cat: Catalog | None = None
        self._sync: SyncService | None = None
        self._dirty = False
        threading.Thread(target=self._autosave_loop, daemon=True).start()

    @property
    def cat(self) -> Catalog:
        return self.boot.cat  # type: ignore[return-value]

    @property
    def sync(self) -> SyncService:
        if not self._sync:
            self._sync = SyncService(self.cat)
        return self._sync

    @property
    def pictures(self):
        if not getattr(self, "_pictures", None):
            from .pictures import PictureQueue
            self._pictures = PictureQueue(self.cat)
        return self._pictures

    @property
    def graph(self) -> CapabilityGraph:
        return CapabilityGraph(self.cat)

    def _autosave_loop(self) -> None:  # NFR-5: autosave every 30 s
        while True:
            time.sleep(30)
            try:
                if self.project and self._dirty:
                    P.autosave(self.project)
                    self._dirty = False
            except Exception:
                pass


def jbytes(o) -> bytes:
    return json.dumps(o, default=str).encode()


class Handler(BaseHTTPRequestHandler):
    app: App
    server_version = "YBSG"

    def log_message(self, *a) -> None:
        pass

    # ---- plumbing ------------------------------------------------------------------------------
    def _send(self, code: int, body: bytes, ctype: str = "application/json", extra: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, o, code: int = 200) -> None:
        self._send(code, jbytes(o))

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        raw = self.rfile.read(n)
        try:
            return json.loads(raw)
        except Exception:
            return {"_raw": raw}

    def _host_ok(self) -> bool:
        h = (self.headers.get("Host") or "").split(":")[0]
        return h in ("127.0.0.1", "localhost")

    # ---- GET ---------------------------------------------------------------------------------------
    def do_GET(self) -> None:
        if not self._host_ok():
            return self._send(403, b"forbidden", "text/plain")
        u = urllib.parse.urlsplit(self.path)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        path = u.path
        try:
            if path == "/" or path == "/index.html":
                html = (WEB / "index.html").read_text(encoding="utf-8").replace("__YBSG_TOKEN__", self.app.token)
                return self._send(200, html.encode(), "text/html; charset=utf-8")
            if path.startswith("/api/"):
                return self._api_get(path[5:], q)
            f = (WEB / path.lstrip("/")).resolve()
            if WEB.resolve() in f.parents and f.is_file():
                return self._send(200, f.read_bytes(), mimetypes.guess_type(str(f))[0] or "application/octet-stream")
            self._send(404, b"not found", "text/plain")
        except BrokenPipeError:
            pass
        except Exception as e:
            self._json({"error": str(e)}, 500)

    def _api_get(self, ep: str, q: dict) -> None:
        a = self.app
        if ep == "boot":
            return self._json(a.boot.status())
        if ep == "log":
            try:
                return self._send(200, log_path().read_bytes()[-20000:], "text/plain; charset=utf-8")
            except Exception:
                return self._send(200, b"(no log yet)", "text/plain")
        if not a.boot.ready:
            return self._json({"error": "starting"}, 503)
        if ep == "projects":
            return self._json(P.list_saved())
        if ep == "brand":
            return self._json({**brand(), "about_db": {"version": a.cat.get_meta("seed_version"), "last_sync": a.cat.get_meta("last_sync")}})
        if ep == "summary":
            c = a.cat
            return self._json({"counts": c.counts(), "size": c.size_bytes(), "last_sync": c.get_meta("last_sync"), "last_checked": c.get_meta("last_checked"),
                               "version": c.get_meta("seed_version"), "backend": a.boot.backend, "resources": resource_check(), "saved": P.list_saved()[:8]})
        if ep == "search":
            types = q.get("type", "soc,board,ic,driver").split(",")
            return self._json(a.cat.search(q.get("q", ""), types, int(q.get("limit", 30)), q.get("vendor", ""), q.get("arch", ""), int(q.get("min_cores", 0) or 0),
                                           q.get("price_band", ""), q.get("availability", "")))
        if ep.startswith("list/"):
            ent = ep.split("/", 1)[1]
            rows = a.cat.list(ent)
            if ent == "board":
                for r in rows:
                    r["soc_name"] = (a.cat.get("soc", r["soc"]) or {}).get("name", r["soc"])
            return self._json(rows)
        if ep.startswith("get/"):
            _, ent, rid = ep.split("/", 2)
            rec = a.cat.get(ent, urllib.parse.unquote(rid))
            if not rec:
                return self._json({"error": "not found"}, 404)
            if ent == "soc":
                rec["boards"] = [{"id": b["id"], "name": b["name"]} for b in a.cat.board_for_soc(rec["id"])]
            if ent == "board":
                rec["soc_rec"] = a.cat.get("soc", rec["soc"])
                img = a.cat.get_image(rec["id"])
                rec["image"] = {k: img[k] for k in ("source_url", "licence", "attribution", "shareable", "fetched")} if img and img["source_url"] != "ybsg:generated" else None
            if ent == "ic":
                rec["drivers"] = a.cat.drivers_for_ic(rec["id"])
            return self._json(rec)
        if ep.startswith("image/"):
            b = a.cat.get("board", ep.split("/", 1)[1])
            if not b:
                return self._send(404, b"", "text/plain")
            img = ensure_image(a.cat, b, a.pictures)
            return self._send(200, img["blob"], img["mime"])
        if ep == "project":
            return self._json({"project": a.project, "validation": a.graph.validate(a.project) if a.project else None})
        if ep == "validate":
            return self._json(a.graph.validate(a.project) if a.project else {"issues": [], "sections": {}, "can_generate": False})
        if ep == "offer/ics":
            return self._json(a.graph.offer_ics(a.project, q["category"]) if a.project else [])
        if ep == "offer/drivers":
            return self._json(a.graph.offer_drivers(a.project, q["ic"]) if a.project else [])
        if ep == "releases":
            return self._json({"releases": a.cat.list("release"), "kernels": a.cat.list("kernel"), "images": P.IMAGE_TYPES, "distros": P.DISTROS})
        if ep == "categories":
            return self._json({"labels": P.CATEGORY_LABEL, "controllers": P.CATEGORY_CONTROLLER, "ic_category": P.IC_CATEGORY})
        if ep == "compat":
            return self._json(self._compat() if a.project else [])
        if ep == "review":
            return self._json(plan(a.cat, a.project) if a.project else {"ok": False, "error": "No project", "files": []})
        if ep == "job":
            return self._json(a.job.snapshot() if a.job else {"state": "idle", "stages": [{"id": s, "label": l, "state": "pending", "message": ""} for s, l in STAGES], "percent": 0, "log": [], "banner": "Idle"})
        if ep == "history":
            return self._json(history(a.project["uid"]) if a.project else [])
        if ep == "db/status":
            return self._json({"status": a.sync.status, "log": a.cat.recent_log(20), "discovered": a.sync.discovered(), "size": a.cat.size_bytes(), "last_checked": a.cat.get_meta("last_checked"), "signature": a.cat.get_meta("seed_signature")})
        if ep == "settings":
            from .orchestrator import settings, find_build_backend
            return self._json({**settings(), "local_backend": find_build_backend()})
        if ep == "update/check":
            from . import update
            return self._json(update.check())
        if ep == "report_templates":
            from .report import list_templates, template_dir
            return self._json({"templates": list_templates(), "folder": str(template_dir())})
        if ep == "reports":
            out = []
            for uid, d in a.outdirs.items():
                for f in Path(d).glob("report/*"):
                    out.append({"name": f.name, "path": str(f), "dir": d})
            return self._json(out)
        if ep == "file":
            f = Path(q["path"]).resolve()
            ok = any(Path(d).resolve() in f.parents for d in a.outdirs.values())
            if not ok or not f.is_file():
                return self._json({"error": "not available"}, 403)
            ct = mimetypes.guess_type(str(f))[0] or "application/octet-stream"
            return self._send(200, f.read_bytes(), ct, {"Content-Disposition": f'inline; filename="{f.name}"'})
        if ep == "export_out":
            from . import exporter
            import tempfile
            d = a.outdirs.get(a.project["uid"]) if a.project else None
            if not d or not Path(d).exists():
                return self._json({"error": "Generate the project first"}, 400)
            kind = q.get("kind", "zip")
            tmp = Path(tempfile.mkdtemp())
            try:
                if kind == "kas":
                    f = exporter.export_kas(d, tmp / "project.kas.yml"); ct = "text/yaml"
                else:
                    f = exporter.export_zip(d, tmp / "bsp.zip"); ct = "application/zip"
                return self._send(200, f.read_bytes(), ct, {"Content-Disposition": f'attachment; filename="{f.name}"'})
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
        if ep == "export":
            import tempfile
            tmp = Path(tempfile.mkdtemp()) / f"{a.project['slug'] if 'slug' in a.project else 'project'}.ybsg"
            P.save(a.project, tmp)
            data = tmp.read_bytes()
            shutil.rmtree(tmp.parent, ignore_errors=True)
            return self._send(200, data, "application/zip", {"Content-Disposition": 'attachment; filename="project.ybsg"'})
        self._json({"error": "unknown endpoint"}, 404)

    def _compat(self) -> list[dict]:
        """Software-page checks (screen 3): kernel vs SoC, drivers vs kernel, vendor layer vs release, licence gates."""
        a = self.app
        p, soc = a.project, a.cat.get("soc", a.project["soc"]) if a.project.get("soc") else None
        out = []
        if not soc:
            return [{"label": "SoC selected", "state": "fail", "detail": "Pick a board or SoC first"}]
        out.append({"label": "Kernel supported by the SoC", "state": "ok" if p["kernel"] in soc["kernels"] else "fail",
                    "detail": f"{soc['name']} supports: {', '.join(soc['kernels'])}"})
        bad = [i for i in a.graph.validate(p)["issues"] if i["code"] == "driver-kernel-mismatch"]
        out.append({"label": "Drivers exist in this kernel", "state": "fail" if bad else "ok", "detail": bad[0]["message"] if bad else "Every selected driver is in range"})
        from .discover import load_layers
        data = load_layers()
        rows, mism = [], []
        for l in soc.get("layers", []):
            ent = data["layer_catalog"].get(l)
            repo = (ent["repo"] if isinstance(ent["repo"], str) else ent["repo"].get(p["release"], ent["repo"]["default"])) if ent else None
            br = data["repos"].get(repo, {}).get("branches", {}).get(p["release"]) if repo else None
            rows.append(f"{l}: {br or 'no branch for this release'}")
            if not br:
                mism.append(l)
        out.append({"label": "Vendor layer matches release", "state": "warn" if mism else "ok", "detail": "; ".join(rows) or "No vendor layer required"})
        gate = soc.get("licence_gate", "none")
        needs = bool(gate) and not gate.lower().startswith("none")
        out.append({"label": "Licence acceptance needed", "state": "warn" if needs else "ok", "detail": (gate + ". You accept it on the vendor site; YBSG never does.") if needs else "None"})
        return out

    # ---- POST ----------------------------------------------------------------------------------------
    def do_POST(self) -> None:
        if not self._host_ok() or self.headers.get("X-YBSG-Token") != self.app.token:
            return self._send(403, b'{"error":"forbidden"}')
        u = urllib.parse.urlsplit(self.path)
        try:
            self._api_post(u.path[5:], self._body())
        except KeyError as e:
            self._json({"error": f"missing or unknown: {e}"}, 400)
        except Exception as e:
            self._json({"error": str(e)}, 500)

    def _api_post(self, ep: str, b: dict) -> None:
        a = self.app
        c = a.cat
        if ep == "project/new":
            if b.get("board"):
                a.project = P.from_board(c, b["board"], b.get("name"))
            elif b.get("soc"):
                a.project = P.from_soc(c, b["soc"], b.get("name"))
            else:
                a.project = P.new_project(b.get("name", "untitled"))
            a._dirty = True
            P.autosave(a.project)
            return self._json({"project": a.project, "validation": a.graph.validate(a.project)})
        if ep == "project/open":
            a.project = P.normalize_ids(c, P.load_saved(b["uid"]))
            return self._json({"project": a.project, "validation": a.graph.validate(a.project)})
        if ep == "project/import":
            import tempfile
            import base64
            tmp = Path(tempfile.mkdtemp()) / "in.ybsg"
            tmp.write_bytes(base64.b64decode(b["data"]))
            a.project = P.normalize_ids(c, P.load(tmp))
            shutil.rmtree(tmp.parent, ignore_errors=True)
            P.autosave(a.project)
            return self._json({"project": a.project, "validation": a.graph.validate(a.project)})
        if ep == "project/duplicate":
            a.project = P.duplicate(a.project)
            P.autosave(a.project)
            return self._json({"project": a.project, "validation": a.graph.validate(a.project)})
        if not a.project and ep.startswith("project/"):
            return self._json({"error": "No project open"}, 400)
        if ep == "project/set":
            for k in ("name", "kernel", "release", "image", "distro", "author", "overlay_mode", "report_template"):
                if k in b:
                    a.project[k] = b[k]
            if "ram" in b:
                a.project["ram"].update(b["ram"])
            if "soc" in b:
                a.project["soc"] = b["soc"]; a.project["mode"] = "custom"; a.project["board"] = None
            P.touch(a.project); a._dirty = True
        elif ep == "project/add":
            P.add_peripheral(a.project, b["category"], b.get("ic", ""), b.get("driver", ""), mode=b.get("mode", ""), props=b.get("props"), driver_version=b.get("driver_version", ""), custom=b.get("custom"))
            a._dirty = True
        elif ep == "project/update_peripheral":
            for x in a.project["peripherals"]:
                if x["uid"] == b["uid"]:
                    for k in ("ic", "driver", "mode", "driver_version"):
                        if k in b: x[k] = b[k]
                    if "props" in b: x["props"].update(b["props"])
            P.touch(a.project); a._dirty = True
        elif ep == "project/remove":
            P.remove_peripheral(a.project, b["uid"]); a._dirty = True
        elif ep == "project/waive":
            a.graph.waive(a.project, b["id"]); a._dirty = True
        elif ep == "project/unwaive":
            a.project["waivers"] = [w for w in a.project["waivers"] if w != b["id"]]
        elif ep == "project/custom_ic":
            ci = {k: b.get(k, "") for k in ("id", "compatible", "bus", "address", "interrupts", "gpios", "clocks")}
            ci["regulators"] = [r for r in str(b.get("regulators", "")).replace(",", " ").split() if r]
            ci["kconfig"] = [k for k in str(b.get("kconfig", "")).replace(",", " ").split() if k]
            if not ci["id"] or not ci["compatible"]:
                return self._json({"error": "A custom IC needs an id and a compatible string"}, 400)
            a.project["custom_ics"] = [x for x in a.project["custom_ics"] if x["id"] != ci["id"]] + [ci]
            P.touch(a.project); a._dirty = True
        elif ep == "project/custom_usb":
            cu = {"name": b["name"], "class": b.get("class", "ff"), "subclass": b.get("subclass", "00"), "protocol": b.get("protocol", "00")}
            a.project["custom_usb"] = [x for x in a.project["custom_usb"] if x["name"] != cu["name"]] + [cu]
            P.touch(a.project); a._dirty = True
        elif ep == "project/save":
            f = P.save(a.project, b["path"]) if b.get("path") else P.autosave(a.project)
            return self._json({"saved": str(f)})
        elif ep == "export_git":
            from . import exporter
            d = a.outdirs.get(a.project["uid"]) if a.project else None
            if not d or not Path(d).exists():
                return self._json({"error": "Generate the project first"}, 400)
            dest = exporter.export_git(d, Path(b["dest"]).expanduser())
            return self._json({"repo": str(dest)})
        elif ep == "settings":
            from .orchestrator import save_settings
            rb = str(b.get("remote_builder", "")).strip()
            if rb and (rb.startswith("-") or any(c in rb for c in " ;|&`$'\"\n")):
                return self._json({"error": "Remote builder must look like user@host:/dir"}, 400)
            return self._json(save_settings(remote_builder=rb))
        elif ep == "update/apply":
            from . import update
            return self._json(update.apply(update.check()))
        elif ep == "project/autosave":
            P.autosave(a.project); a._dirty = False
            return self._json({"ok": True})
        elif ep == "generate":
            out = b.get("outdir") or str(paths.data_dir() / "output" / a.project["uid"])
            a.outdirs[a.project["uid"]] = out
            a.job = Job(c, json.loads(json.dumps(a.project)), out, build=bool(b.get("build")), policy=b.get("policy", "merge"), fetch=bool(b.get("fetch")), offline=bool(b.get("offline", False)), notify_done=bool(b.get("notify")))
            a.job.start()
            return self._json({"started": True, "outdir": out})
        elif ep in ("job/pause", "job/resume", "job/cancel", "job/retry"):
            if not a.job:
                return self._json({"error": "no job"}, 400)
            if ep == "job/retry":
                st = (a.job.failure or {}).get("retry_from", "validate")
                a.job.project.update(json.loads(json.dumps(a.project)))
                a.job.start(st)
            else:
                getattr(a.job, ep.split("/")[1])()
            return self._json({"ok": True})
        elif ep == "db/check":
            a.sync.run_async(a.sync.check_updates, b.get("ids") or None, b.get("branch", "wrynose"), bool(b.get("apply")))
            return self._json({"started": True})
        elif ep == "db/find":
            a.sync.run_async(a.sync.find_boards_online, "", b.get("branch", "wrynose"))
            return self._json({"started": True})
        elif ep == "image/find":
            from .pictures import fetch_for
            board = c.get("board", b["id"])
            if not board:
                return self._json({"error": "unknown board"}, 404)
            return self._json({"result": fetch_for(c, board, force=True)})
        elif ep == "image/upload":
            import base64
            board = c.get("board", b["id"])
            data = base64.b64decode(b["data"])
            if not board or len(data) > 5 * 1024 * 1024 or data[:4] not in (b"\x89PNG", b"\xff\xd8\xff\xe0", b"\xff\xd8\xff\xe1", b"\xff\xd8\xff\xdb", b"RIFF"):
                return self._json({"error": "Use a PNG, JPEG or WebP picture of at most 5 MB"}, 400)
            mime = "image/png" if data[:4] == b"\x89PNG" else "image/webp" if data[:4] == b"RIFF" else "image/jpeg"
            c.set_image(board["id"], mime, data, source_url="user", licence="your own picture", attribution="you", shareable=False)
            return self._json({"result": "saved"})
        elif ep == "board/from_url":
            from .fromurl import LinkError, add_from_url
            try:
                return self._json(add_from_url(c, str(b.get("url", "")), b.get("branch", "wrynose")))
            except (LinkError, OSError, RuntimeError) as e:
                return self._json({"error": str(e)}, 400)
        elif ep == "search/online":
            res = a.sync.find_boards_online(str(b.get("q", ""))[:100], b.get("branch", "wrynose"))
            return self._json(res)
        elif ep == "db/download":
            a.sync.run_async(a.sync.download_discovered, b["ids"])
            return self._json({"started": True})
        elif ep in ("db/pause", "db/resume", "db/cancel"):
            getattr(a.sync, ep.split("/")[1])()
            return self._json({"ok": True})
        elif ep == "db/remove":
            c.delete("board", b["id"])
            return self._json({"ok": True, "size": c.size_bytes()})
        elif ep == "db/user_record":
            rec = b["record"]
            ent = b["entity"]
            if ent not in ("board", "soc", "ic", "driver") or not rec.get("id"):
                return self._json({"error": "entity must be board, soc, ic or driver and the record needs an id"}, 400)
            return self._json({"result": c.add_user_record(ent, rec)})
        elif ep == "db/resolve_dispute":
            c.db.execute("UPDATE record SET disputed=0, dispute_note=NULL WHERE entity=? AND id=?", (b["entity"], b["id"])); c.db.commit()
            return self._json({"ok": True})
        elif ep == "open_url":
            url = b.get("url", "")
            if url.startswith("https://" + brand()["website"]):
                webbrowser.open(url)
            return self._json({"ok": True})
        else:
            return self._json({"error": "unknown endpoint"}, 404)
        self._json({"project": a.project, "validation": a.graph.validate(a.project)})


def make_server(boot: Boot, port: int = 0) -> tuple[ThreadingHTTPServer, App]:
    app = App(boot)
    Handler.app = app
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    return srv, app
