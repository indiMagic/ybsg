"""Job orchestrator: runs the ten stages, reports progress, supports pause/cancel/resume and retry (UI-P1..P8, GEN-11)."""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable

from . import paths
from .catalog import Catalog
from .generator import STAGES, STAGE_IDS, GenerationError, Generator, StageResult

# Default relative stage weights; replaced by the running average of real durations (UI-P2).
DEFAULT_WEIGHTS = {"validate": 1, "resolve": 2, "fetch": 6, "layer": 2, "dts": 2, "config": 2, "dryrun": 3, "build": 70, "package": 3, "report": 4}
TASK_RE = re.compile(r"Running task (\d+) of (\d+)(?: \((.*?)\))?")
SEVERITY_RE = re.compile(r"\b(ERROR|WARNING|NOTE)\b")


def parse_bitbake_progress(line: str) -> tuple[int, int, str] | None:
    """Parse the knotty 'Running task N of M (recipe:task)' line (UI-P2, UI-P3)."""
    m = TASK_RE.search(line)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), (m.group(3) or "").split("/")[-1]


def resource_check() -> dict:
    """NFR-4: a full BitBake build wants 16 cores, 32 GB RAM and 200 GB disk."""
    cpus = os.cpu_count() or 1
    mem_gb = 0.0
    try:
        if sys.platform.startswith("linux"):
            for l in open("/proc/meminfo"):
                if l.startswith("MemTotal"):
                    mem_gb = int(l.split()[1]) / 1024 / 1024
                    break
        elif sys.platform.startswith("win"):
            import ctypes

            class MS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong), ("ullTotalPhys", ctypes.c_ulonglong)] + [(n, ctypes.c_ulonglong) for n in ("a", "b", "c", "d", "e", "f")]
            ms = MS(); ms.dwLength = ctypes.sizeof(MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms))
            mem_gb = ms.ullTotalPhys / 1024**3
    except Exception:
        pass
    disk_gb = shutil.disk_usage(paths.data_dir()).free / 1024**3
    warn = []
    if cpus < 16: warn.append(f"{cpus} cores (16 recommended)")
    if mem_gb and mem_gb < 32: warn.append(f"{mem_gb:.0f} GB RAM (32 GB recommended)")
    if disk_gb < 200: warn.append(f"{disk_gb:.0f} GB free disk (200 GB recommended)")
    return {"cpus": cpus, "mem_gb": round(mem_gb, 1), "disk_gb": round(disk_gb, 1), "ok": not warn, "warnings": warn}


def settings() -> dict:
    f = paths.data_dir() / "settings.json"
    try:
        return json.loads(f.read_text()) if f.exists() else {}
    except ValueError:
        return {}


def save_settings(**kw) -> dict:
    cur = {**settings(), **kw}
    f = paths.data_dir() / "settings.json"
    tmp = f.with_suffix(".tmp"); tmp.write_text(json.dumps(cur, indent=2)); tmp.replace(f)
    return cur


def remote_builder() -> tuple[str, str] | None:
    """FR-16: 'user@host:/dir' from YBSG_REMOTE_BUILDER or settings.json remote_builder. The host needs kas (or
    kas-container with Docker/Podman); this machine needs ssh with key login and rsync."""
    spec = (os.environ.get("YBSG_REMOTE_BUILDER") or settings().get("remote_builder") or "").strip()
    if not spec:
        return None
    host, _, rdir = spec.partition(":")
    return host, rdir or "ybsg-builds"


def _wsl_run(args: list[str], timeout: float = 30) -> subprocess.CompletedProcess:
    return subprocess.run(["wsl.exe", *args], capture_output=True, timeout=timeout)


def parse_wsl_list(raw: bytes) -> list[dict]:
    """`wsl.exe -l -v` prints UTF-16LE: '  NAME STATE VERSION' then '* Ubuntu Running 2'. Returns [{name, state, version, default}]."""
    text = raw.decode("utf-16-le", errors="ignore") if raw[:2] == b"\xff\xfe" or b"\x00" in raw[:20] else raw.decode(errors="ignore")
    out = []
    for line in text.replace("\x00", "").splitlines()[1:]:
        default = line.lstrip().startswith("*")
        parts = line.replace("*", " ").split()
        if len(parts) >= 3 and parts[-1].isdigit():
            out.append({"name": " ".join(parts[:-2]), "state": parts[-2], "version": int(parts[-1]), "default": default})
    return out


def detect_wsl() -> dict | None:
    """PKG-4: the WSL2 distribution to build in, preferring the default one with kas installed. None when unusable,
    with the reason in `problem` when WSL exists but cannot build."""
    if not sys.platform.startswith("win") or not shutil.which("wsl.exe"):
        return None
    try:
        distros = parse_wsl_list(_wsl_run(["-l", "-v"]).stdout)
    except (OSError, subprocess.SubprocessError) as e:
        return {"problem": f"wsl.exe failed: {e}"}
    v2 = sorted((d for d in distros if d["version"] == 2), key=lambda d: not d["default"])
    if not v2:
        return {"problem": "WSL is installed but no WSL2 distribution exists (run: wsl --install -d Ubuntu-24.04)" if not distros
                else "Only WSL1 distributions found; convert one with: wsl --set-version <name> 2"}
    for d in v2:
        r = _wsl_run(["-d", d["name"], "--", "sh", "-lc", "command -v kas || command -v kas-container; command -v rsync"])
        tools = r.stdout.decode(errors="ignore")
        if "kas" in tools and "rsync" in tools:
            return {"distro": d["name"], "kas": "kas-container" if "kas-container" in tools and "/kas\n" not in tools else "kas"}
    return {"problem": f"No WSL2 distribution has kas and rsync; in {v2[0]['name']} run: sudo apt install rsync && pipx install kas"}


def find_build_backend() -> list[str] | None:
    """kas natively, or kas-container (needs Docker/Podman). On Windows: kas inside a WSL2 distribution (PKG-4)."""
    if shutil.which("kas"):
        return ["kas"]
    if shutil.which("kas-container") and (shutil.which("docker") or shutil.which("podman")):
        return ["kas-container"]
    w = detect_wsl()
    if w and w.get("distro"):
        return ["wsl", w["distro"], w["kas"]]
    if not sys.platform.startswith("win") and shutil.which("git") and shutil.which("bash") and shutil.which("python3"):
        return ["native"]  # plain BitBake from the generated build.sh: no kas, no container
    return None


def component_licences(licdir: Path) -> list[dict]:
    """NFR-14: per-package licence inventory from the newest image license.manifest that BitBake wrote."""
    mans = sorted(licdir.glob("**/license.manifest"), key=lambda f: f.stat().st_mtime) if licdir.exists() else []
    if not mans:
        return []
    rows, cur = [], {}
    for line in mans[-1].read_text(errors="replace").splitlines() + [""]:
        if not line.strip():
            if cur.get("package"):
                rows.append(cur)
            cur = {}
            continue
        k, _, v = line.partition(":")
        key = {"PACKAGE NAME": "package", "PACKAGE VERSION": "version", "RECIPE NAME": "recipe", "LICENSE": "licence"}.get(k.strip())
        if key:
            cur[key] = v.strip()
    return sorted(rows, key=lambda r: r["package"])


def notify(title: str, msg: str) -> None:
    """UI-P6 desktop notification, best effort per OS."""
    try:
        if sys.platform.startswith("linux") and shutil.which("notify-send"):
            subprocess.run(["notify-send", title, msg], timeout=5)
        elif sys.platform == "darwin":
            subprocess.run(["osascript", "-e", f'display notification "{msg}" with title "{title}"'], timeout=5)
        elif sys.platform.startswith("win"):
            ps = ("[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null;"
                  "$t=[Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent(1);"
                  f"$t.GetElementsByTagName('text')[0].AppendChild($t.CreateTextNode('{title}: {msg}')) | Out-Null;"
                  "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('YBSG').Show([Windows.UI.Notifications.ToastNotification]::new($t))")
            subprocess.run(["powershell", "-NoProfile", "-Command", ps], timeout=10)
    except Exception:
        pass


class Cancelled(Exception):
    pass


class Job:
    def __init__(self, cat: Catalog, project: dict, outdir: str | Path, *, build: bool = False, policy: str = "merge",
                 fetch: bool = False, offline: bool = False, notify_done: bool = False,
                 on_event: Callable[[dict], None] | None = None):
        self.cat, self.project, self.outdir = cat, project, Path(outdir)
        self.build, self.notify_done = build, notify_done
        self.on_event = on_event or (lambda e: None)
        self.log_lines: list[dict] = []
        self.state = {s: {"id": s, "label": l, "state": "pending", "message": "", "seconds": 0.0} for s, l in STAGES}
        if not build:
            self.state["build"].update(state="skipped", message="Build not requested")
        self.job_state = "idle"  # idle | running | paused | done | failed | cancelled
        self.failure: dict | None = None
        self.started = 0.0
        self.percent = 0.0
        self._partial = {s: 0.0 for s in STAGE_IDS}
        self.current = {"stage": "", "index": 0, "total": len(STAGES), "task": ""}
        self.weights = self._load_weights()
        self._pause = threading.Event(); self._pause.set()
        self._cancel = threading.Event()
        self._proc: subprocess.Popen | None = None
        self.gen = Generator(cat, project, outdir, policy=policy, offline=offline, fetch=fetch, log=self._glog)
        self.artefacts: list[str] = []
        self.thread: threading.Thread | None = None

    # ---- weights from history ---------------------------------------------------------------
    def _wfile(self) -> Path:
        return paths.data_dir() / "stage_durations.json"

    def _load_weights(self) -> dict:
        w = dict(DEFAULT_WEIGHTS)
        try:
            hist = json.loads(self._wfile().read_text())
            for k, v in hist.items():
                if k in w and v.get("n", 0) >= 1:
                    w[k] = max(0.5, v["avg"])
        except Exception:
            pass
        return w

    def _save_weight(self, stage: str, secs: float) -> None:
        try:
            f = self._wfile()
            hist = json.loads(f.read_text()) if f.exists() else {}
            h = hist.get(stage, {"avg": secs, "n": 0})
            h["avg"] = (h["avg"] * h["n"] + secs) / (h["n"] + 1)
            h["n"] += 1
            hist[stage] = h
            f.write_text(json.dumps(hist))
        except Exception:
            pass

    # ---- plumbing ------------------------------------------------------------------------------
    def _emit(self, **e) -> None:
        self.on_event(e)

    def _glog(self, stage: str, level: str, msg: str) -> None:
        self.add_log(stage, level, msg)

    def add_log(self, stage: str, level: str, msg: str) -> None:
        line = {"t": round(time.time() - self.started, 2) if self.started else 0, "stage": stage, "level": level, "msg": msg}
        self.log_lines.append(line)
        if len(self.log_lines) > 20000:
            del self.log_lines[:5000]
        self._emit(type="log", **line)

    def _total_weight(self) -> float:
        return sum(self.weights[s] for s in STAGE_IDS if self.state[s]["state"] != "skipped")

    def _recompute(self) -> None:
        tot = self._total_weight() or 1.0
        done = 0.0
        for s in STAGE_IDS:
            st = self.state[s]["state"]
            if st in ("done", "warning"):
                done += self.weights[s]
            elif st == "running":
                done += self.weights[s] * min(self._partial[s], 0.999)
        pct = 100.0 * done / tot
        if self.job_state == "done":
            pct = 100.0
        elif pct >= 100.0:
            pct = 99.0  # 100% only when the last stage is done (acceptance 5)
        self.percent = max(self.percent, round(pct, 1))  # never moves backwards

    def snapshot(self) -> dict:
        el = (time.time() - self.started) if self.started else 0.0
        eta = None
        if self.job_state == "running" and self.percent > 3:
            eta = el * (100 - self.percent) / self.percent
        return {"state": self.job_state, "stages": [self.state[s] for s in STAGE_IDS], "percent": self.percent, "current": self.current,
                "elapsed": round(el, 1), "eta": None if eta is None else round(eta), "eta_label": "estimate", "failure": self.failure,
                "banner": self._banner(), "log": self.log_lines[-200:], "changes": self.gen.changes, "artefacts": self.artefacts}

    def _banner(self) -> str:
        c = self.current
        if not c["stage"]:
            return "Idle"
        label = self.state[c["stage"]]["label"]
        if c["task"]:
            return f"{label}: {c['task']}"
        return f"{label}, stage {c['index']} of {c['total']}"

    # ---- control ---------------------------------------------------------------------------------
    def pause(self) -> None:
        if self.job_state == "running":
            self._pause.clear()
            self.job_state = "paused"
            self._signal_proc(pause=True)
            self.add_log(self.current["stage"], "info", "Paused")

    def resume(self) -> None:
        if self.job_state == "paused":
            self.job_state = "running"
            self._signal_proc(pause=False)
            self._pause.set()
            self.add_log(self.current["stage"], "info", "Resumed")

    def cancel(self) -> None:
        self._cancel.set()
        self._pause.set()
        if self._proc and self._proc.poll() is None:
            self._signal_proc(pause=False)
            self._proc.terminate()  # the shared sstate/download caches are content-addressed and stay intact

    def _signal_proc(self, pause: bool) -> None:
        p = self._proc
        if not p or p.poll() is not None:
            return
        try:
            if sys.platform.startswith("win"):
                import ctypes
                h = ctypes.windll.kernel32.OpenProcess(0x0800, False, p.pid)
                (ctypes.windll.ntdll.NtSuspendProcess if pause else ctypes.windll.ntdll.NtResumeProcess)(h)
                ctypes.windll.kernel32.CloseHandle(h)
            else:
                os.killpg(os.getpgid(p.pid), signal.SIGSTOP if pause else signal.SIGCONT)
        except Exception as e:
            self.add_log("build", "warning", f"pause/resume not applied: {e}")

    def _check(self) -> None:
        if self._cancel.is_set():
            raise Cancelled()
        self._pause.wait()
        if self._cancel.is_set():
            raise Cancelled()

    # ---- running -----------------------------------------------------------------------------------
    def start(self, start_from: str | None = None) -> threading.Thread:
        t = threading.Thread(target=self.run, args=(start_from,), daemon=True)
        t.start()
        self.thread = t
        return t

    def run(self, start_from: str | None = None) -> dict:
        """Run all stages, or retry from a failed stage (GEN-11)."""
        self._cancel.clear(); self._pause.set()
        self.failure = None
        if start_from is None:
            for s in STAGE_IDS:
                if self.state[s]["state"] != "skipped":
                    self.state[s].update(state="pending", message="")
            self.percent = 0.0
        else:
            idx = STAGE_IDS.index(start_from)
            for s in STAGE_IDS[idx:]:
                if self.state[s]["state"] != "skipped":
                    self.state[s].update(state="pending", message="")
            if start_from == "validate":
                self.percent = 0.0
        self.job_state = "running"
        self.started = time.time() - (self.snapshot()["elapsed"] if start_from else 0)
        self.outdir.mkdir(parents=True, exist_ok=True)
        run_started = time.time()
        first = STAGE_IDS.index(start_from) if start_from else 0
        try:
            for i, sid in enumerate(STAGE_IDS):
                if i < first or self.state[sid]["state"] == "skipped":
                    continue
                self._check()
                self.current = {"stage": sid, "index": i + 1, "total": len(STAGES), "task": ""}
                st = self.state[sid]
                st["state"] = "running"
                self._emit(type="stage", **st)
                self._recompute()
                t0 = time.time()
                try:
                    res = self._run_stage(sid)
                except GenerationError as e:
                    excerpt = "\n".join(l["msg"] for l in self.log_lines if l["stage"] == sid)[-1500:] or e.excerpt
                    self.failure = {"stage": sid, "message": str(e), "uid": e.uid, "excerpt": excerpt, "retry_from": sid}
                    st.update(state="failed", message=str(e), seconds=time.time() - t0)
                    self.add_log(sid, "error", f"Stage failed: {e}")
                    raise
                st.update(state=res.state, message=res.message, seconds=round(time.time() - t0, 2))
                if res.state != "skipped":
                    self._save_weight(sid, time.time() - t0)
                else:
                    self._recompute()
                self._partial[sid] = 1.0
                self._emit(type="stage", **st)
                self._recompute()
            self.job_state = "done"
            self._recompute()
            self.current = {"stage": "report", "index": len(STAGES), "total": len(STAGES), "task": ""}
            outcome = "done"
        except Cancelled:
            self.job_state = "cancelled"
            outcome = "cancelled"
            self.add_log(self.current["stage"], "warning", "Cancelled. Generated files are kept; caches are intact.")
        except GenerationError:
            self.job_state = "failed"
            outcome = "failed"
        except Exception as e:  # unexpected: show it, never crash the GUI
            sid = self.current["stage"] or "validate"
            self.state[sid].update(state="failed", message=f"Unexpected error: {e}")
            self.failure = {"stage": sid, "message": f"Unexpected error: {e}", "uid": "", "excerpt": repr(e), "retry_from": sid}
            self.add_log(sid, "error", f"Unexpected error: {e}")
            self.job_state = "failed"
            outcome = "failed"
        self._record_history(run_started, outcome)
        self._emit(type="done", state=self.job_state)
        if self.notify_done:
            notify("YBSG", f"{self.project['name']}: {outcome}")
        return self.snapshot()

    def _run_stage(self, sid: str) -> StageResult:
        g = self.gen
        if sid == "validate": return g.stage_validate()
        if sid == "resolve": return g.stage_resolve()
        if sid == "fetch": return g.stage_fetch()
        if sid == "layer": return g.stage_layer()
        if sid == "dts": return g.stage_dts()
        if sid == "config": return g.stage_config()
        if sid == "dryrun": return g.stage_dryrun()
        if sid == "build": return self._stage_build()
        if sid == "package":
            r = g.stage_package(getattr(self, "build_info", None))
            self.artefacts = sorted(g.files)
            return r
        if sid == "report":
            from . import report
            paths_ = report.build_all(self.outdir, lambda level, msg: self.add_log("report", level, msg))
            self.artefacts += [str(Path(p).relative_to(self.outdir)) for p in paths_]
            return StageResult("report", "done", ", ".join(Path(p).name for p in paths_))
        raise KeyError(sid)

    # ---- stage 8: build -----------------------------------------------------------------------------------
    def _stage_build(self) -> StageResult:
        rc = resource_check()
        for w in rc["warnings"]:
            self.add_log("build", "warning", f"Host below recommended size: {w}. Builds will be slow or may fail (NFR-4).")
        rb = remote_builder()
        if rb:
            return self._stage_build_remote(*rb)
        backend = find_build_backend()
        if backend and backend[0] == "native":
            args = ["-n", f"{self.gen.slug}-image"] if os.environ.get("YBSG_BUILD_DRYRUN") else []
            rc_ = self._stream(["bash", str(self.outdir / "build.sh"), *args], dict(os.environ))
            if rc_ != 0:
                raise GenerationError("build", f"BitBake exited with code {rc_}; the log above has the reason (host packages, network or a recipe error)")
            return self._collect_deploy()
        if backend and backend[0] == "wsl":
            return self._stage_build_wsl(backend[1], backend[2])
        if not backend:
            raise GenerationError("build", "No build backend found: building needs git, bash and python3 (plain BitBake), or kas, or WSL2 on Windows, or a remote builder (Settings).")
        kas_file = self.outdir / "kas" / f"{self.gen.slug}.yml"
        cmd = backend + ["build", str(kas_file)]
        env = {**os.environ, "KAS_WORK_DIR": str(self.outdir)}  # the kas file's layer path is relative to the work dir
        rc_ = self._stream(cmd, env)
        if rc_ != 0:
            raise GenerationError("build", f"Build backend exited with code {rc_}")
        return self._collect_deploy()

    def _stage_build_remote(self, host: str, rdir: str) -> StageResult:
        """FR-16: copy the output to the remote builder, run kas there, stream its log back, fetch the images."""
        for tool in ("ssh", "rsync"):
            if not shutil.which(tool):
                raise GenerationError("build", f"Remote builder {host} needs {tool} on this machine")
        slug, machine = self.gen.slug, self.gen.machine
        rpath = f"{rdir.rstrip('/')}/{slug}"
        q = shlex.quote
        ssh = ["ssh", "-o", "BatchMode=yes", "-o", "ServerAliveInterval=30"]
        steps = [
            ("Preparing remote folder", ssh + [host, f"mkdir -p {q(rpath)}"]),
            ("Copying the generated BSP", ["rsync", "-az", "--delete", "--exclude", "/build/", "--exclude", "/cache/", "--exclude", "/.ybsg*",
                                           f"{self.outdir}/", f"{host}:{rpath}/"]),
            ("Building on " + host, ssh[:1] + ["-tt"] + ssh[1:] + [host, f"cd {q(rpath)} && export KAS_WORK_DIR=\"$PWD\" && "
                                     f"if command -v kas >/dev/null 2>&1; then kas build kas/{slug}.yml; else kas-container build kas/{slug}.yml; fi"]),
            ("Fetching images", ["rsync", "-az", "--mkpath", f"{host}:{rpath}/build/tmp/deploy/images/{machine}/",
                                 f"{self.outdir}/build/tmp/deploy/images/{machine}/"]),
        ]
        for label, cmd in steps:
            self.add_log("build", "info", f"{label}: " + " ".join(cmd))
            rc_ = self._stream(cmd, dict(os.environ))
            if rc_ != 0:
                raise GenerationError("build", f"{label} failed on the remote builder {host} (exit code {rc_})")
        res = self._collect_deploy()
        self.build_info["remote"] = f"{host}:{rpath}"
        return StageResult("build", "done", res.message + f" (built on {host})")

    def _stage_build_wsl(self, distro: str, kas: str) -> StageResult:
        """PKG-4: build inside WSL2 on the Linux filesystem (BitBake on /mnt/c is slow and trips over permissions)."""
        slug, machine = self.gen.slug, self.gen.machine
        win = str(self.outdir.resolve())
        src = _wsl_run(["-d", distro, "--", "wslpath", "-a", win]).stdout.decode(errors="ignore").strip()
        if not src.startswith("/"):
            raise GenerationError("build", f"Could not map {win} into WSL ({distro})")
        q = shlex.quote
        w = ["wsl.exe", "-d", distro, "--", "sh", "-lc"]
        rp = f"$HOME/ybsg-builds/{slug}"
        steps = [
            ("Copying the BSP into WSL", w + [f'mkdir -p "{rp}" && rsync -a --delete --exclude /build/ --exclude /cache/ {q(src)}/ "{rp}/"']),
            (f"Building in WSL ({distro})", w + [f'cd "{rp}" && export KAS_WORK_DIR="$PWD" && {kas} build kas/{slug}.yml']),
            ("Fetching images", w + [f'mkdir -p {q(src)}/build/tmp/deploy/images/{machine} && rsync -a "{rp}/build/tmp/deploy/images/{machine}/" {q(src)}/build/tmp/deploy/images/{machine}/']),
        ]
        for label, cmd in steps:
            self.add_log("build", "info", f"{label}: " + " ".join(cmd))
            rc_ = self._stream(cmd, dict(os.environ))
            if rc_ != 0:
                raise GenerationError("build", f"{label} failed (exit code {rc_})")
        res = self._collect_deploy()
        self.build_info["wsl"] = distro
        return StageResult("build", "done", res.message + f" (built in WSL2: {distro})")

    def _stream(self, cmd: list[str], env: dict) -> int:
        """Run a build command, forward its lines to the log and BitBake progress to the bar; honours pause/cancel."""
        popen_kw = {"preexec_fn": os.setsid} if not sys.platform.startswith("win") else {"creationflags": 0x00000200}
        self._proc = subprocess.Popen(cmd, cwd=str(self.outdir), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, **popen_kw)
        for line in self._proc.stdout:  # type: ignore[union-attr]
            line = line.rstrip().replace("\r", "")
            self._pause.wait()
            if self._cancel.is_set():
                raise Cancelled()
            lvl = "error" if "ERROR" in line else "warning" if "WARNING" in line else "info"
            self.add_log("build", lvl, line)
            pr = parse_bitbake_progress(line)
            if pr:
                n, tot, task = pr
                self._partial["build"] = n / max(tot, 1)
                self.current["task"] = f"{task}, task {n:,} of {tot:,}"
                self._recompute()
        rc_ = self._proc.wait()
        self._proc = None
        if self._cancel.is_set():
            raise Cancelled()
        return rc_

    def _collect_deploy(self) -> StageResult:
        deploy = self.outdir / "build" / "tmp" / "deploy" / "images" / self.gen.machine
        found = sorted(p.name for p in deploy.glob("*")) if deploy.exists() else []
        self.build_info = {"ran": True, "deploy": str(deploy), "images": found}
        comps = component_licences(self.outdir / "build" / "tmp" / "deploy" / "licenses")
        if comps:
            self.build_info["component_licences"] = comps
            self.add_log("build", "info", f"Licence inventory: {len(comps)} packages from the image license.manifest (NFR-14)")
        self.current["task"] = ""
        return StageResult("build", "done", f"{len(found)} artefacts in deploy dir" + (f", {len(comps)} packages in the licence inventory" if comps else ""))

    # ---- history (UI-P7) ----------------------------------------------------------------------------------
    def _record_history(self, started: float, outcome: str) -> None:
        try:
            d = paths.data_dir() / "history"
            d.mkdir(exist_ok=True)
            f = d / f"{self.project['uid']}.json"
            hist = json.loads(f.read_text()) if f.exists() else []
            hist.append({"start": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)), "duration": round(time.time() - started, 1), "outcome": outcome,
                         "outdir": str(self.outdir), "artefacts": self.artefacts[:200], "failure": self.failure})
            f.write_text(json.dumps(hist[-100:], indent=1))
        except Exception:
            pass


def history(uid: str) -> list[dict]:
    f = paths.data_dir() / "history" / f"{uid}.json"
    try:
        return json.loads(f.read_text())[::-1]
    except Exception:
        return []
