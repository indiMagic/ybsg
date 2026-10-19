"""Sync service: layer-index ingestion, update checks, board discovery (DB-5, DB-9, DB-14 to DB-18).

Status states reported to the UI (DB-15): looking, found, downloading, verifying, saved,
up_to_date, not_found, failed, new_boards. Runs can be paused, cancelled and resumed (DB-17);
each step commits atomically so a cancelled run leaves the database consistent.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
import uuid
from typing import Any, Callable

from .catalog import Catalog, now
from .branding import brand

LAYERINDEX = "https://layers.openembedded.org/layerindex/api"
UA = "YBSG/1.0 (+https://codewavelabs.ca; catalog sync)"

STATES = ("looking", "found", "downloading", "verifying", "saved", "up_to_date", "not_found", "failed", "new_boards")


class BlockedError(PermissionError):
    """The site answered with a bot challenge (e.g. Cloudflare) instead of data. Not a network outage."""


def _reason(e: Exception) -> str:
    if isinstance(e, BlockedError):
        return str(e)
    return f"network ({getattr(e, 'reason', e)}). Retry when online"


class PoliteFetcher:
    """HTTP GET with robots.txt, rate limiting and a timeout (DB-9). Injectable for tests."""

    def __init__(self, min_interval: float = 1.0, timeout: float = 20.0):
        self.min_interval = min_interval
        self.timeout = timeout
        self._last = 0.0
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}

    def allowed(self, url: str) -> bool:
        parts = urllib.parse.urlsplit(url)
        if "/api/" in parts.path:  # official API endpoints are the sanctioned route
            return True
        base = f"{parts.scheme}://{parts.netloc}"
        if base not in self._robots:
            rp = urllib.robotparser.RobotFileParser()
            try:  # RobotFileParser.read() has no timeout; one slow host must not stall a whole check
                req = urllib.request.Request(base + "/robots.txt", headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=min(self.timeout, 10)) as r:
                    rp.parse(r.read().decode("utf-8", errors="replace").splitlines())
                self._robots[base] = rp
            except urllib.error.HTTPError as e:
                rp.parse([]) if e.code in (404, 410) else rp.parse(["User-agent: *", "Disallow: /"]) if e.code in (401, 403) else None
                self._robots[base] = rp if e.code in (401, 403, 404, 410) else None
            except Exception:
                self._robots[base] = None
        rp = self._robots[base]
        return True if rp is None else rp.can_fetch(UA, url)

    def get(self, url: str) -> bytes:
        if not self.allowed(url):
            raise PermissionError(f"robots.txt disallows {url}")
        wait = self.min_interval - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.time()
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json, */*"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (403, 429, 503) and (e.headers.get("cf-mitigated") or "challenge" in (e.headers.get("server", "") + str(e.headers.get("cf-mitigated", "")))):
                host = urllib.parse.urlsplit(url).netloc
                raise BlockedError(f"{host} answered with a bot challenge (HTTP {e.code}) for the YBSG user agent. "
                                   "This is the site's access policy, not a network problem; YBSG does not disguise itself. "
                                   "Ask the site operators to allow it, or import the data manually") from e
            raise

    def get_json(self, url: str) -> Any:
        return json.loads(self.get(url).decode("utf-8"))


class FileFetcher:
    """Reads saved layer-index API responses instead of the network: machines.json, layerItems.json (or layers.json)
    and layerBranches.json in one folder, each saved from the matching /layerindex/api/<endpoint>/?format=json URL."""

    def __init__(self, folder: str):
        from pathlib import Path
        self.folder = Path(folder)

    def get_json(self, url: str) -> Any:
        ep = urllib.parse.urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]
        for name in (ep, {"layerItems": "layers"}.get(ep, ep)):
            f = self.folder / f"{name}.json"
            if f.exists():
                return json.loads(f.read_text(encoding="utf-8"))
        raise FileNotFoundError(f"{self.folder}/{ep}.json is missing (save it from {url})")

    def allowed(self, url: str) -> bool:
        return True


class Cancelled(Exception):
    pass


class SyncService:
    def __init__(self, catalog: Catalog, fetcher: PoliteFetcher | None = None,
                 on_status: Callable[[dict], None] | None = None):
        self.cat = catalog
        self.fetch = fetcher or PoliteFetcher()
        self.on_status = on_status or (lambda s: None)
        self._pause = threading.Event()
        self._pause.set()  # set = running
        self._cancel = threading.Event()
        self.status: dict = {"state": "idle", "message": "Idle", "scope": "", "percent": None}
        self.run_id: str | None = None

    # ---- status plumbing ---------------------------------------------------------
    def _emit(self, state: str, message: str, scope: str = "", percent: float | None = None, **extra: Any) -> None:
        self.status = {"state": state, "message": message, "scope": scope, "percent": percent, "ts": now(), **extra}
        self.cat.log(scope or "sync", state, message)
        self.on_status(self.status)

    def _checkpoint(self) -> None:
        if self._cancel.is_set():
            raise Cancelled()
        self._pause.wait()
        if self._cancel.is_set():
            raise Cancelled()

    def pause(self) -> None:
        self._pause.clear()
        self._emit("looking", "Paused", self.status.get("scope", ""))

    def resume(self) -> None:
        self._pause.set()

    def cancel(self) -> None:
        self._cancel.set()
        self._pause.set()

    def _begin(self, kind: str) -> str:
        self._cancel.clear()
        self._pause.set()
        self.run_id = uuid.uuid4().hex[:12]
        self.cat.db.execute("INSERT INTO sync_state(run_id,kind,cursor,status,updated) VALUES(?,?,?,?,?)",
                            (self.run_id, kind, "0", "running", now()))
        self.cat.db.commit()
        return self.run_id

    def _finish(self, status: str) -> None:
        self.cat.db.execute("UPDATE sync_state SET status=?, updated=? WHERE run_id=?", (status, now(), self.run_id))
        self.cat.db.commit()

    # ---- layer-index client -------------------------------------------------------
    def fetch_machines(self, branch: str) -> list[dict]:
        """Machines per Yocto branch from the OpenEmbedded layer index REST API."""
        q = urllib.parse.urlencode({"filter": f"layerbranch__branch__name:{branch}", "format": "json"})
        return self.fetch.get_json(f"{LAYERINDEX}/machines/?{q}")

    def fetch_layers(self, branch: str) -> dict[int, dict]:
        """layerbranch id -> layer item. A machine's `layerbranch` is a layerBranches id, not a layer id."""
        q = urllib.parse.urlencode({"filter": f"layerbranch__branch__name:{branch}", "format": "json"})
        items = {i["id"]: i for i in self.fetch.get_json(f"{LAYERINDEX}/layerItems/?{q}")}
        qb = urllib.parse.urlencode({"filter": f"branch__name:{branch}", "format": "json"})
        return {lb["id"]: items[lb["layer"]] for lb in self.fetch.get_json(f"{LAYERINDEX}/layerBranches/?{qb}") if lb.get("layer") in items}

    # ---- DB-14: check for updates ---------------------------------------------------
    def check_updates(self, board_ids: list[str] | None = None, branch: str = "wrynose", apply: bool = False) -> dict:
        """DB-14: compare each board with its machine in the BSP layer repositories (git); change only what differs,
        keep user-edited fields. Saved layer-index files (`ybsg db check --from DIR`) take the layer-index route."""
        if isinstance(self.fetch, FileFetcher):
            return self._check_updates_layerindex(board_ids, branch, apply)
        from . import discover as D
        self._begin("check-git")
        ids = board_ids or [b["id"] for b in self.cat.list("board")]
        report = {"checked": 0, "changed": [], "up_to_date": [], "not_found": [], "new": 0, "failed": None}
        try:
            repos = D.discovery_repos()
            self._emit("looking", f"Reading machines from {len(repos)} BSP layer repositories ({branch})", "all", 0.0)
            n = {"i": 0}

            def log(msg: str) -> None:
                n["i"] += 1
                self._checkpoint()
                self._emit("looking", msg, "all", 60.0 * n["i"] / max(len(repos), 1))

            machines, layer_add, repo_add = D.discover(self.cat.list("soc"), branch, log=log)
            if not machines:
                self._emit("failed", "No BSP repository was reachable. Check the internet connection and retry.", "all", retry=True)
                report["failed"] = "unreachable"
                self._finish("failed")
                return report
            D.save_overlay(D.resolve_needs(layer_add, D.load_layers()["layer_catalog"]), repo_add)
            by_machine: dict[str, list[dict]] = {}
            for m in machines:
                by_machine.setdefault(m["machine"], []).append(m)
            with self.cat.bulk():
                for i, bid in enumerate(ids, 1):
                    self._checkpoint()
                    b = self.cat.get("board", bid)
                    if not b or not b.get("machine"):
                        continue
                    pct = 60.0 + 39.0 * i / len(ids)
                    report["checked"] += 1
                    cands = by_machine.get(b["machine"], [])
                    m = next((c for c in cands if c["layer"] in b.get("layers", [])), cands[0] if cands else None)
                    if not m:
                        self._emit("not_found", f"Board not found in the {branch} layers: {b['name']} (machine {b['machine']})", bid, pct, retry=True)
                        report["not_found"].append(bid)
                        continue
                    changes = {}
                    if m["description"] and m["description"] != b.get("machine_description", b.get("description")):
                        changes["machine_description"] = m["description"]
                    if b.get("source") == "layer git repository":  # records that came from discovery follow their layer
                        if m["name"] != b["name"]:
                            changes["name"] = m["name"]
                        if m["soc"] and m["soc"] != b.get("soc"):
                            changes["soc"] = m["soc"]
                        if [m["layer"]] != b.get("layers"):
                            changes["layers"] = [m["layer"]]
                    changes = {k: v for k, v in changes.items() if k not in set(b.get("_user_fields", []))}
                    if not changes:
                        report["up_to_date"].append(bid)
                        continue
                    report["changed"].append({"board": bid, "fields": sorted(changes)})
                    if apply:
                        new = {k: v for k, v in b.items() if k != "_prov"}
                        new.update(changes)
                        self.cat.upsert("board", new, source_url=b["_prov"]["source_url"], from_sync=True, confidence=b["_prov"]["confidence"])
                        self._emit("saved", f"Saved to local database: {b['name']} ({', '.join(sorted(changes))})", bid, pct)
                    else:
                        self._emit("found", f"Update available for {b['name']}: {', '.join(sorted(changes))}", bid, pct)
                if apply:
                    known = {x.get("machine") for x in self.cat.list("board")}
                    recs = D.board_records(machines, repo_add, known)
                    for r in recs:
                        self.cat.upsert("board", r, source_url=r["url"] or "layer git repository", confidence="medium", from_sync=True)
                    report["new"] = len(recs)
            self.cat.set_meta("last_checked", now())
            if apply:
                self.cat.set_meta("last_sync", now())
            ch, up, nf = len(report["changed"]), len(report["up_to_date"]), len(report["not_found"])
            msg = (f"{'Updated' if apply else 'Updates available for'} {ch} board(s); {up} up to date; {nf} not in the {branch} layers"
                   + (f"; {report['new']} new board(s) added" if apply else ""))
            self._emit("saved" if apply else ("found" if ch else "up_to_date"), msg, "all", 100.0)
            self._finish("done")
            return report
        except Cancelled:
            self._emit("failed", "Cancelled. Database left consistent.", "all")
            self._finish("cancelled")
            return report

    def _check_updates_layerindex(self, board_ids: list[str] | None = None, branch: str = "wrynose", apply: bool = False) -> dict:
        """Compare each local record with saved layer-index responses; change only what differs, keep user fields."""
        self._begin("check")
        ids = board_ids or [b["id"] for b in self.cat.list("board")]
        report = {"checked": 0, "changed": [], "up_to_date": [], "not_found": [], "failed": None}
        try:
            self._emit("looking", f"Looking for board data in OpenEmbedded layer index ({branch})", "all")
            try:
                machines = {m.get("name"): m for m in self.fetch_machines(branch)}
            except (urllib.error.URLError, PermissionError, TimeoutError, OSError, ValueError) as e:  # FileNotFoundError is an OSError
                self._emit("failed", f"Failed: {_reason(e)}.", "all", retry=not isinstance(e, BlockedError))
                report["failed"] = _reason(e)
                self._finish("failed")
                return report
            for n, bid in enumerate(ids, 1):
                self._checkpoint()
                b = self.cat.get("board", bid)
                if not b:
                    continue
                pct = 100.0 * n / len(ids)
                self._emit("looking", f"Looking for board {b['name']}", bid, pct)
                m = machines.get(b.get("machine"))
                report["checked"] += 1
                if not m:
                    self._emit("not_found", f"Board not found in layer index: {b['name']} (source: layers.openembedded.org)", bid, pct, retry=True)
                    report["not_found"].append(bid)
                    continue
                self._emit("found", f"Found board {b['name']}", bid, pct)
                self._emit("verifying", "Checking hash and licence", bid, pct)
                new = dict(b)
                new.pop("_prov", None)
                layer = m.get("layerbranch")
                changes = {}
                if m.get("description") and m["description"] != b.get("machine_description"):
                    changes["machine_description"] = m["description"]
                if layer is not None and b.get("layerbranch_id") != layer:
                    changes["layerbranch_id"] = layer
                protected = set(b.get("_user_fields", []))
                changes = {k: v for k, v in changes.items() if k not in protected}
                if changes and apply:
                    self._emit("downloading", f"Downloading {b['name']}: specs", bid, pct)
                    new.update(changes)
                    self.cat.upsert("board", new, source_url=f"{LAYERINDEX}/machines/", from_sync=True,
                                    confidence=b["_prov"]["confidence"])
                    self._emit("saved", f"Saved to local database: {b['name']}", bid, pct)
                    report["changed"].append({"board": bid, "fields": sorted(changes)})
                elif changes:
                    report["changed"].append({"board": bid, "fields": sorted(changes)})
                    self._emit("found", f"Update available for {b['name']}: {', '.join(sorted(changes))}", bid, pct)
                else:
                    self._emit("up_to_date", f"Already up to date: {b['name']}", bid, pct)
                    report["up_to_date"].append(bid)
            self.cat.set_meta("last_checked", now())
            if apply:
                self.cat.set_meta("last_sync", now())
            self._finish("done")
            return report
        except Cancelled:
            self._emit("failed", "Cancelled. Database left consistent.", "all")
            self._finish("cancelled")
            return report

    # ---- DB-16: find more boards ------------------------------------------------------
    def find_more_boards(self, branch: str = "wrynose") -> list[dict]:
        self._begin("discover")
        found: list[dict] = []
        try:
            self._emit("looking", "Looking for new boards in OpenEmbedded layer index", "discover")
            try:
                machines = self.fetch_machines(branch)
                layers = self.fetch_layers(branch)
            except (urllib.error.URLError, PermissionError, TimeoutError, OSError, ValueError) as e:  # FileNotFoundError is an OSError
                self._emit("failed", f"Failed: {_reason(e)}.", "discover", retry=not isinstance(e, BlockedError))
                self._finish("failed")
                return []
            known = {b.get("machine") for b in self.cat.list("board")}
            names = [m.get("name") for m in machines]
            for n, m in enumerate(machines, 1):
                self._checkpoint()
                name = m.get("name")
                if not name or name in known:
                    continue
                lb = m.get("layerbranch")
                layer_name = layers.get(lb, {}).get("name", "unknown-layer")
                rid = f"li-{name}" if names.count(name) == 1 else f"li-{name}-{layer_name}"  # same machine name in two layers
                rec = {"id": rid, "name": name, "machine": name, "layer": layer_name,
                       "source": "layers.openembedded.org", "description": m.get("description", "")}
                found.append(rec)
                self.cat.db.execute(
                    "INSERT OR IGNORE INTO discovered(id,name,source,source_url,payload,ts) VALUES(?,?,?,?,?,?)",
                    (rec["id"], name, rec["source"], f"{LAYERINDEX}/machines/", json.dumps(rec), now()))
            self.cat.db.commit()
            self._emit("new_boards", f"{len(found)} new boards found", "discover", 100.0, count=len(found))
            self._finish("done")
            return found
        except Cancelled:
            self.cat.db.commit()
            self._emit("failed", "Cancelled. Database left consistent.", "discover")
            self._finish("cancelled")
            return found

    def find_boards_online(self, query: str = "", release: str = "wrynose") -> dict:
        """DB-16 from the internet: read the machines of the BSP layer repositories (git), add boards the catalogue
        does not have, and return the ones matching `query`. Works where the layer index refuses YBSG."""
        from . import discover as D
        self._begin("discover-git")
        try:
            self._emit("looking", "Looking for boards in the BSP layer repositories", "discover", 0.0)
            done = {"n": 0}
            repos = D.discovery_repos()

            def log(msg: str) -> None:
                done["n"] += 1
                self._checkpoint()
                self._emit("found" if "machine" in msg else "failed", msg, "discover", 100.0 * done["n"] / max(len(repos), 1))

            machines, layer_add, repo_add = D.discover(self.cat.list("soc"), release, log=log)
            if not machines:
                self._emit("failed", "No BSP repository was reachable. Check the internet connection and retry.", "discover", retry=True)
                self._finish("failed")
                return {"added": 0, "matches": [], "total": 0}
            D.save_overlay(D.resolve_needs(layer_add, D.load_layers()["layer_catalog"]), repo_add)
            known = {b.get("machine") for b in self.cat.list("board")}
            recs = D.board_records(machines, repo_add, known)
            self._emit("downloading", f"Saving {len(recs)} new boards", "discover", 95.0)
            with self.cat.bulk():
                for r in recs:
                    self.cat.upsert("board", r, source_url=r["url"] or "layer git repository", confidence="medium", from_sync=True)
            self.cat.set_meta("last_discovery", now())
            hits = [b for b in self.cat.list("board") if D.matches(b, query)] if query else []
            msg = f"{len(machines)} machines in {len(repos)} BSP repositories; {len(recs)} new boards added"
            self._emit("new_boards", msg + (f"; {len(hits)} match '{query}'" if query else ""), "discover", 100.0, count=len(recs))
            self._finish("done")
            return {"added": len(recs), "total": len(machines), "matches": [{"id": b["id"], "name": b["name"], "soc": b.get("soc", "")} for b in hits]}
        except Cancelled:
            self._emit("failed", "Cancelled. Database left consistent.", "discover")
            self._finish("cancelled")
            return {"added": 0, "matches": [], "total": 0}

    def discovered(self) -> list[dict]:
        return [{"id": r["id"], "name": r["name"], "source": r["source"]}
                for r in self.cat.db.execute("SELECT id,name,source FROM discovered WHERE picked=0 ORDER BY name")]

    def download_discovered(self, ids: list[str]) -> list[str]:
        """Add the boards the user picked, as 'unverified/medium' records with provenance."""
        saved = []
        for n, did in enumerate(ids, 1):
            self._checkpoint()
            row = self.cat.db.execute("SELECT * FROM discovered WHERE id=?", (did,)).fetchone()
            if not row:
                self._emit("not_found", f"Board not found: {did}", did, retry=True)
                continue
            rec = json.loads(row["payload"])
            pct = 100.0 * n / len(ids)
            self._emit("downloading", f"Downloading {rec['name']}: specs", did, pct)
            board = {"id": rec["id"], "name": rec["name"], "vendor": rec.get("layer", ""), "soc": "",
                     "status": "unknown", "layers": [rec["layer"]], "machine": rec["machine"], "url": "",
                     "image_hint": "", "image_licence": "generated placeholder", "preset": [],
                     "ram": {"type": "", "size_mb": 0, "bus_width": 0}}
            self._emit("verifying", "Checking hash and licence", did, pct)
            self.cat.upsert("board", board, source_url=row["source_url"], confidence="medium", from_sync=True)
            self.cat.db.execute("UPDATE discovered SET picked=1 WHERE id=?", (did,))
            self.cat.db.commit()
            self._emit("saved", f"Saved to local database: {rec['name']}", did, pct)
            saved.append(did)
        return saved

    # ---- scheduled refresh (DB-5) ------------------------------------------------------
    def due(self, days: int = 7) -> bool:
        last = self.cat.get_meta("last_checked")
        if not last:
            return True
        t = time.mktime(time.strptime(last, "%Y-%m-%dT%H:%M:%SZ"))
        return (time.time() - t) > days * 86400

    def run_async(self, fn: Callable, *a: Any, **kw: Any) -> threading.Thread:
        t = threading.Thread(target=fn, args=a, kwargs=kw, daemon=True)
        t.start()
        return t


# ---- board images (DB-12, DB-18) ------------------------------------------------------------
def placeholder_svg(name: str, vendor: str, soc: str) -> bytes:
    h = int(hashlib.sha256(name.encode()).hexdigest()[:6], 16)
    hue = h % 360
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 200" role="img" aria-label="{name}">
<rect width="320" height="200" rx="10" fill="hsl({hue},35%,18%)"/>
<rect x="30" y="34" width="260" height="132" rx="8" fill="hsl({hue},45%,30%)" stroke="hsl({hue},60%,65%)" stroke-width="2"/>
<rect x="125" y="68" width="70" height="64" rx="4" fill="#111" stroke="hsl({hue},60%,70%)"/>
<text x="160" y="105" fill="hsl({hue},60%,80%)" font-family="monospace" font-size="10" text-anchor="middle">{soc or 'SoC'}</text>
<g fill="hsl({hue},60%,70%)"><rect x="40" y="46" width="22" height="10"/><rect x="40" y="144" width="22" height="10"/><rect x="258" y="46" width="22" height="10"/><rect x="258" y="144" width="22" height="10"/></g>
<text x="160" y="186" fill="#fff" font-family="sans-serif" font-size="12" text-anchor="middle">{name[:34]}</text>
</svg>"""
    return svg.encode()


def ensure_image(cat: Catalog, board: dict, pictures=None) -> dict:
    """DB-12: the stored photo, or a generated drawing (never stored, so a real photo can still arrive). With a
    PictureQueue, a board without a photo is queued for a background Wikimedia Commons look-up."""
    img = cat.get_image(board["id"])
    if img and img["source_url"] != "ybsg:generated":
        return img
    if pictures is not None:
        pictures.want(board)
    soc = cat.get("soc", board.get("soc", "")) or {}
    return {"blob": placeholder_svg(board["name"], board.get("vendor", ""), soc.get("name", "")), "mime": "image/svg+xml",
            "source_url": "ybsg:generated", "licence": "generated placeholder", "attribution": brand()["company"], "shareable": 1}


def check_links(cat, fetcher: "PoliteFetcher | None" = None, limit: int = 200, on_result=None, timeout: float = 10.0) -> list[dict]:
    """DB-11: flag dead datasheet, product and repository URLs. robots.txt and a one-request-per-second limit apply
    per host; hosts are checked in parallel. HEAD first, then a 1-byte ranged GET for servers that refuse HEAD."""
    import concurrent.futures as cf
    urls: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for ent in ("soc", "board", "ic"):
        for r in cat.list(ent):
            for key in ("datasheet", "url"):
                u = r.get(key)
                if u and u.startswith("http") and u not in seen and len(seen) < limit:
                    seen.add(u); urls.append((ent, r["id"], u))
    by_host: dict[str, list] = {}
    for item in urls:
        by_host.setdefault(urllib.parse.urlsplit(item[2]).netloc, []).append(item)

    def probe(u: str, f: "PoliteFetcher") -> tuple[str, str]:
        if not f.allowed(u):
            return "skipped", "robots.txt disallows"
        last = ""
        for method, extra in (("HEAD", {}), ("GET", {"Range": "bytes=0-0"})):
            wait = f.min_interval - (time.time() - f._last)
            if wait > 0:
                time.sleep(wait)
            f._last = time.time()
            req = urllib.request.Request(u, method=method, headers={"User-Agent": UA, **extra})
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    return "ok", str(resp.status)
            except urllib.error.HTTPError as e:
                if e.code in (405, 501) and method == "HEAD":
                    continue
                if e.code in (401, 403, 429) or (e.headers.get("cf-mitigated") if e.headers else None):
                    return "blocked", f"HTTP {e.code} (the site refuses automated checks; open it in a browser)"
                last = f"HTTP {e.code}"
                if method == "HEAD":
                    continue
            except Exception as e:  # noqa: BLE001
                last = str(getattr(e, "reason", e))[:120]
                if method == "HEAD":
                    continue
        return ("timeout", last) if "timed out" in last.lower() else ("dead", last)

    def host_worker(items):
        f = PoliteFetcher(min_interval=(fetcher.min_interval if fetcher else 1.0), timeout=timeout)
        rows = []
        for ent, rid, u in items:
            st, detail = probe(u, f)
            row = {"entity": ent, "id": rid, "url": u, "state": st, "detail": detail if st != "ok" else ""}
            rows.append(row)
            if on_result:
                on_result(row)
        return rows

    out: list[dict] = []
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        for rows in ex.map(host_worker, by_host.values()):
            out += rows
    return out
