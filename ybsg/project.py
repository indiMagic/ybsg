"""Project model and the single-file .ybsg format (FR-13): a zip holding project.json plus assets."""
from __future__ import annotations

import copy
import io
import json
import time
import uuid
import zipfile
from pathlib import Path

from . import __version__, paths

FORMAT_VERSION = 1
IMAGE_TYPES = {
    "minimal": "Yocto minimal",
    "graphics": "Yocto with graphics (Wayland/Weston)",
    "qt": "Yocto with Qt",
    "kernel-only": "Kernel + bootloader only",
    "distro": "Distribution rootfs",
}
DISTROS = ["ubuntu", "debian", "kali", "fedora", "archarm", "alpine"]

# UI category -> controller type in the capability graph
CATEGORY_CONTROLLER = {
    "usb_host": "usb_host", "usb_device": "usb_host", "camera": "csi", "display": "display",
    "mmc": "mmc", "flash": "flash", "ram": "ram", "net": "net", "audio": "audio", "i2c": "i2c",
}
CATEGORY_LABEL = {
    "usb_host": "USB host", "usb_device": "USB device (gadget)", "camera": "Camera", "display": "Display and HDMI",
    "mmc": "SD / SDIO / eMMC", "flash": "External flash", "ram": "RAM", "net": "Networking",
    "audio": "Audio", "i2c": "Low-speed buses and sensors",
}
# IC category stored in the catalog for each UI category
IC_CATEGORY = {"usb_device": "usb_host"}


def new_project(name: str = "untitled") -> dict:
    t = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return {
        "format": FORMAT_VERSION, "tool_version": __version__, "uid": uuid.uuid4().hex[:12], "name": name,
        "mode": "board", "board": None, "soc": None, "release": "wrynose", "kernel": "6.18",
        "image": "minimal", "distro": None, "overlay_mode": "overlay",
        "ram": {"type": "", "size_mb": 0, "bus_width": 32, "ranks": 1, "speed": ""},
        "peripherals": [], "custom_ics": [], "custom_usb": [], "waivers": [], "author": "", "created": t, "modified": t,
        "pins": {},
    }


def add_peripheral(p: dict, category: str, ic: str = "", driver: str = "", *, mode: str = "", props: dict | None = None,
                   driver_version: str = "", custom: dict | None = None) -> dict:
    item = {"uid": uuid.uuid4().hex[:8], "category": category, "ic": ic, "driver": driver,
            "driver_version": driver_version, "mode": mode, "props": props or {}, "custom": custom or {}}
    p["peripherals"].append(item)
    touch(p)
    return item


def remove_peripheral(p: dict, uid: str) -> None:
    p["peripherals"] = [x for x in p["peripherals"] if x["uid"] != uid]
    touch(p)


def touch(p: dict) -> None:
    p["modified"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def from_board(cat, board_id: str, name: str | None = None) -> dict:
    """FR-4: selecting a known board pre-fills every peripheral from the database."""
    b = cat.get("board", board_id)
    if not b:
        raise KeyError(f"unknown board {board_id}")
    p = new_project(name or b["name"])
    p["mode"] = "board"
    p["board"], p["soc"] = b["id"], b["soc"]
    p["ram"] = {**p["ram"], **b.get("ram", {})}
    for pre in b.get("preset", []):
        add_peripheral(p, pre["category"], pre.get("ic", ""), pre.get("driver", ""), mode=pre.get("mode", ""))
    soc = cat.get("soc", b["soc"]) or {}
    if soc.get("kernels") and p["kernel"] not in soc["kernels"]:
        p["kernel"] = soc["kernels"][-1] if soc["kernels"][-1] != "vendor" else soc["kernels"][0]
    # keep the board's RAM peripheral in the RAM section, not as a peripheral row
    touch(p)
    return p


def from_soc(cat, soc_id: str, name: str | None = None) -> dict:
    s = cat.get("soc", soc_id)
    if not s:
        raise KeyError(f"unknown SoC {soc_id}")
    p = new_project(name or f"{s['name']} custom board")
    p["mode"] = "custom"
    p["soc"] = soc_id
    if p["kernel"] not in s.get("kernels", []):
        p["kernel"] = next((k for k in s["kernels"] if k != "vendor"), "6.6")
    return p


# ---- file format ---------------------------------------------------------------------------
def dumps(p: dict) -> str:
    return json.dumps(p, indent=2, sort_keys=True)


def save(p: dict, path: str | Path, assets: dict[str, bytes] | None = None) -> Path:
    path = Path(path)
    if path.suffix != ".ybsg":
        path = path.with_suffix(".ybsg")
    touch(p)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".ybsg.tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("project.json", dumps(p))
        for name, blob in (assets or {}).items():
            z.writestr(f"assets/{name}", blob)
    tmp.replace(path)  # atomic: a crash never leaves a half-written project (NFR-5)
    return path


def load(path: str | Path) -> dict:
    with zipfile.ZipFile(path) as z:
        p = json.loads(z.read("project.json"))
    if p.get("format", 0) > FORMAT_VERSION:
        raise ValueError("project was created by a newer YBSG")
    base = new_project(p.get("name", "imported"))
    base.update(p)
    return base


def duplicate(p: dict, name: str | None = None) -> dict:
    q = copy.deepcopy(p)
    q["uid"] = uuid.uuid4().hex[:12]
    q["name"] = name or p["name"] + " (copy)"
    touch(q)
    return q


def autosave(p: dict) -> Path:
    """NFR-5: autosave every 30 s from the GUI; a crash never loses a project."""
    return save(p, paths.projects_dir() / f"{p['uid']}.ybsg")


def list_saved() -> list[dict]:
    out = []
    for f in sorted(paths.projects_dir().glob("*.ybsg"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            p = load(f)
            out.append({"file": f.name, "uid": p["uid"], "name": p["name"], "soc": p.get("soc"), "board": p.get("board"),
                        "modified": p["modified"]})
        except Exception:
            continue
    return out


def load_saved(uid: str) -> dict:
    return load(paths.projects_dir() / f"{uid}.ybsg")
