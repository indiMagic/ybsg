"""NFR-11: plugin API. Drop a folder into <data>/plugins/<name>/ containing

  plugin.json   {"name": "...", "version": "1.0", "data": ["socs.json"]}   data packs (same schema as seed.json lists)
  plugin.py     optional; defines register(api) where api offers:
                  api.add_record(entity, record)           add SoCs, ICs, drivers, boards (stored as unverified, user tier)
                  api.add_report_section(title, fn)        fn(manifest) -> list of text lines appended to the report
                  api.add_kconfig_dep(symbol, [deps])      extend the transitive Kconfig dependency table

Plugins run in-process with the user's rights: only install ones you trust."""
from __future__ import annotations

import importlib.util
import json
import traceback
from pathlib import Path

from . import paths

REPORT_SECTIONS: list[tuple[str, object]] = []
KCONFIG_DEPS: dict[str, list[str]] = {}


class API:
    def __init__(self, cat, name):
        self.cat, self.name = cat, name

    def add_record(self, entity: str, record: dict) -> None:
        self.cat.add_user_record(entity, {**record, "_plugin": self.name})

    def add_report_section(self, title: str, fn) -> None:
        REPORT_SECTIONS.append((title, fn))

    def add_kconfig_dep(self, symbol: str, deps: list[str]) -> None:
        KCONFIG_DEPS[symbol] = list(deps)


def load_all(cat) -> list[dict]:
    loaded = []
    root = paths.data_dir() / "plugins"
    for d in sorted(root.glob("*/")) if root.exists() else []:
        info = {"name": d.name, "ok": True, "error": ""}
        try:
            meta = json.loads((d / "plugin.json").read_text()) if (d / "plugin.json").exists() else {}
            info.update(name=meta.get("name", d.name), version=meta.get("version", ""))
            api = API(cat, info["name"])
            for df in meta.get("data", []):
                for entity, rows in json.loads((d / df).read_text()).items():
                    for r in rows:
                        api.add_record(entity.rstrip("s") if entity.endswith("s") and entity not in ("ics",) else entity.replace("ics", "ic"), r)
            if (d / "plugin.py").exists():
                spec = importlib.util.spec_from_file_location(f"ybsg_plugin_{d.name}", d / "plugin.py")
                mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
                if hasattr(mod, "register"):
                    mod.register(api)
        except Exception:
            info.update(ok=False, error=traceback.format_exc(limit=2))
        loaded.append(info)
    return loaded
