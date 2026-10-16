"""Start-up sequence shared by the splash screen (native and web) and the app (UI-13, UI-14)."""
from __future__ import annotations

import logging
import threading
import time
import traceback

from . import paths
from .catalog import Catalog
from .orchestrator import find_build_backend

STEPS = ["Opening local database", "Loading board catalog", "Checking build backend", "Preparing workspace"]


def log_path():
    return paths.data_dir() / "ybsg.log"


def setup_logging() -> None:
    logging.basicConfig(filename=str(log_path()), level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


class Boot:
    """Runs the start-up steps on a thread and exposes live status. Never delays the UI: failures show an error instead."""

    def __init__(self) -> None:
        self.steps = [{"name": s, "state": "pending"} for s in STEPS]
        self.ready = False
        self.error: str | None = None
        self.cat: Catalog | None = None
        self.backend: list[str] | None = None
        self._t = threading.Thread(target=self._run, daemon=True)

    def start(self) -> "Boot":
        setup_logging()
        self._t.start()
        return self

    def _step(self, i: int, fn) -> None:
        self.steps[i]["state"] = "running"
        fn()
        self.steps[i]["state"] = "done"

    def _run(self) -> None:
        try:
            def db() -> None:
                self.cat = Catalog()
                from . import plugins as _pl
                self.plugins = _pl.load_all(self.cat)
            self._step(0, db)
            self._step(1, lambda: self.cat.counts())  # type: ignore[union-attr]
            def be() -> None:
                self.backend = find_build_backend()
            self._step(2, be)
            self._step(3, lambda: paths.projects_dir())
            self.ready = True
            logging.info("startup complete")
        except Exception as e:
            self.error = f"YBSG could not start: {e}. The local database may be damaged or the disk is full."
            logging.error("startup failed\n%s", traceback.format_exc())
            for s in self.steps:
                if s["state"] == "running":
                    s["state"] = "failed"

    def status(self) -> dict:
        return {"steps": self.steps, "ready": self.ready, "error": self.error}
