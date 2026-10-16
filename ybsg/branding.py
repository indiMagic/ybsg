"""Single branding source for splash, About page, PDF cover and footer (UI-13, UI-15, UI-17)."""
from __future__ import annotations

import json
from functools import lru_cache

from . import paths


@lru_cache(maxsize=1)
def brand() -> dict:
    return json.loads((paths.resource_dir() / "branding.json").read_text(encoding="utf-8"))


def about_text() -> str:
    b = brand()
    return (f"{b['product']} {b['version']} (build {b['build_number']}, {b['build_date']})\n"
            f"Tool designer: {b['designer']}\nCompany: {b['company']}\nWebsite: {b['website']}\n{b['rights']}")
