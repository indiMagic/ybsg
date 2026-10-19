"""Self-update (PKG-7): signed release feed, hash-checked download, atomic replace of the portable executable.

The feed is JSON at `update_url` in branding.json (or --feed / YBSG_UPDATE_URL):
    {"version": "1.0.1", "date": "2026-11-02", "notes": "...",
     "assets": {"linux-x86_64": {"url": "...", "sha256": "..."}, "windows-x64": {...}},
     "signature": "<Ed25519 hex over the canonical JSON of everything except signature>"}
It must be signed by a key in data/signing.json (tools/make_update_feed.py). Nothing is installed unless the
signature and the SHA-256 both match. A pip or source install is never modified: it is told to use pip.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import sys
import tempfile
import urllib.request
from pathlib import Path

from . import __version__, signing
from .branding import brand

UA = f"YBSG/{__version__} (update check)"


def _vt(v: str) -> tuple:
    return tuple(int(x) if x.isdigit() else x for x in v.replace("-", ".").split("."))


def canonical(feed: dict) -> bytes:
    return json.dumps({k: v for k, v in feed.items() if k != "signature"}, sort_keys=True, separators=(",", ":")).encode()


def platform_key() -> str:
    m = platform.machine().lower()
    arch = "x86_64" if m in ("x86_64", "amd64") else m
    if sys.platform.startswith("win"):
        return "windows-x64" if arch == "x86_64" else f"windows-{arch}"
    return f"{'linux' if sys.platform.startswith('linux') else sys.platform}-{arch}"


def feed_url(override: str | None = None) -> str:
    return override or os.environ.get("YBSG_UPDATE_URL") or brand().get("update_url", "")


def _get(url: str, timeout: float = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # http(s) and file:// (local mirrors, tests)
        return r.read()


def check(url: str | None = None) -> dict:
    """{state: up_to_date | available | not_configured | failed, ...}. Never installs anything."""
    url = feed_url(url)
    if not url:
        return {"state": "not_configured", "current": __version__, "message": "No update feed configured (branding.json update_url)."}
    try:
        feed = json.loads(_get(url).decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        return {"state": "failed", "current": __version__, "message": f"Could not read the update feed: {e}"}
    sig = bytes.fromhex(feed.get("signature", "") or "00")
    if not any(signing.verify(k, canonical(feed), sig) for k in signing.trusted_keys()):
        return {"state": "failed", "current": __version__, "message": "Update feed signature is not valid; ignoring it."}
    latest = str(feed.get("version", ""))
    asset = feed.get("assets", {}).get(platform_key())
    if not latest or _vt(latest) <= _vt(__version__):
        return {"state": "up_to_date", "current": __version__, "latest": latest, "message": f"YBSG {__version__} is up to date."}
    return {"state": "available", "current": __version__, "latest": latest, "notes": feed.get("notes", ""), "date": feed.get("date", ""),
            "asset": asset, "installable": bool(asset) and is_frozen(),
            "message": f"YBSG {latest} is available." + ("" if asset else f" No build for {platform_key()} in this release.")}


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def apply(info: dict, target: str | Path | None = None) -> dict:
    """Download the asset from a `check()` result, verify its SHA-256 and replace `target` (default: this executable)."""
    if info.get("state") != "available" or not info.get("asset"):
        raise ValueError("no installable update")
    if target is None:
        if not is_frozen():
            return {"state": "manual", "message": f"Installed from source or pip: run `pip install -U ybsg` to get {info['latest']}."}
        # an AppImage runs from a read-only mount; the file to replace is the .AppImage itself
        target = os.environ.get("APPIMAGE") or sys.executable
    target = Path(target)
    if not os.access(target.parent, os.W_OK):
        return {"state": "manual", "message": f"YBSG is installed system-wide in {target.parent}; update it with your package manager "
                                              f"(for example the {info['latest']} .deb) or run the portable build."}
    data = _get(info["asset"]["url"], timeout=600)
    got = hashlib.sha256(data).hexdigest()
    if got != info["asset"]["sha256"].lower():
        raise ValueError(f"download SHA-256 {got} does not match the signed feed; nothing was changed")
    fd, tmp = tempfile.mkstemp(prefix=".ybsg-update-", dir=str(target.parent))
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    shutil.copymode(target, tmp) if target.exists() else os.chmod(tmp, 0o755)
    if sys.platform.startswith("win"):
        # a running .exe cannot be overwritten but can be renamed: move it aside, put the new one in its place
        old = target.with_suffix(".old.exe")
        old.unlink(missing_ok=True)
        if target.exists():
            target.rename(old)
        os.replace(tmp, target)
    else:
        os.replace(tmp, target)  # atomic; the running process keeps its open inode
    return {"state": "installed", "version": info["latest"], "path": str(target), "message": f"Installed YBSG {info['latest']}. Restart YBSG to use it."}
