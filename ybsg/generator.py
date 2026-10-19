"""BSP generator: layer, machine, device tree, kernel/U-Boot config, image recipe, kas file, manifest.

Deterministic (GEN-10): no timestamps or random values in generated files. All output goes through
`emit`, which reuses identical files and never overwrites user edits silently (GEN-12, FR-19).
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import __version__, paths
from .branding import brand
from .catalog import Catalog, driver_binds
from .graph import CapabilityGraph, kver

DATA = Path(__file__).parent / "data"
STAGES = [
    ("validate", "Validate project"), ("resolve", "Resolve sources"), ("fetch", "Fetch"),
    ("layer", "Generate layer"), ("dts", "Generate device tree"), ("config", "Kernel and U-Boot config"),
    ("dryrun", "Dry-run parse"), ("build", "Build"), ("package", "Package"), ("report", "Report"),
]
STAGE_IDS = [s for s, _ in STAGES]

TUNE = {
    "imx8mm": "arm/armv8a/tune-cortexa53.inc", "am625": "arm/armv8a/tune-cortexa53.inc",
    "rk3568": "arm/armv8-2a/tune-cortexa55.inc", "bcm2711": "arm/armv8a/tune-cortexa72.inc", "bcm2712": "arm/armv8-2a/tune-cortexa76.inc",
    "imx6ull": "arm/armv7a/tune-cortexa7.inc", "stm32mp157": "arm/armv7a/tune-cortexa7.inc",
    "am335x": "arm/armv7a/tune-cortexa8.inc",
}
DEFAULT_I2C_ADDR = {"imx219": "0x10", "ov5640": "0x3c", "imx477": "0x1a", "ov7670": "0x21", "adv7533": "0x39",
                    "adv7535": "0x3d", "tda19988": "0x70", "wm8960": "0x1a", "tlv320aic3104": "0x18", "bme280": "0x76",
                    "mpu6050": "0x68", "ds3231": "0x68", "gt911": "0x5d", "sn65dsi83": "0x2d"}
# Kconfig dependencies resolved transitively into the fragment (DB-2). Extend via the catalog or plugins.
from . import plugins as _plugins  # noqa: E402

KCONFIG_DEPS = {
    "CONFIG_USB_DWC3_HOST": ["CONFIG_USB_DWC3", "CONFIG_USB"], "CONFIG_USB_DWC3": ["CONFIG_USB", "CONFIG_USB_GADGET"],
    "CONFIG_USB_XHCI_PCI": ["CONFIG_USB_XHCI_HCD", "CONFIG_PCI"], "CONFIG_USB_XHCI_HCD": ["CONFIG_USB"],
    "CONFIG_USB_EHCI_HCD_PLATFORM": ["CONFIG_USB_EHCI_HCD"], "CONFIG_USB_EHCI_HCD": ["CONFIG_USB"],
    "CONFIG_USB_DWC2_DUAL_ROLE": ["CONFIG_USB_DWC2", "CONFIG_USB_GADGET"], "CONFIG_USB_DWC2": ["CONFIG_USB"],
    "CONFIG_USB_CHIPIDEA_HOST": ["CONFIG_USB_CHIPIDEA", "CONFIG_USB"], "CONFIG_USB_CHIPIDEA_UDC": ["CONFIG_USB_CHIPIDEA", "CONFIG_USB_GADGET"],
    "CONFIG_USB_MUSB_DSPS": ["CONFIG_USB_MUSB_HDRC"], "CONFIG_USB_MUSB_HDRC": ["CONFIG_USB"],
    "CONFIG_VIDEO_IMX219": ["CONFIG_MEDIA_SUPPORT", "CONFIG_MEDIA_CAMERA_SUPPORT", "CONFIG_V4L_SUBDEV_API", "CONFIG_I2C"],
    "CONFIG_VIDEO_OV5640": ["CONFIG_MEDIA_SUPPORT", "CONFIG_MEDIA_CAMERA_SUPPORT", "CONFIG_V4L_SUBDEV_API", "CONFIG_I2C"],
    "CONFIG_VIDEO_IMX477": ["CONFIG_MEDIA_SUPPORT", "CONFIG_MEDIA_CAMERA_SUPPORT", "CONFIG_V4L_SUBDEV_API", "CONFIG_I2C"],
    "CONFIG_VIDEO_OV7670": ["CONFIG_MEDIA_SUPPORT", "CONFIG_MEDIA_CAMERA_SUPPORT", "CONFIG_V4L_SUBDEV_API", "CONFIG_I2C"],
    "CONFIG_MEDIA_CAMERA_SUPPORT": ["CONFIG_MEDIA_SUPPORT"],
    "CONFIG_DRM_I2C_ADV7511": ["CONFIG_DRM", "CONFIG_DRM_KMS_HELPER", "CONFIG_I2C"], "CONFIG_DRM_I2C_ADV7533": ["CONFIG_DRM_I2C_ADV7511"],
    "CONFIG_DRM_I2C_NXP_TDA998X": ["CONFIG_DRM", "CONFIG_I2C"], "CONFIG_DRM_TI_SN65DSI83": ["CONFIG_DRM", "CONFIG_DRM_PANEL_BRIDGE"],
    "CONFIG_DRM_VC4": ["CONFIG_DRM"], "CONFIG_DRM_ROCKCHIP": ["CONFIG_DRM"], "CONFIG_ROCKCHIP_VOP2": ["CONFIG_DRM_ROCKCHIP"],
    "CONFIG_ROCKCHIP_DW_HDMI": ["CONFIG_DRM_ROCKCHIP"],
    "CONFIG_MMC_SDHCI_IPROC": ["CONFIG_MMC", "CONFIG_MMC_SDHCI", "CONFIG_MMC_SDHCI_PLTFM"],
    "CONFIG_MMC_SDHCI_ESDHC_IMX": ["CONFIG_MMC", "CONFIG_MMC_SDHCI", "CONFIG_MMC_SDHCI_PLTFM"],
    "CONFIG_MMC_SDHCI_AM654": ["CONFIG_MMC", "CONFIG_MMC_SDHCI", "CONFIG_MMC_SDHCI_PLTFM"],
    "CONFIG_MMC_OMAP_HS": ["CONFIG_MMC"], "CONFIG_MMC_ARMMMCI": ["CONFIG_MMC", "CONFIG_ARM_AMBA"],
    "CONFIG_MMC_DW_ROCKCHIP": ["CONFIG_MMC", "CONFIG_MMC_DW"], "CONFIG_MMC_SDHCI": ["CONFIG_MMC"],
    "CONFIG_BRCMFMAC_SDIO": ["CONFIG_BRCMFMAC", "CONFIG_MMC"], "CONFIG_BRCMFMAC": ["CONFIG_CFG80211", "CONFIG_WLAN"],
    "CONFIG_MTD_SPI_NOR": ["CONFIG_MTD", "CONFIG_SPI"], "CONFIG_MTD_SPI_NAND": ["CONFIG_MTD", "CONFIG_MTD_NAND_CORE", "CONFIG_SPI"],
    "CONFIG_MTD_RAW_NAND": ["CONFIG_MTD"], "CONFIG_MTD_UBI": ["CONFIG_MTD"],
    "CONFIG_BCMGENET": ["CONFIG_NET_VENDOR_BROADCOM"], "CONFIG_DWMAC_ROCKCHIP": ["CONFIG_STMMAC_ETH"], "CONFIG_DWMAC_STM32": ["CONFIG_STMMAC_ETH"],
    "CONFIG_FEC": ["CONFIG_NET_VENDOR_FREESCALE"], "CONFIG_TI_CPSW": ["CONFIG_NET_VENDOR_TI"], "CONFIG_TI_K3_AM65_CPSW_NUSS": ["CONFIG_NET_VENDOR_TI"],
    "CONFIG_SND_SOC_WM8960": ["CONFIG_SND_SOC", "CONFIG_I2C"], "CONFIG_SND_SOC_TLV320AIC3X_I2C": ["CONFIG_SND_SOC", "CONFIG_I2C"],
    "CONFIG_BMP280_I2C": ["CONFIG_BMP280", "CONFIG_IIO", "CONFIG_I2C"], "CONFIG_INV_MPU6050_I2C": ["CONFIG_INV_MPU6050_IIO", "CONFIG_IIO", "CONFIG_I2C"],
    "CONFIG_RTC_DRV_DS1307": ["CONFIG_RTC_CLASS", "CONFIG_I2C"], "CONFIG_TOUCHSCREEN_GOODIX": ["CONFIG_INPUT_TOUCHSCREEN", "CONFIG_I2C"],
}
GADGET_FUNCS = {
    "mass_storage": ["CONFIG_USB_CONFIGFS_MASS_STORAGE"], "serial": ["CONFIG_USB_CONFIGFS_SERIAL", "CONFIG_USB_CONFIGFS_ACM"],
    "ecm": ["CONFIG_USB_CONFIGFS_ECM"], "ncm": ["CONFIG_USB_CONFIGFS_NCM"], "rndis": ["CONFIG_USB_CONFIGFS_RNDIS"],
    "hid": ["CONFIG_USB_CONFIGFS_F_HID"], "uvc": ["CONFIG_USB_CONFIGFS_F_UVC", "CONFIG_VIDEO_DEV"], "midi": ["CONFIG_USB_CONFIGFS_F_MIDI"],
    "composite": ["CONFIG_USB_CONFIGFS_F_FS"], "custom": ["CONFIG_USB_CONFIGFS_F_FS"],
}
USB_HOST_CLASS = {
    "mass_storage": ["CONFIG_USB_STORAGE", "CONFIG_SCSI", "CONFIG_BLK_DEV_SD"], "uvc": ["CONFIG_USB_VIDEO_CLASS", "CONFIG_MEDIA_SUPPORT", "CONFIG_MEDIA_USB_SUPPORT", "CONFIG_MEDIA_CAMERA_SUPPORT"],
    "hid": ["CONFIG_USB_HID", "CONFIG_HID_GENERIC", "CONFIG_HIDRAW"], "cdc_acm": ["CONFIG_USB_ACM"], "cdc_ecm": ["CONFIG_USB_NET_CDCETHER"],
    "audio": ["CONFIG_SND_USB_AUDIO", "CONFIG_SND"], "printer": ["CONFIG_USB_PRINTER"],
}
FLASH_CMDS = {
    "imx8mm": {"sd": "sudo bmaptool copy {img}.wic.gz /dev/sdX", "emmc": "uuu -b emmc_all imx-boot-{machine}-sd.bin-flash_evk {img}.wic  # board in serial-download mode", "spi": "flashcp -v imx-boot /dev/mtd0  # from running Linux"},
    "imx6ull": {"sd": "sudo bmaptool copy {img}.wic.gz /dev/sdX", "emmc": "uuu -b emmc_all u-boot-{machine}.imx {img}.wic", "spi": "flashcp -v u-boot-dtb.imx /dev/mtd0"},
    "am335x": {"sd": "sudo bmaptool copy {img}.wic.gz /dev/sdX", "emmc": "Boot from SD, then: zcat {img}.wic.gz | dd of=/dev/mmcblk1 bs=4M conv=fsync", "spi": "flashcp -v u-boot.img /dev/mtd0"},
    "stm32mp157": {"sd": "sudo bmaptool copy {img}.wic.gz /dev/sdX", "emmc": "STM32CubeProgrammer -c port=usb1 -w flashlayout_{img}/FlashLayout.tsv  # DFU", "spi": "STM32CubeProgrammer, QSPI flash layout"},
    "rk3568": {"sd": "sudo bmaptool copy {img}.wic.gz /dev/sdX", "emmc": "rkdeveloptool db rk356x_loader.bin && rkdeveloptool wl 0 {img}.wic  # MaskROM mode", "spi": "rkdeveloptool wl 0 spi-image.img"},
    "bcm2711": {"sd": "sudo bmaptool copy {img}.wic.bz2 /dev/sdX", "emmc": "CM4 only: rpiboot, then dd {img}.wic to the exposed eMMC device", "spi": "rpi-eeprom-update (bootloader EEPROM only)"},
    "am625": {"sd": "sudo bmaptool copy {img}.wic.xz /dev/sdX", "emmc": "dfu-util -R -a bootloader -D tiboot3.bin  # then write the wic image to eMMC", "spi": "dfu-util -a ospi.tiboot3 -D tiboot3.bin"},
}
RAW_BOOT = {
    "imx8mm": 'part --source rawcopy --sourceparams="file=imx-boot" --ondisk mmcblk0 --no-table --align 32',
    "rk3568": 'part idbloader --source rawcopy --sourceparams="file=idblock.bin" --ondisk mmcblk0 --no-table --align 32\npart --source rawcopy --sourceparams="file=u-boot.itb" --ondisk mmcblk0 --no-table --align 8192',
}


# Kernel / U-Boot recipe names per SoC for the vendor and community-mainline choices.
# 'linux-yocto' is always the reference kernel when chosen explicitly. Data, so new SoCs need no code.
KERNEL_RECIPE = {
    "imx8mm": {"vendor": "linux-imx", "mainline-lts": "linux-fslc"}, "imx6ull": {"vendor": "linux-imx", "mainline-lts": "linux-fslc"},
    "am335x": {"vendor": "linux-ti-staging", "mainline-lts": "linux-ti-staging"}, "am625": {"vendor": "linux-ti-staging", "mainline-lts": "linux-ti-staging"},
    "stm32mp157": {"vendor": "linux-stm32mp", "mainline-lts": "linux-stm32mp"},
    "rk3568": {"vendor": "linux-rockchip", "mainline-lts": "linux-yocto"}, "bcm2711": {"vendor": "linux-raspberrypi", "mainline-lts": "linux-raspberrypi"},
    "bcm2712": {"vendor": "linux-raspberrypi", "mainline-lts": "linux-raspberrypi"},
}
UBOOT_RECIPE = {"imx8mm": "u-boot-imx", "imx6ull": "u-boot-fslc", "am335x": "u-boot-ti-staging", "am625": "u-boot-ti-staging",
                "stm32mp157": "u-boot-stm32mp", "rk3568": "u-boot-rockchip", "bcm2711": "u-boot", "bcm2712": "u-boot"}


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "project"


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


@dataclass
class StageResult:
    stage: str
    state: str = "done"  # done | warning | failed | skipped
    message: str = ""
    issue_uid: str = ""  # UI-P5: links failures back to the project choice


class GenerationError(Exception):
    def __init__(self, stage: str, message: str, uid: str = "", excerpt: str = ""):
        super().__init__(message)
        self.stage, self.uid, self.excerpt = stage, uid, excerpt


class Generator:
    def __init__(self, cat: Catalog, project: dict, outdir: str | Path, *, policy: str = "merge",
                 log: Callable[[str, str, str], None] | None = None, offline: bool = False, fetch: bool = False):
        self.cat, self.p, self.out = cat, project, Path(outdir)
        self.g = CapabilityGraph(cat)
        self.policy = policy  # merge | keep | replace  (GEN-12)
        self._log = log or (lambda stage, level, msg: None)
        self.offline, self.do_fetch = offline, fetch
        from .discover import load_layers
        self.layers = load_layers()
        self.soc = cat.get("soc", project["soc"]) or {}
        self.board = cat.get("board", project["board"]) if project.get("board") else None
        self.slug = slug(project["name"])
        self.layer_name = f"meta-{self.slug}"
        self.machine = self.slug
        self.rel = cat.get("release", project["release"]) or {}
        self.unpack = "WORKDIR" if project["release"] == "scarthgap" else "UNPACKDIR"
        self.old_manifest = self._read_old_manifest()
        self.files: dict[str, str] = {}  # relative path -> sha256 of what YBSG generated (a user-edited file keeps differing from it, so the edit stays detected)
        self.changes = {"added": [], "changed": [], "unchanged": [], "removed": [], "user_kept": [], "needs_merge": []}
        self.pins: dict[str, dict] = {}
        self.kconfig: dict[str, list[str]] = {}
        self.dts_text = ""
        self.result: dict[str, StageResult] = {}
        self.issues: list[dict] = []

    # ---- helpers ------------------------------------------------------------------------------
    def log(self, stage: str, msg: str, level: str = "info") -> None:
        self._log(stage, level, msg)

    def _read_old_manifest(self) -> dict:
        f = self.out / "bsp-manifest.json"
        try:
            return json.loads(f.read_text()) if f.exists() else {}
        except Exception:
            return {}

    def emit(self, rel: str, content: str | bytes, *, executable: bool = False) -> str:
        """Write a generated file. Reuses identical content; never silently overwrites user edits (GEN-12)."""
        data = content.encode() if isinstance(content, str) else content
        new_hash = sha(data)
        target = self.out / rel
        old_hash = self.old_manifest.get("files", {}).get(rel, {}).get("sha256")
        if target.exists():
            cur = sha(target.read_bytes())
            if cur == new_hash:
                self.changes["unchanged"].append(rel)
            elif old_hash is None or cur == old_hash or self.policy == "replace":
                target.write_bytes(data)
                self.changes["changed"].append(rel)
            elif self.policy == "keep":
                self.changes["user_kept"].append(rel)
                self.log("package", f"Kept your edited file {rel}", "warning")
            else:  # merge: leave the user's file, write ours next to it for side-by-side merging
                (self.out / (rel + ".ybsg-new")).write_bytes(data)
                self.changes["needs_merge"].append(rel)
                self.log("package", f"{rel} was edited; wrote {rel}.ybsg-new to merge", "warning")
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            self.changes["added"].append(rel)
        if executable:
            try:
                target.chmod(0o755)
            except OSError:
                pass
        self.files[rel] = new_hash
        return rel

    def _soc_label(self, ctrl: dict) -> str:
        lab = self.soc.get("dt_labels", {}).get(ctrl["name"], ctrl["name"].split()[0].lower())
        slot = ctrl.get("slot", 0)
        return lab.replace("{n1}", str(slot + 1)).replace("{n}", str(slot))

    def _ctrl_for(self, uid: str, assign: dict) -> dict:
        name = assign.get(uid)
        for c in self.g.controllers(self.p, "usb_host") + self.g.controllers(self.p, "mmc") + self.g.controllers(self.p, "flash") + \
                self.g.controllers(self.p, "csi") + self.g.controllers(self.p, "display") + self.g.controllers(self.p, "net") + \
                self.g.controllers(self.p, "audio") + self.g.controllers(self.p, "ram"):
            if c["instance"] == name:
                return c
        return {}

    def project_hash(self) -> str:
        q = {k: v for k, v in self.p.items() if k not in ("modified", "created", "uid", "pins")}
        return sha(json.dumps(q, sort_keys=True).encode())

    # =========================== stage 1: validate ===========================================
    def stage_validate(self) -> StageResult:
        v = self.g.validate(self.p)
        self.issues = v["issues"]
        self.assign = v["assignment"]
        for i in v["issues"]:
            if i["severity"] in ("error", "warn") and not i.get("waived"):
                self.log("validate", f"{i['severity'].upper()} {i['code']}: {i['message']}", "error" if i["severity"] == "error" else "warning")
        if not v["can_generate"]:
            first = next(i for i in v["issues"] if i["severity"] == "error" and i["blocking"] and not i.get("waived"))
            raise GenerationError("validate", f"{v['blocking']} blocking issue(s); first: {first['message']}", first["uid"])
        warn = sum(1 for i in v["issues"] if i["severity"] == "warn")
        return StageResult("validate", "warning" if warn else "done", f"valid; {warn} warning(s), {sum(1 for i in v['issues'] if i.get('waived'))} waived")

    # =========================== stage 2: resolve ============================================
    def _bsp_layers(self) -> list[str]:
        """Layers the board or SoC asks for directly (LAYERDEPENDS of the generated layer)."""
        want = list(self.soc.get("layers", [])) + (list(self.board.get("layers", [])) if self.board else [])
        if self.p["image"] == "qt":
            want.append("meta-qt6")
        return list(dict.fromkeys(want))

    def _layer_repo(self, name: str) -> str:
        r = self.layers["layer_catalog"][name]["repo"]
        return r if isinstance(r, str) else r.get(self.p["release"], r["default"])

    def layer_plan(self) -> list[str]:
        """Base layers + BSP layers + everything they need, in dependency order (data: layers.json layer_catalog)."""
        cat_, order, seen = self.layers["layer_catalog"], [], set()

        def visit(n: str) -> None:
            if n in seen:
                return
            seen.add(n)
            if n not in cat_:
                self.log("resolve", f"Layer {n} is not in layers.json; add it to layer_catalog", "warning")
                return
            for d in cat_[n]["needs"]:
                visit(d)
            order.append(n)

        for n in self.layers["base_layers"] + self._bsp_layers():
            visit(n)
        return order

    def _repos_needed(self) -> dict[str, list[str]]:
        """repo -> layer paths, plus companion repos that carry no layer (bitbake next to openembedded-core)."""
        out: dict[str, list[str]] = {}
        for n in self.layer_plan():
            out.setdefault(self._layer_repo(n), []).append(self.layers["layer_catalog"][n]["path"])
        for repo in list(out):
            for comp in self.layers.get("companions", {}).get(repo, []):
                out.setdefault(comp, [])
        return out

    def _ls_remote(self, url: str, ref: str) -> str | None:
        if self.offline:
            return None
        try:
            out = subprocess.run(["git", "ls-remote", url, ref], capture_output=True, text=True, timeout=20, check=True).stdout
            return out.split()[0] if out.strip() else None
        except Exception:
            return None

    def stage_resolve(self) -> StageResult:
        rel = self.p["release"]
        unresolved = []
        for repo, repo_layers in self._repos_needed().items():
            meta = self.layers["repos"][repo]
            branch = meta["branches"].get(rel)
            if not branch:
                raise GenerationError("resolve", f"{repo} has no branch for release {rel} in layers.json")
            have = self.p.get("pins", {}).get(repo, {})
            commit = have.get("commit") if have.get("branch") == branch else None
            if not commit:
                commit = self._ls_remote(meta["url"], f"refs/heads/{branch}")
            if commit:
                self.log("resolve", f"{repo}@{branch} pinned to {commit[:12]}")
            else:
                unresolved.append(repo)
                self.log("resolve", f"{repo}@{branch} could not be pinned (offline or unreachable); kas file will track the branch", "warning")
            self.pins[repo] = {"url": meta["url"], "branch": branch, "commit": commit or "", "layers": repo_layers, "licence_gate": meta.get("licence_gate", "")}
            if commit:
                self.p.setdefault("pins", {})[repo] = {"branch": branch, "commit": commit}
        if unresolved:
            return StageResult("resolve", "warning", f"{len(unresolved)} source(s) unpinned: {', '.join(unresolved)}. Re-run online to pin (GEN-2).")
        return StageResult("resolve", "done", f"{len(self.pins)} sources pinned")

    # =========================== stage 3: fetch ==============================================
    def stage_fetch(self) -> StageResult:
        if not self.do_fetch:
            return StageResult("fetch", "skipped", "Fetch is done by the build container; generation uses cached or no sources")
        if self.offline:
            return StageResult("fetch", "skipped", "Offline: using cached layers only (NFR-12)")
        if not shutil.which("git"):
            return StageResult("fetch", "warning", "git not found; cannot fetch")
        cache = paths.cache_dir() / "layers"
        cache.mkdir(parents=True, exist_ok=True)
        failed = []
        for repo, meta in self.pins.items():
            dest = cache / repo
            try:
                if not dest.exists():
                    subprocess.run(["git", "clone", "--filter=blob:none", "--no-checkout", meta["url"], str(dest)], check=True, capture_output=True, timeout=600)
                if meta["commit"]:
                    subprocess.run(["git", "-C", str(dest), "fetch", "origin", meta["commit"]], check=True, capture_output=True, timeout=600)
                self.log("fetch", f"cached {repo}")
            except Exception as e:
                failed.append(repo)
                self.log("fetch", f"could not fetch {repo}: {getattr(e, 'stderr', e)}", "warning")
        return StageResult("fetch", "warning" if failed else "done", f"{len(self.pins) - len(failed)} of {len(self.pins)} cached")

    # =========================== stage 4: layer ==============================================
    def _features(self) -> list[str]:
        cats = {x["category"] for x in self.p["peripherals"]}
        feat = ["ext2", "serial"]
        if cats & {"usb_host"}: feat.append("usbhost")
        if "usb_device" in cats: feat.append("usbgadget")
        if "audio" in cats: feat.append("alsa")
        if "display" in cats: feat.append("screen")
        if "camera" in cats: feat.append("v4l2")
        if any(x["ic"] == "sdio-wifi-bcm43455" for x in self.p["peripherals"]): feat.append("wifi")
        return sorted(set(feat))

    def _image_packages(self) -> list[str]:
        cats = {x["category"] for x in self.p["peripherals"]}
        pk = ["packagegroup-core-boot", "kernel-modules", "i2c-tools", "usbutils", "util-linux"]
        if "camera" in cats: pk += ["v4l-utils", "media-ctl", "libcamera"]
        if "flash" in cats: pk += ["mtd-utils", "mtd-utils-ubifs"]
        if "audio" in cats: pk += ["alsa-utils"]
        if "usb_device" in cats: pk += ["usb-gadget-setup"]
        if "net" in cats: pk += ["iproute2", "ethtool"]
        if any(x["ic"] == "sdio-wifi-bcm43455" for x in self.p["peripherals"]): pk += ["iw", "wpa-supplicant", "linux-firmware-bcm43455"]
        if self.p["image"] == "graphics": pk += ["weston", "weston-init", "wayland"]
        if self.p["image"] == "qt": pk += ["qtbase", "qtwayland", "qtdeclarative", "weston", "weston-init"]
        return sorted(set(pk))

    def stage_layer(self) -> StageResult:
        L, s, rel = self.layer_name, self.slug, self.p["release"]
        series = self.layers["layer_series"].get(rel, rel)
        lc = self.layers["layer_catalog"]
        bsp = set(self._bsp_layers())
        # direct BSP layers and everything they need (yocto-check-layer adds exactly these when it tests this layer)
        need, todo = set(), [x for x in bsp if x in lc]
        while todo:
            x = todo.pop()
            if x not in need:
                need.add(x); todo += [d for d in lc[x]["needs"] if d in lc]
        deps = sorted(dict.fromkeys(["core", "openembedded-layer"] + [lc[x]["collection"] for x in need]))
        self.emit(f"{L}/conf/layer.conf", f'''# Generated by YBSG {__version__}. Layer for {self.p["name"]}.
BBPATH .= ":${{LAYERDIR}}"
BBFILES += "${{LAYERDIR}}/recipes-*/*/*.bb ${{LAYERDIR}}/recipes-*/*/*.bbappend"
BBFILE_COLLECTIONS += "{s}"
BBFILE_PATTERN_{s} = "^${{LAYERDIR}}/"
BBFILE_PRIORITY_{s} = "10"
LAYERVERSION_{s} = "1"
LAYERSERIES_COMPAT_{s} = "{series}"
LAYERDEPENDS_{s} = "{' '.join(deps)}"
''')
        self.layer_deps = deps
        self.emit(f"{L}/COPYING.MIT", f"MIT License\n\nCopyright (c) 2026 {brand()['company']}\n\nPermission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the \"Software\"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:\n\nThe above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.\n\nTHE SOFTWARE IS PROVIDED \"AS IS\", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED.\n")
        self.emit(f"{L}/README.md", f"# {L}\n\nBoard support layer for **{self.p['name']}** ({self.soc.get('name')}), generated by YBSG {__version__}.\n\n- Release: {self.rel.get('name', rel)} {self.rel.get('version', '')}\n- Machine: `{self.machine}`\n- Mode: {'thin overlay on ' + str(self.soc.get('layers')) if self._overlay() else 'from scratch'}\n\nMaintainer: {self.p.get('author') or brand()['designer']}\n")
        self.emit(f"{L}/conf/templates/default/conf-notes.txt", f"Generated by YBSG. Build the image with: bitbake {s}-image\n")
        self.emit(f"{L}/conf/templates/default/bblayers.conf.sample", self._bblayers_sample())
        self.emit(f"{L}/conf/templates/default/local.conf.sample", f'MACHINE ??= "{self.machine}"\nDISTRO ?= "poky"\nPACKAGE_CLASSES ?= "package_ipk"\n'
                  '# Development image: root login without a password. Remove for production.\n'
                  f'EXTRA_IMAGE_FEATURES ?= "{self._dev_features()}"\n'
                  'DL_DIR ?= "${TOPDIR}/../cache/downloads"\nSSTATE_DIR ?= "${TOPDIR}/../cache/sstate"\nCONF_VERSION = "2"\n')
        self.emit(f"{L}/conf/machine/{self.machine}.conf", self._machine_conf())
        self.emit(f"{L}/recipes-core/images/{s}-image.bb", self._image_recipe())
        wic_dir = "wic" if self.p["release"] == "scarthgap" else "files/wic"  # OE-core sanity check from Whinlatter on
        self.emit(f"{L}/{wic_dir}/{s}.wks", self._wks())
        if any(x["category"] == "usb_device" for x in self.p["peripherals"]) or self.p.get("custom_usb"):
            self._gadget_recipe()
        self.emit(f"kas/{s}.yml", self._kas())
        self.emit("build.sh", self._build_sh(), executable=True)
        self.emit("FLASHING.md", self._flashing_guide())
        if self.p["image"] == "distro":
            self._distro_rootfs()
        return StageResult("layer", "done", f"{L} written")

    def _overlay(self) -> bool:
        return self.p.get("overlay_mode", "overlay") == "overlay" and bool(self.board and self.board.get("layers")) and bool(self.board.get("machine"))

    def _dev_features(self) -> str:
        # debug-tweaks was removed after Scarthgap; an image that still asks for it is skipped by BitBake
        return "debug-tweaks" if self.p["release"] == "scarthgap" else "allow-empty-password allow-root-login empty-root-password"

    def _build_sh(self) -> str:
        """Plain BitBake build, no kas and no container: the usual oe-init-build-env + bitbake, at the pinned commits."""
        core = self._layer_repo("meta")
        q = lambda s_: "'" + s_.replace("'", "'\\''") + "'"
        lines = [f"#!/bin/bash", f"# Build {self.p['name']} with plain BitBake (no kas, no container). Generated by YBSG {__version__}.",
                 "# Run from anywhere:  ./build.sh            (builds the image)", f"#                    ./build.sh -c fetch {self.slug}-image   (any bitbake arguments)",
                 "# Needs git, python3 and the Yocto host packages: https://docs.yoctoproject.org/ref-manual/system-requirements.html",
                 "set -e", 'cd "$(dirname "$0")"', 'TOP="$PWD"', "",
                 "fetch() {  # name url branch commit",
                 '  [ -d "sources/$1/.git" ] || git clone -q --no-checkout "$2" "sources/$1"',
                 '  if [ -n "$4" ]; then',
                 '    git -C "sources/$1" fetch -q --depth 1 origin "$4" 2>/dev/null || git -C "sources/$1" fetch -q origin "$3"',
                 '    git -C "sources/$1" checkout -q "$4"',
                 "  else",
                 '    git -C "sources/$1" fetch -q origin "$3" && git -C "sources/$1" checkout -q FETCH_HEAD',
                 "  fi", "}", "mkdir -p sources"]
        for repo, m in self.pins.items():
            lines.append(f"fetch {repo} {q(m['url'])} {q(m['branch'])} {q(m['commit'] or '')}" + ("" if m["commit"] else "  # UNPINNED: tracks the branch"))
        lines += ["", f'export TEMPLATECONF="$TOP/{self.layer_name}/conf/templates/default"']
        if "bitbake" in self.pins:
            lines.append('export BITBAKEDIR="$TOP/sources/bitbake"')
        if self.p["release"] != "scarthgap":  # a build folder made by an older YBSG may still ask for the removed debug-tweaks
            lines.append(f'[ -f build/conf/local.conf ] && sed -i \'s/"debug-tweaks"/"{self._dev_features()}"/\' build/conf/local.conf')
        lines += ["set +e", f'. "$TOP/sources/{core}/oe-init-build-env" "$TOP/build" > /dev/null || exit 1', "set -e",
                  f'bitbake "${{@:-{self.slug}-image}}"', ""]
        return "\n".join(lines)

    def _bblayers_sample(self) -> str:
        lines = ["POKY_BBLAYERS_CONF_VERSION = \"2\"", "BBPATH = \"${TOPDIR}\"", "BSPDIR := \"${@os.path.abspath(os.path.dirname(d.getVar('FILE')) + '/../..')}\"", "", "BBLAYERS ?= \" \\"]
        for repo, m in self.pins.items():
            for l in m["layers"]:
                lines.append(f"  ${{BSPDIR}}/sources/{repo}" + ("" if l == "." else f"/{l}") + " \\")
        lines.append(f"  ${{BSPDIR}}/{self.layer_name} \\")
        lines.append('"')
        return "\n".join(lines) + "\n"

    def _machine_conf(self) -> str:
        s = self.soc
        feats = " ".join(self._features())
        head = f"#@TYPE: Machine\n#@NAME: {self.p['name']}\n#@DESCRIPTION: {self.p['name']} on {s.get('name')} (generated by YBSG {__version__})\n\n"
        dtb = f"{s.get('dtb_dir', '.')}/{self.slug}.dtb"
        con = s.get("dt_console", "ttyS0,115200")
        baud, tty = con.split(",")[1], con.split(",")[0]
        if self._overlay():
            return head + f'''# Thin overlay (GEN-3): reuse the vendor/community machine and override only what the project changes.
require conf/machine/{self.board["machine"]}.conf
{self._kernel_conf()}
# ":{self.machine}" beats the vendor's override-qualified values (the machine name is the last MACHINEOVERRIDES entry)
KERNEL_DEVICETREE:append:{self.machine} = " {dtb}"
SERIAL_CONSOLES:{self.machine} = "{baud};{tty}"
{"" if s.get("keep_vendor_wks") else f'WKS_FILE:{self.machine} = "{self.slug}.wks"' + chr(10)}{self._machine_extra()}{self._provider_lines()}MACHINE_FEATURES:append = " {feats}"
MACHINE_ESSENTIAL_EXTRA_RRECOMMENDS:append = " kernel-modules"
'''
        arch = s.get("arch", "arm64")
        tune = TUNE.get(s["id"], "arm/armv8a/tune-cortexa53.inc")
        return head + f'''# From-scratch machine. Review against your SoC vendor layer before use.
require conf/machine/include/{tune}

MACHINEOVERRIDES =. "{s.get('family', s['id'])}:"
PREFERRED_PROVIDER_virtual/kernel ?= "{self._kernel_recipe_name()}"
PREFERRED_PROVIDER_virtual/bootloader ?= "u-boot"
KERNEL_IMAGETYPE = "{'Image' if arch == 'arm64' else 'zImage'}"
KERNEL_DEVICETREE = "{dtb}"
UBOOT_MACHINE = "{s.get('uboot_defconfig', '')}"
SERIAL_CONSOLES = "{baud};{tty}"
MACHINE_FEATURES = "{feats}"
IMAGE_FSTYPES = "wic wic.bmap ext4 tar.xz"
WKS_FILE = "{self.slug}.wks"
IMAGE_BOOT_FILES = "${{KERNEL_IMAGETYPE}} ${{KERNEL_DEVICETREE}} boot.scr"
'''

    def _machine_extra(self) -> str:
        """SoC-specific machine lines from the catalogue, e.g. Raspberry Pi config.txt `device_tree=` so the firmware loads our DTB."""
        lines = [x.format(machine=self.machine, slug=self.slug) for x in self.soc.get("machine_extra", [])]
        return "".join(l + "\n" for l in lines)

    def _provider_lines(self) -> str:
        """Pin the providers the generated .bbappend files extend, only where the SoC data says which ones the chosen
        kernel uses (verified against the vendor layer); otherwise the vendor machine's own choice stands."""
        if not self.soc.get("pin_providers") or self.p["kernel"] not in self.soc.get("kernel_conf", {}):
            return ""
        k, u, m = self._kernel_recipe_name(), self._uboot_recipe_name(), self.machine
        return (f'PREFERRED_PROVIDER_virtual/kernel:{m} = "{k}"\nPREFERRED_PROVIDER_virtual/bootloader:{m} = "{u}"\n'
                f'PREFERRED_PROVIDER_u-boot:{m} = "{u}"\n')

    def _kernel_conf(self) -> str:
        """Vendor switches that select the kernel/U-Boot for the chosen kernel (seed: soc.kernel_conf), e.g. meta-ti's BSP profile."""
        lines = self.soc.get("kernel_conf", {}).get(self.p["kernel"], [])
        return ("\n# Kernel choice " + self.p["kernel"] + "\n" + "\n".join(lines) + "\n") if lines else ""

    def _kernel_recipe_name(self) -> str:
        k = self.p["kernel"]
        if k == "linux-yocto":
            return "linux-yocto"
        kind = "vendor" if k == "vendor" else "mainline-lts"
        return KERNEL_RECIPE.get(self.soc["id"], {}).get(kind, "linux-yocto")

    def _uboot_recipe_name(self) -> str:
        return UBOOT_RECIPE.get(self.soc["id"], "u-boot")

    def _image_recipe(self) -> str:
        pk = " ".join(self._image_packages())
        base = {"minimal": "core-image-minimal", "graphics": "core-image-weston", "qt": "core-image-weston",
                "kernel-only": "core-image-minimal", "distro": "core-image-minimal"}[self.p["image"]]
        extra = ""
        if self.p["image"] == "kernel-only":
            extra = '\n# Kernel + bootloader only: no extra userland.\nIMAGE_INSTALL = "packagegroup-core-boot"\nIMAGE_FEATURES = ""\n'
        elif self.p["image"] == "distro":
            extra = '\n# Distribution target: this image supplies the bootloader, kernel, DTB and modules.\n# The rootfs comes from rootfs/assemble-rootfs.sh and is NOT Yocto-built (GEN-9).\nIMAGE_INSTALL = "packagegroup-core-boot kernel-modules"\nIMAGE_FEATURES = ""\n'
        else:
            extra = f'\nIMAGE_INSTALL:append = " {pk}"\n'
        return f'''SUMMARY = "{self.p["name"]} image ({self.p["image"]})"
LICENSE = "MIT"
require recipes-core/images/{base}.bb
{extra}
IMAGE_FSTYPES += "wic wic.bmap"
WKS_FILE ?= "{self.slug}.wks"
'''

    def _wks(self) -> str:
        raw = RAW_BOOT.get(self.soc["id"], "")
        return f'''# Flashable image layout for {self.p["name"]}. Generated by YBSG {__version__}.
# Bootloader placement is SoC specific: see FLASHING.md.
{raw}
part /boot --source bootimg-partition --ondisk mmcblk0 --fstype=vfat --label boot --active --align 4096 --size 64M
part / --source rootfs --ondisk mmcblk0 --fstype=ext4 --label root --align 4096
bootloader --ptable msdos
'''.replace("\n\n\n", "\n")

    def _kas(self) -> str:
        rel = self.p["release"]
        out = [f"# kas project file generated by YBSG {__version__}.", "# Build from the output folder:  kas-container build kas/%s.yml" % self.slug, "header:", "  version: 14", f"machine: {self.machine}", "distro: poky", f"target: {self.slug}-image", "repos:"]
        for repo, m in self.pins.items():
            out += [f"  {repo}:", f"    url: {m['url']}"]
            if m["commit"]:
                out.append(f"    commit: {m['commit']}")
            else:
                out += [f"    branch: {m['branch']}  # UNPINNED: re-run YBSG online to pin a commit (GEN-2)"]
            out.append("    layers:")
            if not m["layers"]:  # tool repo such as bitbake: checked out, not a layer
                out.append("      .: disabled")
            for l in m["layers"]:
                out.append(f"      {l}:" if l != "." else "      .:")
        out += [f"  {self.layer_name}:", f"    path: {self.layer_name}  # relative to KAS_WORK_DIR: run kas from the output folder", "local_conf_header:", "  ybsg: |", f'    # {self.p["name"]}', "    BB_NUMBER_THREADS ?= \"${@oe.utils.cpu_count()}\"", '    PARALLEL_MAKE ?= "-j ${@oe.utils.cpu_count()}"',
                '    DL_DIR ?= "${TOPDIR}/../cache/downloads"', '    SSTATE_DIR ?= "${TOPDIR}/../cache/sstate"']
        return "\n".join(out) + "\n"

    def _gadget_recipe(self) -> None:
        funcs = []
        for x in self.p["peripherals"]:
            if x["category"] == "usb_device":
                funcs.append(x.get("props", {}).get("function", "serial"))
        for c in self.p.get("custom_usb", []):
            funcs.append("custom:" + c.get("name", "func"))
        sh = ["#!/bin/sh", "# USB gadget via ConfigFS, generated by YBSG (USB device section).", "set -e", "modprobe libcomposite", "mount -t configfs none /sys/kernel/config 2>/dev/null || true",
              f"G=/sys/kernel/config/usb_gadget/{self.slug}", "mkdir -p $G", "echo 0x1d6b > $G/idVendor", "echo 0x0104 > $G/idProduct", "mkdir -p $G/strings/0x409 $G/configs/c.1/strings/0x409",
              f'echo "{self.p["name"]}" > $G/strings/0x409/product', f'echo "{brand()["company"]}" > $G/strings/0x409/manufacturer', 'echo "Config 1" > $G/configs/c.1/strings/0x409/configuration']
        for n, f in enumerate(funcs):
            if f.startswith("custom:"):
                nm = f.split(":", 1)[1]
                sh += [f"mkdir -p $G/functions/ffs.{nm}", f"ln -sf $G/functions/ffs.{nm} $G/configs/c.1/", f"# FunctionFS custom function '{nm}': mount it at /dev/ffs-{nm} and start your daemon before binding the UDC"]
            else:
                fn = {"mass_storage": "mass_storage.0", "serial": "acm.GS0", "ecm": "ecm.usb0", "ncm": "ncm.usb0", "rndis": "rndis.usb0", "hid": "hid.usb0", "uvc": "uvc.0", "midi": "midi.0", "composite": "acm.GS0"}.get(f, "acm.GS0")
                sh += [f"mkdir -p $G/functions/{fn}", f"ln -sf $G/functions/{fn} $G/configs/c.1/"]
        sh += ['UDC=$(ls /sys/class/udc | head -n1)', 'echo "$UDC" > $G/UDC']
        d = f"{self.layer_name}/recipes-support/usb-gadget-setup"
        self.emit(f"{d}/files/usb-gadget-setup.sh", "\n".join(sh) + "\n", executable=True)
        self.emit(f"{d}/usb-gadget-setup_1.0.bb", f'''SUMMARY = "USB gadget setup via ConfigFS"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${{COMMON_LICENSE_DIR}}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"
SRC_URI = "file://usb-gadget-setup.sh"
S = "${{{self.unpack}}}"
do_install() {{
    install -d ${{D}}${{sbindir}}
    install -m 0755 ${{{self.unpack}}}/usb-gadget-setup.sh ${{D}}${{sbindir}}/usb-gadget-setup
}}
''')
        self.gadget_functions = funcs

    def _flashing_guide(self) -> str:
        f = FLASH_CMDS.get(self.soc["id"], {})
        img = f"{self.slug}-image-{self.machine}"
        fm = lambda t: t.format(img=img, machine=self.machine)
        return f'''# Flashing guide: {self.p["name"]}

Generated by YBSG {__version__}. Commands are templates: replace `/dev/sdX` with your card reader
(check with `lsblk`; the wrong device will destroy data).

Boot chain: {self.soc.get("boot", "see SoC reference manual")}

## SD card
```
{fm(f.get("sd", "sudo dd if=%s.wic of=/dev/sdX bs=4M conv=fsync status=progress" % img))}
```
## eMMC (through USB or from a booted system)
```
{fm(f.get("emmc", "see vendor documentation"))}
```
## SPI flash
```
{fm(f.get("spi", "see vendor documentation"))}
```
## After flashing
1. Insert the media, connect a serial console at {self.soc.get("dt_console", "115200")} 8N1.
2. Power on and confirm U-Boot, then the kernel, reach a login prompt.
3. Check `dmesg` for each selected peripheral driver listed in the PDF report.

Note: {self.soc.get("dram", "")}
'''

    def _distro_rootfs(self) -> None:
        d = self.p.get("distro") or "debian"
        meta = self.layers["distro_rootfs"].get(d, {})
        soc_arch = "arm64" if self.soc.get("arch") == "arm64" else "armhf"
        arch = meta.get("arch", {}).get(soc_arch)
        if not arch:
            raise GenerationError("layer", f"{d} has no {soc_arch} root filesystem; choose another distribution")
        m, suite, mirror, M = meta["method"], meta["suite"], meta["mirror"], self.machine
        qemu = {"arm64": "aarch64", "armhf": "arm"}[soc_arch]
        emul = f"""# {meta['tool']} runs target binaries: on a non-ARM host this needs qemu-user binfmt support
if [ "$(uname -m)" != "{ {'arm64': 'aarch64', 'armhf': 'armv7l'}[soc_arch] }" ] && [ ! -e /proc/sys/fs/binfmt_misc/qemu-{qemu} ]; then
  echo "Install qemu-user-static and binfmt-support (Debian/Ubuntu) or qemu-user-static (Fedora) first." >&2; exit 1
fi
"""
        fetch = {
            "debootstrap": emul + f'sudo debootstrap --arch={arch} {suite} "$ROOT" {mirror}',
            "mmdebstrap": emul + f'sudo mmdebstrap --arch={arch} {suite} "$ROOT" {mirror}',
            "dnf": emul + f"""sudo dnf -y --installroot="$ROOT" --forcearch={arch} --releasever={suite} \\
  --repofrompath=ybsg-fedora,{mirror}/{suite}/Everything/{arch}/os/ --repo=ybsg-fedora \\
  --setopt=ybsg-fedora.gpgcheck=1 --setopt=ybsg-fedora.gpgkey={meta.get('gpgkey', '')} \\
  --setopt=install_weak_deps=False install @core""",
            "archarm-tarball": f"""T=ArchLinuxARM-{arch}-latest.tar.gz
curl -fLO {mirror}/$T && curl -fLO {mirror}/$T.md5
md5sum -c $T.md5   # the mirror also publishes $T.sig for gpg --verify with the Arch Linux ARM build key
sudo install -d "$ROOT" && sudo bsdtar -xpf $T -C "$ROOT" """.rstrip(),
            "alpine-minirootfs": f"""REL={mirror}/{suite}/releases/{arch}
F=$(curl -fsSL $REL/latest-releases.yaml | awk '/file: alpine-minirootfs/ {{print $2; exit}}')
curl -fLO $REL/$F && curl -fL $REL/$F.sha256 -o $F.sha256
sha256sum -c $F.sha256
sudo install -d "$ROOT" && sudo tar -xzf $F -C "$ROOT" """.rstrip(),
        }[m]
        script = f'''#!/bin/sh
# Assembles a {d} rootfs around the Yocto-built bootloader, kernel and DTB (GEN-9).
# NOTE: this rootfs is NOT built by Yocto and is outside Yocto's SBOM and CVE tracking.
set -e
ROOT=${{1:-./rootfs-{d}}}
DEPLOY=${{2:-./build/tmp/deploy/images/{M}}}
{fetch}
# kernel modules from the Yocto build
sudo tar -xf "$DEPLOY"/modules-{M}.tgz -C "$ROOT"
# boot files: fail loudly if the build did not produce them
sudo install -d "$ROOT/boot"
for f in "$DEPLOY"/*Image "$DEPLOY"/{self.slug}.dtb "$DEPLOY"/boot.scr; do
  [ -e "$f" ] || {{ echo "missing $f: build the image first (kas build kas/{self.slug}.yml)" >&2; exit 1; }}
  sudo cp -L "$f" "$ROOT/boot/"
done
echo "Rootfs ready in $ROOT."
echo "Create the disk image (inside the kas shell): wic create {self.slug} -e {self.slug}-image --rootfs-dir \"$ROOT\""
'''
        self.emit("rootfs/assemble-rootfs.sh", script, executable=True)
        self.emit("rootfs/README.md", f"# {d} rootfs path\n\nThe bootloader, kernel and device tree come from Yocto. The {d} root filesystem is assembled by `assemble-rootfs.sh` with {meta.get('tool')} ({arch}) and **is not Yocto-built**: it is outside Yocto's SBOM and CVE tracking. Test the kernel modules against this userland on your board.\n")

    # =========================== stage 5: device tree ========================================
    def _dt_i2c(self, bus: str) -> str:
        m_ = re.search(r"(\d+)$", bus)
        n = m_.group(1) if m_ else "0"
        return self.soc.get("dt_i2c", "i2c{n}").format(n=n)

    def _bus_default(self) -> str:
        return {"imx8mm": "i2c2", "imx6ull": "i2c1", "am335x": "i2c2", "stm32mp157": "i2c1", "rk3568": "i2c4", "bcm2711": "i2c1", "bcm2712": "i2c1", "am625": "i2c1"}.get(self.soc["id"], "i2c0")

    def _node_comment(self, x: dict, indent: str = "") -> str:
        ic = self.cat.get("ic", x["ic"]) if x["ic"] else None
        return f"{indent}/* YBSG selection: {x['category']} / {ic['part'] if ic else 'custom IC'} / driver {x['driver'] or 'n/a'} */"

    def _board_wired(self) -> set[tuple[str, str]]:
        """(category, IC) pairs the board's base device tree already configures."""
        if not (self.board or {}).get("base_dts"):
            return set()
        return {(x["category"], x.get("ic", "")) for x in self.board.get("preset", [])}

    def stage_dts(self) -> StageResult:
        s, p = self.soc, self.p
        # a board whose SoC needs board-level wiring (Pi 5: RP1 sits under pcie2) starts from the vendor board file
        dtsi = (self.board or {}).get("base_dts") or Path(s.get("dtsi", "soc.dtsi")).name
        arm64 = s.get("arch") == "arm64"
        ram = p["ram"]
        base = int(s.get("ram_base", "0x0"), 16)
        size = int(ram["size_mb"]) * 1024 * 1024
        reg = f"<0x0 0x{base:x} 0x0 0x{size:x}>" if arm64 else f"<0x{base:x} 0x{size:x}>" if base < 2**32 else f"<0x{base:x} 0x{size:x}>"
        if arm64 and size >= 2**32:
            reg = f"<0x0 0x{base:x} 0x{size >> 32:x} 0x{size & 0xffffffff:x}>"
        out = [f"// Generated by YBSG {__version__} for {p['name']}.", "// The SoC include is referenced, never copied (GEN-4).",
               "// Node labels come from the catalog (confidence: %s); check them against your kernel with dtc." % s.get("label_confidence", "medium"),
               "/dts-v1/;", "", f'#include "{dtsi}"', "#include <dt-bindings/gpio/gpio.h>", "#include <dt-bindings/interrupt-controller/irq.h>", "",
               "/ {", f'\tmodel = "{p["name"]}";', f'\tcompatible = "ybsg,{self.slug}", "{s.get("dt_compat", "")}";', "",
               f"\t/* YBSG selection: RAM {ram.get('type')} {ram['size_mb']} MB, bus {ram.get('bus_width')} bit */", f"\tmemory@{base:x} {{", '\t\tdevice_type = "memory";', f"\t\treg = {reg};", "\t};", "};", ""]
        assign = getattr(self, "assign", None) or self.g.validate(p)["assignment"]
        i2c_blocks: dict[str, list[str]] = {}
        for x in p["peripherals"]:
            c = self._ctrl_for(x["uid"], assign)
            lab = self._soc_label(c) if c else ""
            ic = self.cat.get("ic", x["ic"]) if x["ic"] else None
            cat_, drv = x["category"], (self.cat.get("driver", x["driver"]) if x["driver"] else None)
            cm = self._node_comment(x)
            cmi = self._node_comment(x, "\t")  # inside an i2c bus block
            if lab and (cat_, x.get("ic")) in self._board_wired():
                # the vendor board file already wires this part (pins, PHY, card detect): only switch it on
                out += [cm.removesuffix(" */") + f", wired by {self.board['base_dts']} */", f"&{lab} {{", '\tstatus = "okay";', "};", ""]
                continue
            if cat_ in ("usb_host", "usb_device") and lab:
                role = x.get("mode") or ("peripheral" if cat_ == "usb_device" else "host")
                dr = {"device": "peripheral", "host": "host", "otg": "otg", "peripheral": "peripheral"}.get(role, "host")
                if cat_ == "usb_device" and "device" in role: dr = "peripheral"
                drd = bool({"otg", "device"} & set(c.get("modes", [])))
                out += [cm, f"&{lab} {{"] + ([f'\tdr_mode = "{dr}";'] if drd else []) + ['\tstatus = "okay";', "};", ""]
            elif cat_ == "mmc" and lab:
                emmc = bool(ic and ic["interface"] == "emmc")
                bw = 8 if emmc and "hs400" in " ".join(c.get("modes", [])) else 4
                sdio = bool(ic and ic["interface"] == "sdio")
                card = "\tnon-removable;" if (emmc or sdio) else "\tcd-gpios = <&gpio1 0 GPIO_ACTIVE_LOW>; /* adjust to your card-detect pin */"
                out += [cm, f"&{lab} {{", f"\tbus-width = <{bw}>;", card] + (["\tkeep-power-in-suspend;"] if sdio else []) + ['\tstatus = "okay";', "};", ""]
            elif cat_ == "net" and lab:
                mode = "rgmii-id" if ic and "rgmii" in ic["controller_modes"] and ic["interface"] == "rgmii" else ("rmii" if ic and ic["interface"] == "rmii" else "mii" if ic and ic["interface"] == "mii" else "rgmii-id")
                out += [cm, f"&{lab} {{", f'\tphy-mode = "{mode}";', f"\tphy-handle = <&ethphy_{x['uid']}>;", '\tstatus = "okay";', "\tmdio {", "\t\t#address-cells = <1>;", "\t\t#size-cells = <0>;", f"\t\tethphy_{x['uid']}: ethernet-phy@0 {{", "\t\t\treg = <0>;", "\t\t};", "\t};", "};", ""]
            elif cat_ == "display" and ic and ic["id"] == "native-hdmi" and lab:
                out += [cm, f"&{lab} {{", '\tstatus = "okay";', "};", ""]
            elif cat_ == "display" and lab and ic and ic["id"] != "native-hdmi":
                bus = x.get("props", {}).get("bus") or self._bus_default()
                addr = x.get("props", {}).get("address") or DEFAULT_I2C_ADDR.get(ic["id"], "0x39")
                comp = drv["compatible"] if drv else ic["id"]
                i2c_blocks.setdefault(bus, []).append(f"{cmi}\n\tbridge@{addr[2:]} {{\n\t\tcompatible = \"{comp}\";\n\t\treg = <{addr}>;\n\t}};")
                out += [cm, f"&{lab} {{", '\tstatus = "okay";', "};", ""]
            elif cat_ == "camera" and ic:
                bus = x.get("props", {}).get("bus") or self._bus_default()
                addr = x.get("props", {}).get("address") or DEFAULT_I2C_ADDR.get(ic["id"], "0x10")
                n_l = 4 if "4lane" in ic["interface"] else 2
                lanes = " ".join(str(i + 1) for i in range(n_l)) if "csi2" in ic["interface"] else ""
                prop = f"data-lanes = <{lanes}>;" if lanes else "bus-width = <8>;"
                comp = drv["compatible"] if drv else ic["id"]
                s_ep, c_ep = f"sensor_ep_{x['uid']}", f"csi_ep_{x['uid']}"
                i2c_blocks.setdefault(bus, []).append(
                    f"{cmi}\n\tcamera@{addr[2:]} {{\n\t\tcompatible = \"{comp}\";\n\t\treg = <{addr}>;\n\t\tport {{\n\t\t\t{s_ep}: endpoint {{\n\t\t\t\tremote-endpoint = <&{c_ep}>;\n\t\t\t\t{prop}\n\t\t\t}};\n\t\t}};\n\t}};")
                if lab:
                    out += [cm, f"&{lab} {{", '\tstatus = "okay";', "\tports {", "\t\t#address-cells = <1>;", "\t\t#size-cells = <0>;", "\t\tport@0 {", "\t\t\treg = <0>;", f"\t\t\t{c_ep}: endpoint {{", f"\t\t\t\tremote-endpoint = <&{s_ep}>;", f"\t\t\t\t{prop}", "\t\t\t};", "\t\t};", "\t};", "};", ""]
            elif cat_ == "flash" and ic and lab:
                parts = x.get("props", {}).get("partitions") or [{"name": "bootloader", "offset": 0, "size": 0x100000}, {"name": "env", "offset": 0x100000, "size": 0x20000}, {"name": "data", "offset": 0x120000, "size": 0xE0000}]
                nand = "nand" in ic["interface"]
                head = [cm, f"&{lab} {{", '\tstatus = "okay";', f"\tflash@0 {{" if not nand or "spi" in ic["interface"] else "\tnand@0 {"]
                comp = "spi-nand" if ic["interface"] == "spi-nand" else ("nand-chip" if nand and "spi" not in ic["interface"] else "jedec,spi-nor")
                head += [f'\t\tcompatible = "{comp}";', "\t\treg = <0>;", "\t\tspi-max-frequency = <50000000>;" if "spi" in ic["interface"] or "qspi" in ic["interface"] else "\t\tnand-ecc-mode = \"hw\";", "\t\t#address-cells = <1>;", "\t\t#size-cells = <1>;", "\t\tpartitions {", '\t\t\tcompatible = "fixed-partitions";', "\t\t\t#address-cells = <1>;", "\t\t\t#size-cells = <1>;"]
                for q in parts:
                    head += [f"\t\t\tpartition@{q['offset']:x} {{", f'\t\t\t\tlabel = "{q["name"]}";', f"\t\t\t\treg = <0x{q['offset']:x} 0x{q['size']:x}>;", "\t\t\t};"]
                out += head + ["\t\t};", "\t};", "};", ""]
            elif cat_ == "audio" and ic:
                bus = x.get("props", {}).get("bus") or self._bus_default()
                addr = x.get("props", {}).get("address") or DEFAULT_I2C_ADDR.get(ic["id"], "0x1a")
                comp = drv["compatible"] if drv else ic["id"]
                i2c_blocks.setdefault(bus, []).append(f"{cmi}\n\tcodec@{addr[2:]} {{\n\t\tcompatible = \"{comp}\";\n\t\treg = <{addr}>;\n\t\t#sound-dai-cells = <0>;\n\t}};")
                if lab:
                    out += [cm, f"&{lab} {{", '\t#sound-dai-cells = <0>;', '\tstatus = "okay";', "};", ""]
            elif cat_ == "i2c" and ic:
                bus = x.get("props", {}).get("bus") or self._bus_default()
                addr = x.get("props", {}).get("address") or x.get("custom", {}).get("address") or DEFAULT_I2C_ADDR.get(ic["id"], "0x50")
                comp = drv["compatible"] if drv else ic["id"]
                i2c_blocks.setdefault(bus, []).append(f"{cmi}\n\tsensor@{addr[2:]} {{\n\t\tcompatible = \"{comp}\";\n\t\treg = <{addr}>;\n\t}};")
        # custom ICs (FR-11)
        self.custom_kconfig: list[str] = []
        for ci in p.get("custom_ics", []):
            bus = ci.get("bus", self._bus_default())
            addr = ci.get("address", "0x50")
            body = [f"\t/* YBSG selection: custom IC {ci['id']} */", f"\t{ci['id'].replace('_', '-')}@{addr[2:]} {{", f'\t\tcompatible = "{ci["compatible"]}";', f"\t\treg = <{addr}>;"]
            if ci.get("interrupts"): body.append(f"\t\tinterrupts = <{ci['interrupts']}>;")
            if ci.get("gpios"): body.append(f"\t\tgpios = <{ci['gpios']}>;")
            if ci.get("clocks"): body.append(f"\t\tclocks = <{ci['clocks']}>;")
            for reg_ in (ci.get("regulators") or []):
                body.append(f"\t\t{reg_}-supply = <&{reg_}>;")
            body.append("\t};")
            i2c_blocks.setdefault(bus, []).append("\n".join(body))
            if ci.get("kconfig"):
                self.custom_kconfig += ci["kconfig"] if isinstance(ci["kconfig"], list) else [ci["kconfig"]]
        for bus in sorted(i2c_blocks):
            out += [f"&{self._dt_i2c(bus)} {{", "\t#address-cells = <1>;", "\t#size-cells = <0>;", '\tstatus = "okay";', ""] + i2c_blocks[bus] + ["};", ""]
        self.dts_text = "\n".join(out).replace("\n\n\n", "\n\n").rstrip() + "\n"
        errs = dts_syntax_check(self.dts_text)
        if errs:
            raise GenerationError("dts", "Generated device tree has syntax errors: " + "; ".join(errs[:3]), "", "\n".join(errs))
        d = s.get("dtb_dir", ".")
        self.emit(f"{self.layer_name}/recipes-kernel/linux/files/{self.slug}.dts", self.dts_text)
        self.emit(f"dts/{self.slug}.dts", self.dts_text)
        return StageResult("dts", "done", f"{self.slug}.dts: {self.dts_text.count('YBSG selection')} selections")

    # =========================== stage 6: kernel and bootloader config ========================
    def _closure(self, syms: list[str]) -> list[str]:
        seen, stack = set(), list(syms)
        while stack:
            s = stack.pop()
            if s in seen:
                continue
            seen.add(s)
            stack += KCONFIG_DEPS.get(s, []) + _plugins.KCONFIG_DEPS.get(s, [])
        return sorted(seen)

    def stage_config(self) -> StageResult:
        base = ["CONFIG_DEVTMPFS", "CONFIG_DEVTMPFS_MOUNT", "CONFIG_EXT4_FS", "CONFIG_VFAT_FS", "CONFIG_TMPFS", "CONFIG_PROC_FS", "CONFIG_SYSFS"]
        self.kconfig = {s: ["base"] for s in base}
        for x in self.p["peripherals"]:
            drv = self.cat.get("driver", x["driver"]) if x["driver"] else None
            syms = list(drv["kconfig"]) if drv else []
            if x["category"] == "usb_device":
                syms += ["CONFIG_USB_CONFIGFS", "CONFIG_USB_LIBCOMPOSITE", "CONFIG_USB_GADGET"] + GADGET_FUNCS.get(x.get("props", {}).get("function", "serial"), [])
            if x["category"] == "usb_host" and x.get("props", {}).get("class"):
                syms += USB_HOST_CLASS.get(x["props"]["class"], [])
            if x["category"] == "flash" and "nand" in (self.cat.get("ic", x["ic"]) or {}).get("interface", ""):
                syms += ["CONFIG_MTD_UBI", "CONFIG_UBIFS_FS"]
            for sym in self._closure(syms):
                self.kconfig.setdefault(sym, []).append(drv["name"] if drv and sym in drv["kconfig"] else x["category"])
        if self.p.get("custom_usb"):
            for sym in self._closure(["CONFIG_USB_CONFIGFS", "CONFIG_USB_CONFIGFS_F_FS", "CONFIG_USB_LIBCOMPOSITE", "CONFIG_USB_GADGET"]):
                self.kconfig.setdefault(sym, []).append("custom USB function")
        for sym in self._closure(self.custom_kconfig):
            self.kconfig.setdefault(sym, []).append("custom IC")
        if self.p["image"] in ("graphics", "qt"):
            for sym in ("CONFIG_DRM", "CONFIG_DRM_KMS_HELPER", "CONFIG_FB", "CONFIG_INPUT_EVDEV"):
                self.kconfig.setdefault(sym, []).append("graphics image")
        lines = [f"# Kernel config fragment generated by YBSG {__version__} for {self.p['name']}.", "# Symbols and the selection that needs them (DB-2); dependencies are resolved transitively."]
        for sym in sorted(self.kconfig):
            lines.append(f"{sym}=y  # {', '.join(sorted(set(self.kconfig[sym])))}")
        cfg = "\n".join(lines) + "\n"
        recipe = self._kernel_recipe_name()
        d = f"{self.layer_name}/recipes-kernel/linux"
        self.emit(f"{d}/files/{self.slug}.cfg", cfg)
        arch_dir, dtb_dir = self.soc.get("arch_dir", "arm64"), self.soc.get("dtb_dir", ".")
        self.emit(f"{d}/{recipe}_%.bbappend", f'''# Generated by YBSG {__version__}
FILESEXTRAPATHS:prepend := "${{THISDIR}}/files:"
SRC_URI:append = " file://{self.slug}.cfg file://{self.slug}.dts"
COMPATIBLE_MACHINE:append = "|{self.machine}"

# Device tree: copy into the kernel tree and register it in the Makefile.
do_configure:append() {{
    cp ${{{self.unpack}}}/{self.slug}.dts ${{S}}/arch/{arch_dir}/boot/dts/{dtb_dir}/
    grep -q "{self.slug}.dtb" ${{S}}/arch/{arch_dir}/boot/dts/{dtb_dir}/Makefile || \\
        echo "dtb-y += {self.slug}.dtb" >> ${{S}}/arch/{arch_dir}/boot/dts/{dtb_dir}/Makefile
}}
''')
        # patches list (GEN-6): the tool applies none by default; user patches are listed here
        patches = self.p.get("patches", [])
        self.emit(f"{d}/files/PATCHES.txt", "\n".join(patches) + ("\n" if patches else "# no patches applied by YBSG\n"))
        # U-Boot
        ram = self.p["ram"]
        ub = [f"# U-Boot config fragment generated by YBSG {__version__}", f"# RAM: {ram.get('type')} {ram['size_mb']} MB on a {ram.get('bus_width')}-bit bus.", f"# DRAM init: {self.soc.get('dram', '')}",
              "CONFIG_NR_DRAM_BANKS=1", "CONFIG_BOOTDELAY=1", "CONFIG_CMD_FS_GENERIC=y", "CONFIG_CMD_EXT4=y", "CONFIG_CMD_FAT=y", "CONFIG_USE_BOOTCOMMAND=y"]
        cats = {x["category"] for x in self.p["peripherals"]}
        if cats & {"usb_host"}: ub += ["CONFIG_USB=y", "CONFIG_CMD_USB=y", "CONFIG_USB_STORAGE=y"]
        if "mmc" in cats: ub += ["CONFIG_MMC=y", "CONFIG_CMD_MMC=y"]
        if "flash" in cats: ub += ["CONFIG_MTD=y", "CONFIG_DM_SPI_FLASH=y", "CONFIG_CMD_SF=y"]
        if "net" in cats: ub += ["CONFIG_NET=y", "CONFIG_CMD_DHCP=y", "CONFIG_CMD_PING=y"]
        ub = sorted(set(l for l in ub if not l.startswith("#")))
        hdr = [f"# U-Boot config fragment generated by YBSG {__version__}", f"# RAM {ram.get('type')} {ram['size_mb']} MB, {ram.get('bus_width')}-bit. DRAM init: {self.soc.get('dram', '')}"]
        self.emit(f"{self.layer_name}/recipes-bsp/u-boot/files/{self.slug}-uboot.cfg", "\n".join(hdr + ub) + "\n")
        self.emit(f"{self.layer_name}/recipes-bsp/u-boot/{self._uboot_recipe_name()}_%.bbappend", f'''# Generated by YBSG {__version__}
FILESEXTRAPATHS:prepend := "${{THISDIR}}/files:"
SRC_URI:append = " file://{self.slug}-uboot.cfg"
UBOOT_MACHINE ?= "{self.soc.get("uboot_defconfig", "")}"
''')
        con = self.soc.get("dt_console", "ttyS0,115200")
        kimg = "Image" if self.soc.get("arch") == "arm64" else "zImage"
        base_ = int(self.soc.get("ram_base", "0x0"), 16)
        self.emit(f"{self.layer_name}/recipes-bsp/u-boot-bootscript/files/boot.cmd", f'''# Boot script generated by YBSG {__version__}
setenv bootargs console={con} root=/dev/mmcblk${{mmcdev}}p2 rootwait rw
load ${{devtype}} ${{devnum}}:${{distro_bootpart}} 0x{base_ + 0x2000000:x} {kimg}
load ${{devtype}} ${{devnum}}:${{distro_bootpart}} 0x{base_ + 0x1000000:x} {self.slug}.dtb
{"booti" if self.soc.get("arch") == "arm64" else "bootz"} 0x{base_ + 0x2000000:x} - 0x{base_ + 0x1000000:x}
''')
        self.emit(f"{self.layer_name}/recipes-bsp/u-boot-bootscript/u-boot-bootscript_1.0.bb", f'''SUMMARY = "U-Boot boot script for {self.p["name"]}"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${{COMMON_LICENSE_DIR}}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"
DEPENDS = "u-boot-mkimage-native"
inherit deploy nopackages
SRC_URI = "file://boot.cmd"
S = "${{{self.unpack}}}"
do_compile() {{
    mkimage -A {'arm64' if self.soc.get('arch') == 'arm64' else 'arm'} -T script -C none -n "boot" -d ${{{self.unpack}}}/boot.cmd boot.scr
}}
do_deploy() {{
    install -m 0644 boot.scr ${{DEPLOYDIR}}/boot.scr
}}
addtask deploy after do_compile before do_build
''')
        return StageResult("config", "done", f"{len(self.kconfig)} kernel symbols, U-Boot fragment, boot script")

    # =========================== stage 7: dry-run parse ======================================
    def stage_dryrun(self) -> StageResult:
        notes, problems = [], []
        problems += layer_check(self.out / self.layer_name, self.p["release"], self.layers["layer_series"])
        notes.append("built-in layer conformance check" + (" failed" if problems else " passed"))
        errs = dts_syntax_check((self.out / "dts" / f"{self.slug}.dts").read_text())
        problems += errs
        notes.append("device-tree syntax check" + (" failed" if errs else " passed"))
        dtc = shutil.which("dtc")
        if dtc:
            tmp = self.out / ".ybsg-dtc.dts"
            stripped = "\n".join(l for l in self.dts_text.splitlines() if not l.startswith("#include")).replace("/dts-v1/;", "/dts-v1/;\n/plugin/;")
            tmp.write_text(stripped)
            r = subprocess.run([dtc, "-I", "dts", "-O", "dtb", "-@", "-o", "/dev/null" if shutil.which("true") else "nul", str(tmp)], capture_output=True, text=True)
            tmp.unlink(missing_ok=True)
            if r.returncode == 0:
                notes.append("dtc syntax check passed")
            else:
                self.log("dryrun", "dtc could not fully verify the standalone file (SoC include not available): " + r.stderr.strip()[:300], "warning")
                notes.append("dtc: include-dependent errors ignored; verify during the kernel build")
        else:
            notes.append("dtc not installed: relying on the built-in check")
        if shutil.which("kas") or shutil.which("bitbake"):
            notes.append("bitbake -p runs inside the build backend")
        else:
            notes.append("bitbake not found locally: `bitbake -p` is deferred to the build backend")
        for m in notes:
            self.log("dryrun", m)
        if problems:
            raise GenerationError("dryrun", "; ".join(problems[:3]), "", "\n".join(problems))
        return StageResult("dryrun", "warning" if "deferred" in " ".join(notes) or "not installed" in " ".join(notes) else "done", "; ".join(notes))

    # =========================== stage 9: package ============================================
    def _report_template(self) -> dict | None:
        from .report import load_template
        try:
            return load_template(self.p.get("report_template"))
        except (OSError, ValueError) as e:
            self.log("package", f"Report template not used: {e}", "warning")
            return None

    def build_manifest(self, build_info: dict | None = None) -> dict:
        s, p = self.soc, self.p
        peripherals, drivers = [], []
        assign = getattr(self, "assign", {})
        for x in p["peripherals"]:
            ic = self.cat.get("ic", x["ic"]) if x["ic"] else None
            c = self._ctrl_for(x["uid"], assign)
            drv = self.cat.get("driver", x["driver"]) if x["driver"] else None
            peripherals.append({"category": x["category"], "instance": c.get("instance", "-"), "ic": ic["part"] if ic else "custom", "vendor": ic["vendor"] if ic else "", "interface": ic["interface"] if ic else "",
                                "speed": x.get("props", {}).get("speed", x.get("mode", "")), "pins": ", ".join(c.get("pins", [])) or "-", "confidence": (ic or {}).get("_prov", {}).get("confidence", "unverified")})
            if drv:
                st, why = self.g.driver_status(drv, p, s)
                drivers.append({"name": drv["name"], "ic": ic["part"] if ic else "", "source": drv["source"], "version": x.get("driver_version") or drv.get("last_verified", ""), "kernel_range": f"{drv['first_kernel']} to {drv.get('removed_in') or drv.get('last_verified')}",
                                "compatible": drv["compatible"], "kconfig": drv["kconfig"], "licence": drv["licence"], "status": st})
            elif x["category"] != "ram":
                drivers.append({"name": "no driver", "ic": ic["part"] if ic else "custom", "source": "none", "version": "", "kernel_range": "", "compatible": "", "kconfig": [], "licence": "", "status": "no-driver"})
        for ci in p.get("custom_ics", []):
            peripherals.append({"category": "custom IC", "instance": ci.get("bus", "i2c"), "ic": ci["id"], "vendor": "user", "interface": ci.get("bus", "i2c"), "speed": "", "pins": ci.get("gpios", "-") or "-", "confidence": "unverified"})
        for cu in p.get("custom_usb", []):
            peripherals.append({"category": "custom USB function", "instance": "gadget", "ic": cu.get("name", "custom"), "vendor": "user", "interface": "usb", "speed": "", "pins": "-", "confidence": "unverified"})
        flash_parts = []
        for x in p["peripherals"]:
            if x["category"] == "flash":
                for q in x.get("props", {}).get("partitions", []) or [{"name": "bootloader", "offset": 0, "size": 0x100000}, {"name": "env", "offset": 0x100000, "size": 0x20000}, {"name": "data", "offset": 0x120000, "size": 0xE0000}]:
                    flash_parts.append({"name": q["name"], "offset": q["offset"], "size": q["size"]})
        prov = []
        for kind, rec in (("board", self.board), ("soc", s)):
            if rec:
                pv = rec.get("_prov", {})
                prov.append({"kind": kind, "id": rec["id"], "name": rec["name"], "source": pv.get("source_url", ""), "fetched": pv.get("fetched", ""), "confidence": pv.get("confidence", ""), "disputed": pv.get("disputed", False)})
        for x in p["peripherals"]:
            ic = self.cat.get("ic", x["ic"]) if x["ic"] else None
            if ic:
                pv = ic["_prov"]
                prov.append({"kind": "ic", "id": ic["id"], "name": ic["part"], "source": pv["source_url"], "fetched": pv["fetched"], "confidence": pv["confidence"], "disputed": pv["disputed"]})
        prov = sorted({json.dumps(r, sort_keys=True) for r in prov})
        prov = [json.loads(r) for r in prov]
        licences = sorted({d["licence"] for d in drivers if d["licence"]} | {"MIT (generated layer)"})
        gates = sorted({m["licence_gate"] for m in self.pins.values() if m["licence_gate"]} | ({s["licence_gate"]} if s.get("licence_gate", "none").lower() != "none" and not s.get("licence_gate", "none").lower().startswith("none") else set()))
        files = {k: {"sha256": v, "purpose": purpose(k)} for k, v in sorted(self.files.items()) if k != "bsp-manifest.json"}
        return {
            "report_template": self._report_template(),
            "format": 1, "tool": {"name": brand()["product"], "version": __version__, "database_version": self.cat.get_meta("seed_version", ""), "database_synced": self.cat.get_meta("last_sync", "")},
            "project": {"name": p["name"], "slug": self.slug, "hash": self.project_hash(), "author": p.get("author") or brand()["designer"], "board": self.board["name"] if self.board else "custom board",
                        "soc": s["name"], "soc_vendor": s["vendor"], "mode": p["mode"], "overlay": self._overlay()},
            "summary": {"release": f"{self.rel.get('name', p['release'])} {self.rel.get('version', '')}", "kernel": (self.cat.get("kernel", p["kernel"]) or {}).get("label", p["kernel"]), "bootloader": "U-Boot (Yocto recipe, pinned by layer)", "image": p["image"] + (f" ({p['distro']})" if p.get("distro") else ""),
                        "machine": self.machine, "arch": s["arch"], "yocto_baseline": brand()["yocto_baseline"]},
            "ram": p["ram"], "peripherals": peripherals, "drivers": drivers, "flash_partitions": flash_parts, "boot_order": ["SD", "eMMC", "USB", "SPI"],
            "layers": [{"repo": r, "url": m["url"], "branch": m["branch"], "commit": m["commit"] or "UNPINNED", "layers": m["layers"]} for r, m in sorted(self.pins.items())],
            "kernel_config": {k: sorted(set(v)) for k, v in sorted(self.kconfig.items())},
            "files": files, "warnings": [{"code": i["code"], "severity": i["severity"], "message": i["message"], "waived": bool(i.get("waived"))} for i in self.issues if i["severity"] in ("warn", "error")],
            "waivers": [i["message"] for i in self.issues if i.get("waived")], "unverified": [r["name"] for r in prov if r["confidence"] == "unverified"],
            "no_driver": [d["ic"] for d in drivers if d["status"] == "no-driver"], "provenance": prov, "licences": licences, "licence_gates": gates,
            "distro_note": "The distribution rootfs is not Yocto-built and is outside Yocto's SBOM and CVE tracking (GEN-9)." if p["image"] == "distro" else "",
            "block_diagram": self._diagram(peripherals), "build": build_info or {}, "stages": {k: {"state": v.state, "message": v.message} for k, v in self.result.items()},
        }

    def _diagram(self, peripherals: list[dict]) -> dict:
        return {"soc": self.soc["name"], "buses": sorted({x["interface"] for x in peripherals if x["interface"]}), "blocks": [{"label": f"{x['category']}: {x['ic']}", "instance": x["instance"]} for x in peripherals]}

    def _sbom(self, m: dict) -> dict:
        ns = uuid.uuid5(uuid.NAMESPACE_URL, "ybsg:" + m["project"]["hash"])
        comps = [{"type": "library", "name": l["repo"], "version": l["commit"], "externalReferences": [{"type": "vcs", "url": l["url"]}]} for l in m["layers"]]
        comps += [{"type": "library", "name": d["name"], "version": d["version"], "licenses": [{"license": {"name": d["licence"]}}] if d["licence"] else []} for d in m["drivers"] if d["status"] != "no-driver"]
        return {"bomFormat": "CycloneDX", "specVersion": "1.5", "serialNumber": f"urn:uuid:{ns}", "version": 1, "metadata": {"component": {"type": "firmware", "name": m["project"]["name"]}, "tools": [{"name": "YBSG", "version": __version__}]}, "components": comps}

    def stage_package(self, build_info: dict | None = None) -> StageResult:
        m = self.build_manifest(build_info)
        self.emit("sbom.cdx.json", json.dumps(self._sbom(m), indent=2, sort_keys=True) + "\n")
        self.emit("licences.json", json.dumps({"licences": m["licences"], "licence_gates": m["licence_gates"], "note": "YBSG never accepts a licence on your behalf (NFR-14)."}, indent=2) + "\n")
        m["files"] = {k: {"sha256": v, "purpose": purpose(k)} for k, v in sorted(self.files.items()) if k != "bsp-manifest.json"}
        manifest_text = json.dumps(m, indent=2, sort_keys=True) + "\n"
        (self.out / "bsp-manifest.json").write_text(manifest_text)  # always current; excluded from its own file list
        # removed files (FR-19 diff): in the old manifest but no longer generated
        for old in self.old_manifest.get("files", {}):
            if old not in self.files and (self.out / old).exists():
                cur = sha((self.out / old).read_bytes())
                if cur == self.old_manifest["files"][old]["sha256"]:
                    (self.out / old).unlink()
                    self.changes["removed"].append(old)
                    for d in (self.out / old).parents:  # drop folders the removal left empty, up to the output root
                        if d == self.out or any(d.iterdir()):
                            break
                        d.rmdir()
                else:
                    self.changes["user_kept"].append(old)
        self.manifest = m
        return StageResult("package", "done", f"{len(m['files'])} files; +{len(self.changes['added'])} ~{len(self.changes['changed'])} ={len(self.changes['unchanged'])} -{len(self.changes['removed'])}")


# ---------------------------------------------------------------------------------------------
def purpose(rel: str) -> str:
    table = [("layer.conf", "Layer definition"), ("conf/machine/", "Machine configuration"), (".dts", "Device tree source"), (".cfg", "Configuration fragment"),
             (".bbappend", "Recipe extension"), (".bb", "Recipe"), (".wks", "Flashable image layout"), ("kas/", "Reproducible build project (pinned)"),
             ("FLASHING", "Flashing guide"), ("sbom", "Software bill of materials"), ("licences", "Licence summary"), ("boot.cmd", "U-Boot boot script"),
             ("rootfs/", "Distribution rootfs assembly"), ("README", "Documentation"), ("COPYING", "Layer licence"), (".sh", "Script"), ("conf-notes", "Template notes"), ("sample", "Build configuration template"), ("PATCHES", "Applied patch list")]
    for k, v in table:
        if k in rel:
            return v
    return "Generated file"


def dts_syntax_check(text: str) -> list[str]:
    """Small device-tree syntax checker: balanced braces/brackets, terminated statements, quotes."""
    errs: list[str] = []
    depth = 0
    for n, raw in enumerate(text.splitlines(), 1):
        line = re.sub(r"/\*.*?\*/", "", raw)
        line = re.sub(r"//.*$", "", line).strip()
        if not line or line.startswith("#include") or line.startswith("/dts-v1/"):
            continue
        if line.count('"') % 2:
            errs.append(f"line {n}: unterminated string")
        if line.count("<") != line.count(">"):
            errs.append(f"line {n}: unbalanced <>")
        opens, closes = line.count("{"), line.count("}")
        if opens and not re.search(r"\{\s*$", line):
            errs.append(f"line {n}: '{{' must end the line")
        depth += opens - closes
        if depth < 0:
            errs.append(f"line {n}: unexpected '}}'")
            depth = 0
        if closes and not line.endswith(";"):
            errs.append(f"line {n}: '}}' must be followed by ';'")
        if not opens and not closes and not line.endswith(";"):
            errs.append(f"line {n}: property must end with ';'")
    if depth != 0:
        errs.append("unbalanced braces at end of file")
    return errs


def layer_check(layer: Path, release: str, series: dict[str, str]) -> list[str]:
    """Built-in conformance subset of yocto-check-layer (GEN-1). The full tool runs in the build backend."""
    probs: list[str] = []
    conf = layer / "conf" / "layer.conf"
    if not conf.exists():
        return ["layer.conf missing"]
    t = conf.read_text()
    for need in ("BBPATH", "BBFILES", "BBFILE_COLLECTIONS", "BBFILE_PATTERN_", "BBFILE_PRIORITY_", "LAYERVERSION_", "LAYERSERIES_COMPAT_", "LAYERDEPENDS_"):
        if need not in t:
            probs.append(f"layer.conf lacks {need}")
    m = re.search(r'LAYERSERIES_COMPAT_\S+\s*=\s*"([^"]+)"', t)
    if m and series.get(release, release) not in m.group(1).split():
        probs.append("LAYERSERIES_COMPAT does not include the selected release")
    if not (layer / "COPYING.MIT").exists():
        probs.append("layer licence file missing")
    for bb in layer.rglob("*.bb"):
        txt = bb.read_text()
        if "LICENSE" not in txt:
            probs.append(f"{bb.name}: LICENSE missing")
    for ba in layer.rglob("*.bbappend"):
        if "_%" not in ba.name and "_" not in ba.stem:
            probs.append(f"{ba.name}: bbappend should carry a version or %")
    return probs


def plan(cat: Catalog, project: dict) -> dict:
    """Exact files that generation will produce, computed in a scratch folder (UI-9). Never touches the real output."""
    import tempfile
    with tempfile.TemporaryDirectory(prefix="ybsg-plan-") as tmp:
        g = Generator(cat, json.loads(json.dumps(project)), tmp, offline=True)
        try:
            g.stage_validate()
        except GenerationError as e:
            return {"ok": False, "error": str(e), "uid": e.uid, "files": []}
        g.stage_resolve(); g.stage_layer(); g.stage_dts(); g.stage_config()
        files = []
        for rel in sorted(g.files):
            files.append({"path": rel, "purpose": purpose(rel), "bytes": (Path(tmp) / rel).stat().st_size})
        for extra, why in (("sbom.cdx.json", "Software bill of materials"), ("licences.json", "Licence summary"), ("bsp-manifest.json", "Pinned manifest (source for the report)"),
                           (f"report/{g.slug}-report.pdf", "PDF report"), (f"report/{g.slug}-report.md", "Report (Markdown)"), (f"report/{g.slug}-report.json", "Report (JSON)")):
            files.append({"path": extra, "purpose": why, "bytes": 0})
        return {"ok": True, "files": files, "slug": g.slug, "layer": g.layer_name, "machine": g.machine}
