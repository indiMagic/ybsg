"""Capability graph: evaluates every choice against SoC controllers, pins, clocks, DMA and drivers.

FR-9  offer only valid peripherals; disabled items stay visible with the reason.
FR-12 conflicts (pin mux, shared clock, DMA, power, memory map) shown with suggested fixes;
      generation blocked until resolved or waived.
DB-1..DB-4 driver listing, kernel config symbols, "no driver" slot, driver/kernel mismatch.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, asdict
from itertools import product

from .catalog import Catalog, driver_binds
from .project import CATEGORY_CONTROLLER, IC_CATEGORY

DMA_POOL_DEFAULT = 8


def kver(s: str | None) -> tuple[int, int] | None:
    try:
        a, b = str(s).split(".")[:2]
        return int(a), int(b)
    except Exception:
        return None


@dataclass
class Issue:
    code: str
    severity: str  # error | warn | info
    message: str
    fix: str = ""
    uid: str = ""
    kind: str = "invalid"  # invalid | pinmux | clock | dma | memmap | power | address | bandwidth | licence | data
    blocking: bool = True  # blocks generation unless waived
    waivable: bool = False
    id: str = field(default="")

    def __post_init__(self) -> None:
        self.id = hashlib.sha1(f"{self.code}:{self.uid}:{self.message}".encode()).hexdigest()[:10]


class CapabilityGraph:
    def __init__(self, cat: Catalog):
        self.cat = cat

    # ---- helpers -----------------------------------------------------------------------
    def soc(self, p: dict) -> dict | None:
        return self.cat.get("soc", p["soc"]) if p.get("soc") else None

    def kernel_ok(self, soc: dict, kernel: str) -> bool:
        return kernel in soc.get("kernels", [])

    def _kernel_tuple(self, p: dict, soc: dict | None) -> tuple[int, int] | None:
        k = self.cat.get("kernel", p["kernel"])
        return (k["major"], k["minor"]) if k else kver(p["kernel"])

    # ---- driver status against the chosen kernel (DB-1, DB-4) ----------------------------
    def driver_status(self, drv: dict, p: dict, soc: dict | None = None) -> tuple[str, str]:
        """Return (status, reason): ok | unverified | removed | not-yet | vendor-only."""
        kt = self._kernel_tuple(p, soc)
        first, last, removed = kver(drv.get("first_kernel")), kver(drv.get("last_verified")), kver(drv.get("removed_in"))
        kernel = self.cat.get("kernel", p["kernel"]) or {}
        if drv.get("source") == "vendor" and kernel.get("kind") not in ("vendor", None):
            return "vendor-only", f"{drv['name']} lives in a vendor tree; the selected kernel is {kernel.get('label', p['kernel'])}"
        if kt and removed and kt >= removed:
            return "removed", f"{drv['name']} was removed in kernel {drv['removed_in']}; selected kernel is {kt[0]}.{kt[1]}"
        if kt and first and kt < first:
            return "not-yet", f"{drv['name']} first appears in kernel {drv['first_kernel']}"
        if kt and last and kt > last:
            return "unverified", f"{drv['name']} last verified on kernel {drv['last_verified']}"
        return "ok", "in range"

    # ---- FR-9 / FR-10: what can be offered -----------------------------------------------
    def controllers(self, p: dict, ctype: str) -> list[dict]:
        soc = self.soc(p)
        out = []
        for c in (soc or {}).get("controllers", []):
            if c["type"] != ctype or c.get("count", 1) == 0:
                continue
            for i in range(c.get("count", 1)):
                inst = dict(c)
                inst["instance"] = f"{c['name']}" if c.get("count", 1) == 1 else f"{c['name']}#{i}"
                inst["slot"] = i
                out.append(inst)
        return out

    def offer_ics(self, p: dict, category: str) -> list[dict]:
        """Every IC in the category with enabled/disabled state and the reason (FR-9)."""
        soc = self.soc(p)
        ctype = CATEGORY_CONTROLLER[category]
        want_mode = {"usb_device": ("device", "otg")}.get(category, None)
        out = []
        ctrls = self.controllers(p, ctype)
        used = self._assignment(p)[0]
        for ic in self.cat.ics_for(IC_CATEGORY.get(category, category)):
            reason = ""
            if not soc:
                reason = "No SoC selected"
            elif not ctrls:
                reason = f"{soc['name']} has no {ctype} controller"
            else:
                modes_ok = [c for c in ctrls if set(c["modes"]) & set(ic["controller_modes"])
                            and (not want_mode or set(want_mode) & set(c["modes"]))]
                free = [c for c in modes_ok if c["instance"] not in used.values()]
                if not modes_ok:
                    reason = f"No {soc['name']} controller supports {ic['interface']}"
                elif not free and ctype != "i2c":
                    reason = "All compatible controllers are already in use"
            drivers = self.cat.drivers_for_ic(ic["id"])
            kernel_ok = [d for d in drivers if self.driver_status(d, p, soc)[0] in ("ok", "unverified")]
            if not reason and drivers and not kernel_ok:
                reason = "Every known driver is unavailable on the selected kernel"
            out.append({**ic, "enabled": not reason, "reason": reason, "driver_count": len(drivers),
                        "no_driver": len(drivers) == 0})
        return out

    def offer_drivers(self, p: dict, ic_id: str) -> list[dict]:
        soc = self.soc(p)
        out = []
        for d in self.cat.drivers_for_ic(ic_id):
            st, why = self.driver_status(d, p, soc)
            out.append({**d, "status": st, "reason": why, "enabled": st in ("ok", "unverified")})
        return out

    # ---- controller assignment ---------------------------------------------------------------
    def _assignment(self, p: dict) -> tuple[dict[str, str], list[Issue]]:
        """Backtracking assignment of peripherals to controller instances. i2c devices share buses."""
        issues: list[Issue] = []
        assign: dict[str, str] = {}
        soc = self.soc(p)
        if not soc:
            return assign, issues
        pending = []
        for x in p["peripherals"]:
            ctype = CATEGORY_CONTROLLER.get(x["category"])
            if not ctype or ctype == "i2c":
                continue
            ic = self.cat.get("ic", x["ic"]) if x["ic"] else None
            cands = []
            for c in self.controllers(p, ctype):
                if ic and not (set(c["modes"]) & set(ic["controller_modes"])):
                    continue
                if x["category"] == "usb_device" and not ({"device", "otg"} & set(c["modes"])):
                    continue
                if x["category"] == "usb_host" and x.get("mode") and x["mode"] not in c["modes"]:
                    continue
                cands.append(c["instance"])
            fam = [c["instance"] for c in self.controllers(p, ctype) if x.get("driver") and c.get("driver_family") == x["driver"] and c["instance"] in cands]
            if fam and ctype in ("usb_host", "mmc"):
                cands = fam
            pending.append((x["uid"], cands, x["category"]))
        pending.sort(key=lambda t: len(t[1]))

        def solve(i: int, taken: set[str]) -> dict[str, str] | None:
            if i == len(pending):
                return {}
            uid, cands, _ = pending[i]
            for c in cands:
                if c in taken:
                    continue
                rest = solve(i + 1, taken | {c})
                if rest is not None:
                    rest[uid] = c
                    return rest
            return None

        sol = solve(0, set())
        if sol is not None:
            assign = sol
        else:
            # find which peripherals cannot be placed: greedy, report the rest
            taken: set[str] = set()
            for uid, cands, cat_ in pending:
                free = [c for c in cands if c not in taken]
                if free:
                    assign[uid] = free[0]
                    taken.add(free[0])
                else:
                    label = cat_.replace("_", " ")
                    issues.append(Issue("no-controller", "error",
                                        f"No free {label} controller on {soc['name']} for this selection",
                                        "Remove another peripheral on this controller type, choose an IC that fits another controller, or pick a different SoC",
                                        uid, "invalid", True, False))
        return assign, issues

    # ---- full validation ---------------------------------------------------------------------------
    def validate(self, p: dict) -> dict:
        issues: list[Issue] = []
        soc = self.soc(p)
        board = self.cat.get("board", p["board"]) if p.get("board") else None

        if not p.get("soc"):
            issues.append(Issue("no-soc", "error", "No SoC or board selected", "Pick a board or a SoC in Library", "", "invalid", True, False))
            return self._pack(p, issues, {})
        if not soc:
            issues.append(Issue("unknown-soc", "error", f"SoC '{p['soc']}' is not in the catalog", "Add it as a user record (Data > Local database)", "", "invalid", True, False))
            return self._pack(p, issues, {})
        if board and board["soc"] != soc["id"]:
            issues.append(Issue("board-soc-mismatch", "error", f"Board {board['name']} uses {board['soc']}, not {soc['id']}", "Select the board's SoC", "", "invalid", True, False))

        # software section
        rel = self.cat.get("release", p["release"])
        if not rel:
            issues.append(Issue("release-unknown", "error", f"Yocto release '{p['release']}' is not in release metadata", "Choose a listed release", "", "invalid"))
        if not self.kernel_ok(soc, p["kernel"]):
            issues.append(Issue("kernel-unsupported", "error", f"{soc['name']} has no support status for kernel '{p['kernel']}'",
                                f"Choose one of: {', '.join(soc['kernels'])}", "", "invalid"))
        if p["kernel"] == "vendor" and soc.get("licence_gate", "none").lower().startswith("none") is False:
            pass
        gate = soc.get("licence_gate", "none")
        if gate and not gate.lower().startswith("none"):
            issues.append(Issue("licence-gate", "warn", f"Licence acceptance needed: {gate}. YBSG never accepts a licence for you.",
                                "Accept the licence yourself on the vendor site before building", "", "licence", False))
        if p["image"] == "distro":
            issues.append(Issue("distro-rootfs", "info", "The distribution rootfs is not Yocto-built and is outside Yocto's SBOM and CVE tracking (GEN-9).",
                                "", "", "data", False))
            if not p.get("distro"):
                issues.append(Issue("distro-missing", "error", "Distro image chosen but no distribution selected", "Pick Ubuntu, Debian, Kali, Fedora, Arch ARM or Alpine", "", "invalid"))

        # controller assignment
        assign, assign_issues = self._assignment(p)
        issues += assign_issues
        ctrl_by_name = {c["instance"]: c for ctype in {c["type"] for c in soc["controllers"]} for c in self.controllers(p, ctype)}

        # per-peripheral checks
        for x in p["peripherals"]:
            ic = self.cat.get("ic", x["ic"]) if x["ic"] else None
            label = CATEGORY_LABEL_SAFE.get(x["category"], x["category"])
            if not ic and not x.get("custom"):
                issues.append(Issue("no-ic", "error", f"{label}: no IC chosen", "Choose an IC or enter a custom one", x["uid"], "invalid"))
                continue
            if ic:
                if not any(c for c in self.controllers(p, CATEGORY_CONTROLLER[x["category"]]) if set(c["modes"]) & set(ic["controller_modes"])):
                    issues.append(Issue("ic-incompatible", "error", f"{label}: {ic['part']} ({ic['interface']}) is not supported by any {soc['name']} controller",
                                        "Choose an IC with a compatible interface", x["uid"], "invalid"))
                drivers = self.cat.drivers_for_ic(ic["id"])
                drv = self.cat.get("driver", x["driver"]) if x["driver"] else None
                if not drivers and not x.get("custom", {}).get("driver"):
                    issues.append(Issue("no-driver", "warn", f"{label}: {ic['part']} has no known driver", "Use the custom-driver slot (path, git URL or patch file) (DB-3)",
                                        x["uid"], "data", False))
                elif x["driver"] and not drv:
                    issues.append(Issue("driver-unknown", "error", f"{label}: driver '{x['driver']}' is not in the catalog", "Choose a listed driver", x["uid"], "invalid"))
                elif drv:
                    if not driver_binds(drv, ic):
                        issues.append(Issue("driver-ic-mismatch", "error", f"{label}: driver {drv['name']} does not bind to {ic['part']}", "Choose a driver that lists this IC", x["uid"], "invalid"))
                    st, why = self.driver_status(drv, p, soc)
                    if st in ("removed", "not-yet"):
                        issues.append(Issue("driver-kernel-mismatch", "error", f"{label}: {why}", "Pick another driver or kernel (DB-4)", x["uid"], "invalid"))
                    elif st == "vendor-only":
                        issues.append(Issue("driver-vendor-only", "warn", f"{label}: {why}", "Select the vendor kernel or a mainline driver", x["uid"], "data", False))
                    elif st == "unverified":
                        issues.append(Issue("driver-unverified", "warn", f"{label}: {why}", "Validate on hardware", x["uid"], "data", False))
                elif drivers and not x["driver"]:
                    issues.append(Issue("no-driver-chosen", "warn", f"{label}: no driver chosen for {ic['part']}", "Choose one of the listed drivers", x["uid"], "data", False))
            # flash partitions: memory-map overlap
            if x["category"] == "flash":
                parts = sorted((q for q in x.get("props", {}).get("partitions", [])), key=lambda q: q["offset"])
                for a, b in zip(parts, parts[1:]):
                    if a["offset"] + a["size"] > b["offset"]:
                        issues.append(Issue("partition-overlap", "error", f"Flash partitions '{a['name']}' and '{b['name']}' overlap",
                                            "Shift the second partition's offset or shrink the first", x["uid"], "memmap", True, True))

        # pin-mux exclusions between assigned controllers
        pairs: set[frozenset] = set()
        for uid, cname in assign.items():
            c = ctrl_by_name.get(cname, {})
            for token in c.get("excludes", []):
                for uid2, cname2 in assign.items():
                    if uid2 == uid:
                        continue
                    c2 = ctrl_by_name.get(cname2, {})
                    if (token in c2.get("name", "") or any(token in pin for pin in c2.get("pins", [])))                             and frozenset((cname, cname2)) not in pairs:
                        pairs.add(frozenset((cname, cname2)))
                        issues.append(Issue("pinmux", "error", f"Pin-mux conflict: {c['name']} and {c2['name']} share pins ({token})",
                                            f"Drop one of them, or move {c['name']} to another controller", uid, "pinmux", True, True))
        # i2c address conflicts (custom ICs and catalog sensors with props.address)
        buses: dict[tuple[str, str], list[str]] = {}
        for x in p["peripherals"]:
            addr = x.get("props", {}).get("address") or x.get("custom", {}).get("address")
            if addr:
                bus = str(x.get("props", {}).get("bus") or x.get("custom", {}).get("bus") or "i2c0")
                buses.setdefault((bus, str(addr).lower()), []).append(x["uid"])
        for ci in p.get("custom_ics", []):
            if ci.get("address"):
                buses.setdefault((str(ci.get("bus", "i2c0")), str(ci["address"]).lower()), []).append("custom:" + ci["id"])
        for (bus, addr), users in buses.items():
            if len(users) > 1:
                issues.append(Issue("i2c-address", "error", f"Two devices share address {addr} on {bus}", "Change one address or use another bus",
                                    users[0], "address", True, True))

        # driver must match the controller it is placed on (usb/mmc host drivers)
        for x in p["peripherals"]:
            c = ctrl_by_name.get(assign.get(x["uid"], ""), {})
            if x.get("driver") and x["category"] in ("usb_host", "usb_device", "mmc") and c.get("driver_family"):
                known = {d["id"] for d in self.cat.list("driver")}
                if c["driver_family"] in known and x["driver"] != c["driver_family"]:
                    issues.append(Issue("driver-controller-mismatch", "error",
                                        f"Driver {x['driver']} does not drive {c['name']} (needs {c['driver_family']})",
                                        f"Choose {c['driver_family']}", x["uid"], "invalid"))

        # DMA budget
        pool = soc.get("dma_channels", DMA_POOL_DEFAULT)
        dma = sum(ctrl_by_name.get(c, {}).get("dma", 0) for c in assign.values())
        if dma > pool:
            issues.append(Issue("dma-exhausted", "error", f"DMA demand {dma} exceeds the {pool}-channel pool on {soc['name']}",
                                "Drop a DMA-hungry peripheral or share a controller", "", "dma", True, True))

        # shared clock rule: exclusive clocks
        clk_users: dict[str, list[str]] = {}
        for uid, cname in assign.items():
            for clk in ctrl_by_name.get(cname, {}).get("clock_exclusive", []):
                clk_users.setdefault(clk, []).append(cname)
        for clk, users in clk_users.items():
            if len(users) > 1:
                issues.append(Issue("shared-clock", "error", f"Controllers {', '.join(users)} need the exclusive clock {clk}",
                                    "Use only one of them", "", "clock", True, True))

        # power domains: IO voltage agreement and current budget per supply domain
        issues += self._power_issues(p, soc, assign, ctrl_by_name)

        # RAM
        ram = p.get("ram", {})
        ram_ctrl = self.controllers(p, "ram")
        if not ram.get("type") or not ram.get("size_mb"):
            issues.append(Issue("ram-missing", "error", "RAM type and size are not set", "Choose a RAM type and size in the RAM section", "", "invalid"))
        elif ram_ctrl and ram["type"] not in ram_ctrl[0]["modes"]:
            issues.append(Issue("ram-unsupported", "error", f"{soc['name']} does not support {ram['type']} (supports {', '.join(ram_ctrl[0]['modes'])})",
                                "Choose a supported memory type", "", "invalid"))
        elif board and (ram.get("type") != board.get("ram", {}).get("type") or ram.get("size_mb") != board.get("ram", {}).get("size_mb")):
            issues.append(Issue("ram-changed", "warn", "RAM differs from the reference board: DRAM timing/training must be validated on hardware (GEN-5)",
                                "Reuse the vendor DRAM configuration; validate on hardware", "", "data", False))
        elif not board:
            issues.append(Issue("ram-new-combo", "warn", "Custom board RAM: DRAM timing/training must be validated on hardware (GEN-5)",
                                "Reuse vendor DRAM configuration", "", "data", False))

        # bandwidth rule: stacked high-speed streams
        hs = sum(1 for x in p["peripherals"] if x["category"] in ("camera", "display")) + sum(
            1 for x in p["peripherals"] if x["category"] == "usb_host" and "uvc" in x.get("props", {}).get("class", ""))
        if hs >= 3:
            issues.append(Issue("bandwidth", "warn", f"{hs} high-speed streams share memory bandwidth; verify on hardware", "Lower resolution or frame rate", "",
                                "bandwidth", False, True))

        # board-data confidence
        if board and board["_prov"]["disputed"]:
            issues.append(Issue("data-disputed", "warn", f"Board record is disputed: {board['_prov']['dispute_note']}. The device-tree value is used until you choose.",
                                "Open Library > Boards and pick a value", "", "data", False))
        if board and board["_prov"]["confidence"] == "unverified":
            issues.append(Issue("data-unverified", "warn", "Board record is user-entered and unverified", "", "", "data", False))

        return self._pack(p, issues, assign)

    def _power_issues(self, p: dict, soc: dict, assign: dict[str, str], ctrl_by_name: dict) -> list[Issue]:
        """FR-12 power: SoC `power_domains` [{name, io_mv: [..], budget_ma}], controller `power_domain`,
        IC or peripheral props `io_mv` (list of acceptable IO levels) and `current_ma`. Missing data is not checked."""
        domains = {d["name"]: d for d in soc.get("power_domains", [])}
        if not domains:
            return []
        issues: list[Issue] = []
        users: dict[str, list[tuple[dict, dict]]] = {}
        for x in p["peripherals"]:
            dom = ctrl_by_name.get(assign.get(x["uid"], ""), {}).get("power_domain")
            if dom in domains:
                ic = (self.cat.get("ic", x["ic"]) if x.get("ic") else None) or {}
                users.setdefault(dom, []).append((x, ic))
        for name, members in users.items():
            d = domains[name]
            levels = set(d.get("io_mv", []))
            for x, ic in members:
                want = x.get("props", {}).get("io_mv") or ic.get("io_mv")
                if want:
                    levels &= set(want if isinstance(want, list) else [want])
            if d.get("io_mv") and not levels:
                parts = ", ".join(ic.get("part") or x["category"] for x, ic in members)
                issues.append(Issue("power-io-voltage", "error",
                                    f"No common IO voltage on power domain {name} for {parts} (domain allows {', '.join(f'{v} mV' for v in d['io_mv'])})",
                                    "Move one peripheral to a controller on another domain, add a level shifter, or choose an IC with a matching IO level",
                                    members[0][0]["uid"], "power", True, True))
            load = sum(int(x.get("props", {}).get("current_ma") or ic.get("current_ma") or 0) for x, ic in members)
            if d.get("budget_ma") and load > d["budget_ma"]:
                issues.append(Issue("power-budget", "error", f"Power domain {name} load {load} mA exceeds its {d['budget_ma']} mA budget",
                                    "Supply the heavy peripheral from its own regulator or drop one", members[0][0]["uid"], "power", True, True))
        return issues

    def _pack(self, p: dict, issues: list[Issue], assign: dict) -> dict:
        waived = set(p.get("waivers", []))
        out = []
        for i in issues:
            d = asdict(i)
            d["waived"] = i.id in waived and i.waivable
            out.append(d)
        blocking = [d for d in out if d["severity"] == "error" and d["blocking"] and not d["waived"]]
        conflicts = [d for d in out if d["kind"] in ("pinmux", "clock", "dma", "memmap", "power", "address", "bandwidth") and d["severity"] == "error" and not d["waived"]]
        peri_invalid = [d for d in blocking if d["uid"] or d["code"] in ("no-soc", "unknown-soc", "board-soc-mismatch", "ram-missing", "ram-unsupported", "no-controller")]
        sw_invalid = [d for d in blocking if d["code"] in ("release-unknown", "kernel-unsupported", "distro-missing")]
        has_soc = bool(p.get("soc"))
        sections = {
            "peripherals": has_soc and not peri_invalid and not conflicts,
            "software": has_soc and not sw_invalid,
            "review": has_soc and not blocking,
        }
        sections["generate"] = all(sections[k] for k in ("peripherals", "software", "review"))
        return {"issues": out, "assignment": assign, "conflicts": len(conflicts), "blocking": len(blocking),
                "sections": sections, "can_generate": sections["generate"]}

    def waive(self, p: dict, issue_id: str) -> None:
        if issue_id not in p["waivers"]:
            p["waivers"].append(issue_id)


CATEGORY_LABEL_SAFE = {
    "usb_host": "USB host", "usb_device": "USB device", "camera": "Camera", "display": "Display", "mmc": "SD/eMMC",
    "flash": "Flash", "ram": "RAM", "net": "Network", "audio": "Audio", "i2c": "Sensor/bus",
}
