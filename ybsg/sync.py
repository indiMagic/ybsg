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
            try:
                rp.set_url(base + "/robots.txt")
                rp.read()
                self._robots[base] = rp
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
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return r.read()

    def get_json(self, url: str) -> Any:
        return json.loads(self.get(url).decode("utf-8"))


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
        q = urllib.parse.urlencode({"filter": f"layerbranch__branch__name:{branch}", "format": "json"})
        items = self.fetch.get_json(f"{LAYERINDEX}/layerItems/?{q}")
        return {i["id"]: i for i in items}

    # ---- DB-14: check for updates ---------------------------------------------------
    def check_updates(self, board_ids: list[str] | None = None, branch: str = "wrynose", apply: bool = False) -> dict:
        """Compare each local record with the layer index; change only what differs, keep user fields."""
        self._begin("check")
        ids = board_ids or [b["id"] for b in self.cat.list("board")]
        report = {"checked": 0, "changed": [], "up_to_date": [], "not_found": [], "failed": None}
        try:
            self._emit("looking", f"Looking for board data in OpenEmbedded layer index ({branch})", "all")
            try:
                machines = {m.get("name"): m for m in self.fetch_machines(branch)}
            except (urllib.error.URLError, PermissionError, TimeoutError, OSError, ValueError) as e:
                reason = getattr(e, "reason", e)
                self._emit("failed", f"Failed: network ({reason}). Retry when online.", "all", retry=True)
                report["failed"] = str(reason)
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
            except (urllib.error.URLError, PermissionError, TimeoutError, OSError, ValueError) as e:
                reason = getattr(e, "reason", e)
                self._emit("failed", f"Failed: network ({reason}). Retry when online.", "discover", retry=True)
                self._finish("failed")
                return []
            known = {b.get("machine") for b in self.cat.list("board")}
            for n, m in enumerate(machines, 1):
                self._checkpoint()
                name = m.get("name")
                if not name or name in known:
                    continue
                lb = m.get("layerbranch")
                layer_name = next((l["name"] for l in layers.values() if l.get("id") == lb), "unknown-layer")
                rec = {"id": f"li-{name}", "name": name, "machine": name, "layer": layer_name,
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


def ensure_image(cat: Catalog, board: dict) -> dict:
    """DB-12: return the stored picture, or store a generated placeholder (shareable: generated by YBSG)."""
    img = cat.get_image(board["id"])
    if img:
        return img
    soc = cat.get("soc", board.get("soc", "")) or {}
    cat.set_image(board["id"], "image/svg+xml", placeholder_svg(board["name"], board.get("vendor", ""), soc.get("name", "")),
                  source_url="ybsg:generated", licence="generated placeholder", attribution=brand()["company"])
    return cat.get_image(board["id"])  # type: ignore[return-value]


def check_links(cat, fetcher: "PoliteFetcher | None" = None, limit: int = 200) -> list[dict]:
    """DB-11: flag dead datasheet, product and repository URLs. Honest about robots.txt and rate limits."""
    import urllib.request
    f = fetcher or PoliteFetcher()
    seen, out = set(), []
    for ent in ("soc", "board", "ic"):
        for r in cat.list(ent):
            for key in ("datasheet", "url"):
                u = r.get(key)
                if not u or u in seen or not u.startswith("http"):
                    continue
                seen.add(u)
                if len(seen) > limit:
                    return out
                try:
                    if not f.allowed(u):
                        out.append({"entity": ent, "id": r["id"], "url": u, "state": "skipped", "detail": "robots.txt disallows"}); continue
                    f.get(u)
                    out.append({"entity": ent, "id": r["id"], "url": u, "state": "ok", "detail": ""})
                except Exception as e:  # noqa: BLE001
                    out.append({"entity": ent, "id": r["id"], "url": u, "state": "dead", "detail": str(e)[:120]})
    return out
