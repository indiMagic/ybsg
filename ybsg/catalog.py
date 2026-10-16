"""Catalog database (SQLite): SoCs, controllers, ICs, drivers, boards, releases, kernels, rules.

Every record keeps provenance (source URL, fetch date, content hash) and a confidence
level (DB-5, DB-8). User records are flagged and never overwritten by sync (DB-7).
"""
from __future__ import annotations

import difflib
import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable

from . import paths

ENTITIES = ("soc", "ic", "driver", "board", "release", "kernel", "rule")
SEED_FILE = Path(__file__).parent / "data" / "seed.json"

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS record (
    entity TEXT NOT NULL, id TEXT NOT NULL, name TEXT, vendor TEXT, data TEXT NOT NULL,
    source_url TEXT, fetched TEXT, hash TEXT, confidence TEXT DEFAULT 'medium',
    user INTEGER DEFAULT 0, disputed INTEGER DEFAULT 0, dispute_note TEXT,
    PRIMARY KEY (entity, id)
);
CREATE INDEX IF NOT EXISTS ix_record_entity ON record(entity);
CREATE TABLE IF NOT EXISTS board_image (
    board_id TEXT PRIMARY KEY, mime TEXT, blob BLOB, source_url TEXT, licence TEXT,
    attribution TEXT, shareable INTEGER DEFAULT 0, fetched TEXT, hash TEXT
);
CREATE TABLE IF NOT EXISTS sync_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, scope TEXT, state TEXT, message TEXT
);
CREATE TABLE IF NOT EXISTS sync_state (run_id TEXT PRIMARY KEY, kind TEXT, cursor TEXT, status TEXT, updated TEXT);
CREATE TABLE IF NOT EXISTS discovered (
    id TEXT PRIMARY KEY, name TEXT, source TEXT, source_url TEXT, payload TEXT, ts TEXT, picked INTEGER DEFAULT 0
);
"""

CONF_RANK = {"high": 3, "medium": 2, "unverified": 1}


def _compact(s: str) -> str:
    return "".join(ch for ch in s.lower() if ch.isalnum())


def driver_binds(drv: dict, ic: dict) -> bool:
    """A driver binds to one IC, a list of ICs, or every IC of a category (host-controller drivers)."""
    b = drv.get("ic")
    if isinstance(b, list):
        return ic.get("id") in b
    if b:
        return ic.get("id") == b
    return bool(drv.get("ic_category")) and drv.get("ic_category") == ic.get("category")


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def content_hash(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class _LockedConn:
    """One SQLite connection is shared by the HTTP request threads and the sync thread. Every statement runs under
    one lock and reads are fetched eagerly, so a cursor is never used while another thread writes."""

    def __init__(self, conn, lock):
        self._c, self._l = conn, lock

    def execute(self, sql, params=()):
        with self._l:
            cur = self._c.execute(sql, params)
            rows = cur.fetchall() if cur.description else []
            return _Rows(rows, cur)

    def executemany(self, sql, seq):
        with self._l:
            return self._c.executemany(sql, seq)

    def executescript(self, sql):
        with self._l:
            return self._c.executescript(sql)

    def commit(self):
        with self._l:
            self._c.commit()

    def close(self):
        with self._l:
            self._c.close()


class _Rows:
    def __init__(self, rows, cur):
        self._rows, self._i = rows, 0
        self.rowcount, self.lastrowid = cur.rowcount, cur.lastrowid

    def fetchone(self):
        if self._i < len(self._rows):
            self._i += 1
            return self._rows[self._i - 1]
        return None

    def fetchall(self):
        r = self._rows[self._i:]; self._i = len(self._rows); return r

    def __iter__(self):
        return iter(self.fetchall())


class Catalog:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else paths.data_dir() / "catalog.sqlite"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        raw = sqlite3.connect(str(self.path), check_same_thread=False)
        raw.row_factory = sqlite3.Row
        self.db = _LockedConn(raw, self._lock)
        self.db.executescript(SCHEMA)
        if not self.get_meta("seed_version"):
            self.load_seed()

    # ---- meta -------------------------------------------------------------
    def get_meta(self, k: str, default: str | None = None) -> str | None:
        r = self.db.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        return r["v"] if r else default

    def set_meta(self, k: str, v: str) -> None:
        with self._lock:
            self.db.execute("INSERT INTO meta(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))
            self.db.commit()

    # ---- seed -------------------------------------------------------------
    def load_seed(self, seed_path: Path = SEED_FILE) -> int:
        seed = json.loads(seed_path.read_text(encoding="utf-8"))
        n = 0
        stamp = seed["meta"]["built"] + "T00:00:00Z"
        mapping = {"releases": "release", "kernels": "kernel", "socs": "soc", "boards": "board",
                   "ics": "ic", "drivers": "driver", "rules": "rule"}
        for key, ent in mapping.items():
            for rec in seed.get(key, []):
                url = rec.get("datasheet") or rec.get("url") or "ybsg:seed"
                self.upsert(ent, rec, source_url=url or "ybsg:seed", fetched=stamp,
                            confidence="medium", from_sync=True)
                n += 1
        self.set_meta("seed_version", seed["meta"]["version"])
        self.set_meta("seed_built", seed["meta"]["built"])
        self.set_meta("last_sync", stamp)
        return n

    def snapshot_hash(self) -> str:
        """Content hash of all non-user records, used to verify a shipped snapshot (DB-10)."""
        h = hashlib.sha256()
        for r in self.db.execute("SELECT entity,id,hash FROM record WHERE user=0 ORDER BY entity,id"):
            h.update(f"{r['entity']}:{r['id']}:{r['hash']}\n".encode())
        return h.hexdigest()

    # ---- CRUD -------------------------------------------------------------
    def upsert(self, entity: str, rec: dict, *, source_url: str = "", fetched: str | None = None,
               confidence: str = "medium", user: bool = False, from_sync: bool = False) -> str:
        """Insert/update. Sync never overwrites a user record (DB-7); it only changes differing fields."""
        assert entity in ENTITIES, entity
        rid = rec["id"]
        with self._lock:
            cur = self.db.execute("SELECT user, data, confidence FROM record WHERE entity=? AND id=?", (entity, rid)).fetchone()
            if cur and cur["user"] and not user:  # DB-7: only the user can change a user record
                return "skipped-user-record"
            h = content_hash(rec)
            if cur and not from_sync and not user:
                pass
            if cur and from_sync:
                old = json.loads(cur["data"])
                if content_hash(old) == h:
                    self.db.execute("UPDATE record SET fetched=? WHERE entity=? AND id=?", (fetched or now(), entity, rid))
                    self.db.commit()
                    return "unchanged"
            self.db.execute(
                """INSERT INTO record(entity,id,name,vendor,data,source_url,fetched,hash,confidence,user)
                   VALUES(?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(entity,id) DO UPDATE SET name=excluded.name, vendor=excluded.vendor, data=excluded.data,
                     source_url=excluded.source_url, fetched=excluded.fetched, hash=excluded.hash,
                     confidence=excluded.confidence, user=excluded.user""",
                (entity, rid, rec.get("name") or rec.get("part") or rid, rec.get("vendor", ""),
                 json.dumps(rec), source_url, fetched or now(), h,
                 "unverified" if user else confidence, 1 if user else 0))
            self.db.commit()
            return "updated" if cur else "inserted"

    def add_user_record(self, entity: str, rec: dict) -> str:
        """DB-7: user-entered record, marked and unverified."""
        return self.upsert(entity, rec, source_url="user", user=True)

    def delete(self, entity: str, rid: str) -> None:
        with self._lock:
            self.db.execute("DELETE FROM record WHERE entity=? AND id=?", (entity, rid))
            if entity == "board":
                self.db.execute("DELETE FROM board_image WHERE board_id=?", (rid,))
            self.db.commit()

    @staticmethod
    def _row(r: sqlite3.Row) -> dict:
        d = json.loads(r["data"])
        d["_prov"] = {"source_url": r["source_url"], "fetched": r["fetched"], "hash": r["hash"],
                      "confidence": r["confidence"], "user": bool(r["user"]), "disputed": bool(r["disputed"]),
                      "dispute_note": r["dispute_note"]}
        return d

    def get(self, entity: str, rid: str) -> dict | None:
        r = self.db.execute("SELECT * FROM record WHERE entity=? AND id=?", (entity, rid)).fetchone()
        return self._row(r) if r else None

    def list(self, entity: str, **filters: str) -> list[dict]:
        rows = [self._row(r) for r in self.db.execute("SELECT * FROM record WHERE entity=? ORDER BY name", (entity,))]
        for k, v in filters.items():
            if v in (None, "", "all"):
                continue
            rows = [r for r in rows if str(r.get(k, "")).lower() == str(v).lower()]
        return rows

    def counts(self) -> dict:
        out = {e: self.db.execute("SELECT COUNT(*) c FROM record WHERE entity=?", (e,)).fetchone()["c"] for e in ENTITIES}
        out["user_records"] = self.db.execute("SELECT COUNT(*) c FROM record WHERE user=1").fetchone()["c"]
        out["images"] = self.db.execute("SELECT COUNT(*) c FROM board_image").fetchone()["c"]
        return out

    def size_bytes(self) -> int:
        return self.path.stat().st_size if self.path.exists() else 0

    # ---- queries used by the UI and the capability graph --------------------
    def ics_for(self, category: str) -> list[dict]:
        return [i for i in self.list("ic") if i.get("category") == category]

    def drivers_for_ic(self, ic_id: str) -> list[dict]:
        ic = self.get("ic", ic_id) or {"id": ic_id}
        return [d for d in self.list("driver") if driver_binds(d, ic)]

    def board_for_soc(self, soc_id: str) -> list[dict]:
        return [b for b in self.list("board") if b.get("soc") == soc_id]

    # ---- search (FR-2): type-ahead, filters, fuzzy -----------------------------
    def search(self, q: str, types: Iterable[str] = ("soc", "board", "ic", "driver"), limit: int = 30,
               vendor: str = "", arch: str = "", min_cores: int = 0, price_band: str = "",
               availability: str = "") -> list[dict]:
        q = (q or "").strip().lower()
        results: list[tuple[float, dict]] = []
        for ent in types:
            for r in self.db.execute("SELECT * FROM record WHERE entity=?", (ent,)):
                d = json.loads(r["data"])
                if vendor and vendor.lower() not in (r["vendor"] or "").lower():
                    continue
                if ent == "soc":
                    if arch and d.get("arch") != arch:
                        continue
                    if min_cores and d.get("cores", 0) < min_cores:
                        continue
                    if price_band and d.get("price_band") != price_band:
                        continue
                    if availability and availability.lower() not in str(d.get("availability", "")).lower():
                        continue
                hay = f"{r['name']} {r['vendor']} {r['id']}".lower()
                if not q:
                    score = 0.1
                elif hay.startswith(q) or f" {q}" in hay:
                    score = 1.0
                elif q in hay:
                    score = 0.8
                elif _compact(q) and _compact(q) in _compact(hay):
                    score = 0.75
                else:
                    best = max([difflib.SequenceMatcher(None, q, t).ratio() for t in hay.replace("-", " ").split()] or [0])
                    score = best if best >= 0.6 else 0
                if score:
                    results.append((score, {"type": ent, "id": r["id"], "name": r["name"], "vendor": r["vendor"],
                                            "confidence": r["confidence"], "user": bool(r["user"])}))
        results.sort(key=lambda t: (-t[0], t[1]["name"]))
        return [r for _, r in results[:limit]]

    # ---- data-quality rule (6.3) ---------------------------------------------
    def mark_disputed(self, entity: str, rid: str, note: str) -> None:
        with self._lock:
            self.db.execute("UPDATE record SET disputed=1, dispute_note=? WHERE entity=? AND id=?", (note, entity, rid))
            self.db.commit()

    def resolve_value(self, board: dict, field: str, sources: dict[str, Any]) -> tuple[Any, bool]:
        """If sources disagree, flag disputed and prefer the device-tree value for generation."""
        vals = {k: v for k, v in sources.items() if v is not None}
        if len({json.dumps(v, sort_keys=True) for v in vals.values()}) > 1:
            self.mark_disputed("board", board["id"], f"{field}: " + "; ".join(f"{k}={v}" for k, v in vals.items()))
            return vals.get("devicetree", next(iter(vals.values()))), True
        return next(iter(vals.values())) if vals else None, False

    # ---- images (DB-12, DB-18) -------------------------------------------------
    def set_image(self, board_id: str, mime: str, blob: bytes, source_url: str, licence: str, attribution: str = "") -> None:
        shareable = 1 if licence and licence.lower() not in ("", "unknown", "unclear") else 0
        with self._lock:
            self.db.execute(
                """INSERT INTO board_image(board_id,mime,blob,source_url,licence,attribution,shareable,fetched,hash)
                   VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(board_id) DO UPDATE SET mime=excluded.mime, blob=excluded.blob,
                   source_url=excluded.source_url, licence=excluded.licence, attribution=excluded.attribution,
                   shareable=excluded.shareable, fetched=excluded.fetched, hash=excluded.hash""",
                (board_id, mime, blob, source_url, licence, attribution, shareable, now(), hashlib.sha256(blob).hexdigest()))
            self.db.commit()

    def get_image(self, board_id: str) -> dict | None:
        r = self.db.execute("SELECT * FROM board_image WHERE board_id=?", (board_id,)).fetchone()
        return dict(r) if r else None

    # ---- sync log ----------------------------------------------------------------
    def log(self, scope: str, state: str, message: str) -> None:
        with self._lock:
            self.db.execute("INSERT INTO sync_log(ts,scope,state,message) VALUES(?,?,?,?)", (now(), scope, state, message))
            self.db.commit()

    def recent_log(self, n: int = 50) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT ts,scope,state,message FROM sync_log ORDER BY id DESC LIMIT ?", (n,))][::-1]

    def close(self) -> None:
        self.db.close()
