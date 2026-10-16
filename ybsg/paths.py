"""Per-user data locations, frozen-app aware."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def data_dir() -> Path:
    override = os.environ.get("YBSG_HOME")
    if override:
        p = Path(override)
    elif sys.platform.startswith("win"):
        p = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "YBSG"
    else:
        p = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "ybsg"
    p.mkdir(parents=True, exist_ok=True)
    return p


def resource_dir() -> Path:
    """Bundled read-only resources (works in PyInstaller and from source)."""
    base = getattr(sys, "_MEIPASS", None)
    return Path(base) / "ybsg" if base else Path(__file__).parent


def projects_dir() -> Path:
    p = data_dir() / "projects"
    p.mkdir(parents=True, exist_ok=True)
    return p


def cache_dir() -> Path:
    p = data_dir() / "cache"
    p.mkdir(parents=True, exist_ok=True)
    return p
