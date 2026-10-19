"""Board discovery from the BSP layers' own git repositories (DB-16).

The OpenEmbedded layer index refuses YBSG's user agent, so boards are read where they are defined: each layer's
conf/machine/*.conf. Only conf/ folders are fetched (shallow, sparse). For every machine we keep its name and
description, match it to a catalogue SoC (device-tree names, includes, overrides) and record the layer with its real
collection name and dependencies, so a generated BSP pulls in the right layers.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Callable

from . import paths

DATA = Path(__file__).parent / "data"
VENDOR_BY_LAYER = {
    "meta-freescale": "NXP", "meta-freescale-3rdparty": "Community (i.MX)", "meta-imx-bsp": "NXP", "meta-ti-bsp": "Texas Instruments",
    "meta-beagle": "BeagleBoard.org", "meta-st-stm32mp": "STMicroelectronics", "meta-rockchip": "Rockchip",
    "meta-raspberrypi": "Raspberry Pi", "meta-arm-bsp": "Arm", "meta-intel": "Intel", "meta-tegra": "NVIDIA", "meta-qcom": "Qualcomm",
    "meta-xilinx-core": "AMD Xilinx", "meta-xilinx-standalone": "AMD Xilinx", "meta-riscv": "RISC-V community", "meta-phytec": "PHYTEC",
    "meta-yocto-bsp": "Yocto Project",
}
DTB_MARK = "\x00dtb"  # marks an include level that lists device trees
_ASSIGN = re.compile(r'^\s*([A-Za-z0-9_:\-${}./]+)\s*(\?\?=|\?=|:=|\+=|=\+|\.=|=\.|=)\s*"((?:[^"\\]|\\.)*)"', re.M)


def discovery_repos() -> dict:
    return json.loads((DATA / "layers.json").read_text())["discovery"]


def _git(args: list[str], cwd: Path | None = None, timeout: int = 600) -> subprocess.CompletedProcess:
    # https/http only: a pasted link must never reach git transports such as ext:: (runs commands) or file://
    env = {**os.environ, "GIT_ALLOW_PROTOCOL": "https:http", "GIT_TERMINAL_PROMPT": "0"}
    return subprocess.run(["git", *args], cwd=str(cwd) if cwd else None, capture_output=True, text=True, timeout=timeout, env=env)


def fetch_conf(name: str, url: str, branch: str, cache: Path) -> Path:
    """Shallow, sparse checkout of conf/ folders only. Re-running updates it in place."""
    dest = cache / name
    if not (dest / ".git").exists():
        shutil.rmtree(dest, ignore_errors=True)
        r = _git(["clone", "--depth", "1", "--filter=blob:none", "--sparse", "-b", branch, "--", url, str(dest)])
        if r.returncode:
            raise RuntimeError(r.stderr.strip()[-300:])
        _git(["sparse-checkout", "set", "--no-cone", "/conf/", "/*/conf/", "/*/*/conf/"], dest)
    else:
        r = _git(["fetch", "--depth", "1", "origin", branch], dest)
        if r.returncode:
            raise RuntimeError(r.stderr.strip()[-300:])
        _git(["reset", "--hard", "-q", "FETCH_HEAD"], dest)
    return dest


def _vars(text: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for m in _ASSIGN.finditer(text.replace("\\\n", " ")):
        out.setdefault(m.group(1), []).append(m.group(3))
    return out


def _layer_of(path: Path, repo: Path) -> Path:
    for p in path.parents:
        if (p / "conf" / "layer.conf").exists():
            return p
        if p == repo:
            break
    return repo


def scan_layers(repo: Path, repo_name: str) -> dict[str, dict]:
    """layer name -> {path, collection, depends (collections), compat}."""
    out = {}
    for lc in sorted(repo.glob("**/conf/layer.conf")):
        root = lc.parent.parent
        v = _vars(lc.read_text(errors="replace"))
        coll = next((x.strip() for x in v.get("BBFILE_COLLECTIONS", []) if x.strip()), "")
        if not coll:
            continue
        deps = " ".join(x for k, xs in v.items() if k.startswith("LAYERDEPENDS_") and ":" not in k for x in xs)
        compat = " ".join(x for k, xs in v.items() if k.startswith("LAYERSERIES_COMPAT_") and ":" not in k for x in xs)
        rel = root.relative_to(repo).as_posix() or "."
        lname = repo_name if rel == "." else root.name
        out[lname] = {"path": rel, "collection": coll, "depends": [d for d in re.sub(r"\([^)]*\)", "", deps).split()], "compat": compat.split()}
    return out


def _tokens(conf: Path, repo: Path, depth: int = 0, seen: set | None = None) -> tuple[list[set[str]], dict[str, list[str]]]:
    """Words that identify the SoC (device-tree basenames, include names, overrides), one set per include depth, so
    the machine's own file outranks shared vendor includes that mention every SoC."""
    seen = seen if seen is not None else set()
    if conf in seen or depth > 6 or not conf.exists():
        return [], {}
    seen.add(conf)
    text = conf.read_text(errors="replace")
    v = _vars(text)
    toks: set[str] = set()
    tiers: list[set[str]] = [toks]
    for k, xs in v.items():
        if ":" in k:  # override-qualified (e.g. MACHINEOVERRIDES_EXTENDER:mx6ull:...) describes other SoCs
            continue
        if ("DEVICETREE" in k and "FILTER" not in k) or k.startswith("UBOOT_DTB") or k in ("MACHINEOVERRIDES", "SOC_FAMILY", "SOC_FAMILY_ARCH", "SOC"):
            for x in xs:
                for w in re.split(r"[\s:]+", x):
                    if w and "$" not in w:
                        toks.add(Path(w).name.removesuffix(".dtb").lower())
                        if "DEVICETREE" in k and w.endswith(".dtb"):
                            toks.add(DTB_MARK)
                            toks.add(DTB_MARK + Path(w).name.split("-")[0].lower())  # device-tree family, e.g. bcm2711
    for inc in re.findall(r"^\s*(?:require|include)\s+(\S+)", text, re.M):
        if "$" in inc:
            continue
        toks.add(Path(inc).stem.lower())
        cands = [conf.parent / inc] + [lr / inc for lr in {_layer_of(conf, repo)}] + list(repo.glob(f"**/{inc}"))[:3]
        for c in cands:
            if c.exists():
                t2, v2 = _tokens(c, repo, depth + 1, seen)
                for i, t in enumerate(t2, 1):
                    while len(tiers) <= i:
                        tiers.append(set())
                    tiers[i] |= t
                for k, xs in v2.items():
                    v.setdefault(k, xs)
                break
    return tiers, v


def match_soc(tokens: set[str], socs: list[dict]) -> str:
    """Longest catalogue SoC key that a token names: 'imx8mm-evk' -> imx8mm, 'k3-am625-sk' -> am625, 'mx8mm' -> imx8mm."""
    best, best_len = "", 0
    for s in socs:
        keys = {s["id"].lower()} | ({s["dt_compat"].split(",")[-1].lower()} if s.get("dt_compat") else set())
        keys |= {k[1:] for k in keys if k.startswith("imx")}  # meta-freescale overrides drop the leading i (mx8mm)
        for k in keys:
            for t in tokens:
                if len(k) > best_len and (t == k or t.startswith(k + "-") or t.startswith("k3-" + k) or (len(k) >= 6 and t.startswith(k))):
                    best, best_len = s["id"], len(k)
    return best


def scan_machines(repo: Path, repo_name: str, socs: list[dict], layers: dict) -> list[dict]:
    out = []
    for conf in sorted(repo.glob("**/conf/machine/*.conf")):
        text = conf.read_text(errors="replace")
        name = (re.search(r"^#@NAME:\s*(.+)$", text, re.M) or [None, ""])[1].strip()
        desc = (re.search(r"^#@DESCRIPTION:\s*(.+)$", text, re.M) or [None, ""])[1].strip()
        tiers, v = _tokens(conf, repo)
        tiers[0].add(conf.stem.lower())
        stem = conf.stem.lower()
        soc = next((x["id"] for x in socs if any(stem == h or stem.startswith(h + "-") for h in x.get("machine_hints", []))), "")
        acc: set[str] = set()
        for t in ([] if soc else tiers):  # nearest include that names a known SoC wins
            acc |= t
            families = {x for x in t if x.startswith(DTB_MARK) and x != DTB_MARK}
            if len(families) > 1:
                break  # a shared list naming several SoC families cannot tell which one this board uses
            soc = match_soc(acc, socs)
            if soc or DTB_MARK in t:  # the nearest file that names device trees decides; shared lists further up name every SoC
                break
        root = _layer_of(conf, repo)
        rel = root.relative_to(repo).as_posix() or "."
        lname = next((n for n, l in layers.items() if l["path"] == rel), repo_name)
        out.append({"machine": conf.stem, "name": name or conf.stem, "description": desc, "layer": lname,
                    "soc": soc, "vendor": VENDOR_BY_LAYER.get(lname, lname)})
    return out


def discover(socs: list[dict], release: str = "wrynose", only: list[str] | None = None,
             log: Callable[[str], None] = print, cache: Path | None = None) -> tuple[list[dict], dict, dict]:
    """Scan every discovery repo for `release`. Returns (machines, layer_catalog additions, repos additions)."""
    cache = cache or paths.cache_dir() / "discovery" / release
    cache.mkdir(parents=True, exist_ok=True)
    machines, layer_add, repo_add = [], {}, {}
    for name, meta in discovery_repos().items():
        if only and name not in only:
            continue
        branch = meta["branches"].get(release)
        if not branch:
            continue
        try:
            repo = fetch_conf(name, meta["url"], branch, cache)
        except Exception as e:  # noqa: BLE001
            log(f"{name}: not reachable ({e})")
            continue
        layers = scan_layers(repo, name)
        found = scan_machines(repo, name, socs, layers)
        log(f"{name}@{branch}: {len(layers)} layer(s), {len(found)} machine(s), {sum(1 for f in found if f['soc'])} on a known SoC")
        for f in found:
            f["repo"] = name
        machines += found
        repo_add[name] = {"url": meta["url"], "branches": meta["branches"], **({"licence_gate": meta["licence_gate"]} if meta.get("licence_gate") else {})}
        for lname, l in layers.items():
            layer_add[lname] = {"repo": name, "path": l["path"], "collection": l["collection"], "depends": l["depends"]}
    return machines, layer_add, repo_add


def resolve_needs(layer_add: dict, known: dict) -> dict:
    """Turn LAYERDEPENDS collection names into layer names (layers.json `needs`)."""
    by_coll = {v["collection"]: k for k, v in {**known, **layer_add}.items()}
    out = {}
    for k, v in layer_add.items():
        needs = sorted({by_coll[c] for c in v["depends"] if c in by_coll and by_coll[c] != k})
        out[k] = {"repo": v["repo"], "path": v["path"], "collection": v["collection"], "needs": needs or ["meta"],
                  **({"unresolved": sorted(c for c in v["depends"] if c not in by_coll)} if any(c not in by_coll for c in v["depends"]) else {})}
    return out


# ---- catalogue integration ---------------------------------------------------------------------------------
def overlay_path() -> Path:
    return paths.data_dir() / "layers-discovered.json"


def load_layers() -> dict:
    """layers.json plus layers learnt by discovery on this machine (shipped, verified entries always win)."""
    base = json.loads((DATA / "layers.json").read_text())
    f = overlay_path()
    if f.exists():
        try:
            extra = json.loads(f.read_text())
            for key in ("layer_catalog", "repos"):
                base[key] = {**extra.get(key, {}), **base[key]}
        except ValueError:
            pass
    return base


def save_overlay(layer_add: dict, repo_add: dict) -> None:
    cur = json.loads(overlay_path().read_text()) if overlay_path().exists() else {"layer_catalog": {}, "repos": {}}
    cur["layer_catalog"].update(layer_add)
    cur["repos"].update(repo_add)
    tmp = overlay_path().with_suffix(".tmp")
    tmp.write_text(json.dumps(cur, indent=1, sort_keys=True))
    tmp.replace(overlay_path())


def board_records(machines: list[dict], repos: dict, known_machines: set[str]) -> list[dict]:
    """Board records for machines the catalogue does not have yet. A machine on an unknown SoC is still listed
    (search, details) but generation explains that its SoC must be added first."""
    names = [m["machine"] for m in machines]
    out = []
    for m in machines:
        if m["machine"] in known_machines or m["machine"].endswith(("-k3r5", "-r5")) or "-k3r5-" in m["machine"]:
            continue  # TI k3r5 entries are firmware configurations for the boot R5 core, not boards
        rid = f"m-{m['machine']}" if names.count(m["machine"]) == 1 else f"m-{m['machine']}-{m['layer']}"
        out.append({"id": rid, "name": m["name"], "vendor": m["vendor"], "soc": m["soc"], "status": "community",
                    "layers": [m["layer"]], "machine": m["machine"], "description": m["description"],
                    "url": repos.get(m["repo"], {}).get("url", ""), "image_hint": "", "image_licence": "generated placeholder",
                    "preset": [], "ram": {"type": "", "size_mb": 0, "bus_width": 0}, "source": "layer git repository"})
    return out


def matches(rec: dict, q: str) -> bool:
    q = re.sub(r"[\W_]+", "", q.lower())
    hay = re.sub(r"[\W_]+", "", f"{rec.get('name', '')} {rec.get('machine', '')} {rec.get('description', '')} {rec.get('vendor', '')}".lower())
    return bool(q) and q in hay
