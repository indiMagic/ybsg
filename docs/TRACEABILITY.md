# Requirement traceability (SRS: Yocto Board Support Generator, 16 Oct 2026)

Status words are literal. **Done** = implemented and exercised in the build sandbox (Linux, no network to the layer index, no BitBake, no dtc, no Docker). **Done, unexercised** = code is written but the environment needed to run it was not available here. **Partial** = part of the requirement is met; the gap is stated. **Not done** = not implemented.

Evidence: `pytest` (tests/test_core.py, 27 tests), `tests/ui_smoke.py` and `tests/ui_dnd.py` (headless Chromium), CLI runs for rpi4b, imx8mm-evk, am62x-sk and bbb, and a PyInstaller Linux build that was run. A second pass on a Linux desktop with `dtc` 1.7, Ghostscript 10.02 and Google Chrome re-ran all of these and generated all seven shipped boards offline.

## Functional

| ID | Status | Where / note |
|---|---|---|
| FR-1 | Done | Boards, SoCs pages; "Custom board on a SoC" dialog; `project.from_board/from_soc` |
| FR-2 | Done | Ctrl+K palette and `/api/search` (fuzzy, punctuation-insensitive); filters on Boards, SoCs, ICs, Drivers pages |
| FR-3 | Done | `PAGES.board`, `PAGES.soc` (architecture, cores, clocks, controllers, layers, boot chain, datasheet, provenance) |
| FR-4 | Done | `from_board` pre-fills; add/remove/change in Peripherals |
| FR-5 | Done | Compare up to four boards (cards/table checkboxes). SoCs are compared through their boards |
| FR-6, FR-7, FR-8 | Done | Software page; data in `data/seed.json`, `data/layers.json` |
| FR-9 | Done | `graph.py`; offers disabled with a stated reason; checked live on every change |
| FR-10 | Done | Peripherals workbench: category, IC, driver, mode, class/function, partitions |
| FR-11 | Done | Custom IC and custom USB class dialogs; node, Kconfig fragment and ConfigFS/FunctionFS output |
| FR-12 | Done | Pin mux, exclusive clocks, DMA budget, partition overlap, I2C address, RAM, driver/kernel range and power domains (IO-voltage agreement and current budget per domain, waivable) are modelled. **The shipped catalogue has no power-domain data yet** (`power_domains` on a SoC, `power_domain` on a controller, `io_mv`/`current_ma` on an IC or peripheral), so the rule only fires on user or plugin data; tested with fixture data |
| FR-13 | Done | `.ybsg` zip, atomic save, duplicate, export, import; tested round-trip |
| FR-14 | Done | Board view (SVG) and block diagram in the PDF |
| FR-15 | Done | 10-stage generator: layer, machine, DTS, U-Boot/kernel config, image recipe, kas, WIC, boot script, flashing guide, report |
| FR-16 | Done, unexercised | Local kas / kas-container backend with BitBake progress parsing. **No remote builder.** Never run against a real BitBake here |
| FR-17 | Done | zip, kas file and git repository from the Generate page (`/api/export_git`); also `ybsg export --git` |
| FR-18 | Done | Every generation ends with PDF + Markdown + JSON |
| FR-19 | Done | Reuse of identical files, diff summary, changed/unchanged counts; tested |

## Driver database

| ID | Status | Note |
|---|---|---|
| DB-1 | Done | Every IC lists its drivers with source, kernel range, licence |
| DB-2 | Done | Kconfig symbols with transitive dependencies in a generated fragment (extendable by plugins) |
| DB-3 | Done | "No driver" state and custom-driver slot |
| DB-4 | Done | Removed/too-new/unverified kernel detection, suggested fix |
| DB-5 | Partial | `SyncService.due()`, manual trigger, pause/resume/cancel written against the public layer index API. **The index is blocked from this sandbox (HTTP 403), so no live sync was run.** The shipped data is a curated snapshot (v2026.10.0) |
| DB-6 | Partial | Canonical ids and compact-name matching. No automated vendor-alias or duplicate-merging service |
| DB-7 | Done | User records never overwritten by sync (a test found and fixed a hole here) |
| DB-8 | Done | high / medium / unverified, disputed flag with resolution, device-tree value wins |
| DB-9 | Done | robots.txt, rate limit, user agent. Licence-restricted sources are not scraped |
| DB-10 | Partial | Snapshot ships with the app and a SHA-256 of its contents. **It is not cryptographically signed** |
| DB-11 | Done, unexercised | `ybsg db links`; needs the network |
| DB-12 | Done | Picture beside specs; labelled placeholder when none |
| DB-13 | Done | Local store, read offline |
| DB-14 | Done | Check one/all, Update |
| DB-15 | Done | Banner with all states of the SRS |
| DB-16 | Done, unexercised | Find more boards, list, choose, download. Live discovery blocked here |
| DB-17 | Done | Pause, resume, cancel |
| DB-18 | Done | Source URL, licence, attribution kept; unclear-licence pictures stay local and are excluded from exports |
| DB-19 | Done | Size, last checked, remove a board |

## Generation

| ID | Status | Note |
|---|---|---|
| GEN-1 | Partial | Built-in layer conformance check passes. **`yocto-check-layer` itself was not run (no Yocto here)** |
| GEN-2 | Done | Pins by `git ls-remote`, written to kas file and manifest. Offline mode emits an explicit UNPINNED warning rather than inventing hashes |
| GEN-3 | Done | Thin overlay on vendor/community BSP; from-scratch machine also available |
| GEN-4 | Done | SoC `.dtsi` included by reference. Syntax is checked by `dtc` when installed, else a built-in check. With `dtc` installed, the files for bbb and imx8mm-evk pass outright; for the other five, `dtc` errors because the SoC `.dtsi` lives in the kernel tree, so those errors are logged as a warning. **Node labels are medium confidence**; verify on a real build |
| GEN-5 | Partial | Memory node and reuse of the vendor DRAM configuration. **No DRAM training/timing is generated**; the UI and report say it must be validated on hardware |
| GEN-6 | Done | linux-yocto, vendor, mainline LTS; patches listed |
| GEN-7 | Done | FLASHING.md per board, also in the PDF |
| GEN-8 | Done, unexercised | Containerised build through kas-container; host-package list not verified |
| GEN-9 | Done, unexercised | Distro rootfs script (debootstrap/mmdebstrap style); never run here |
| GEN-10 | Done | Tested: same project, same files |
| GEN-11 | Done | Stop, log excerpt, retry from that stage, link back to the choice |
| GEN-12 | Done | Merge / keep / replace; tested (a test found and fixed a bug where a kept edit was forgotten on the next run) |

## User interface

| ID | Status | Note |
|---|---|---|
| UI-1 | Done | SVG board, drag-and-drop and keyboard placement onto pads; invalid drops refused with a reason |
| UI-2 | Partial | Pin map lists pins per controller as assigned / free / conflicting using shape and label. **Not a true package ball map** |
| UI-3 | Done | Dark and light themes, one design system, vector icons |
| UI-4 | Done | Card, table, graph views. Photos appear only when fetched with a clear licence; otherwise a placeholder |
| UI-5 | Not measured | 60 fps with 500 nodes was not benchmarked |
| UI-6 | Partial | Keyboard operation, labels, status by shape+word, contrast chosen for AA. **No formal WCAG audit or screen-reader test was done** |
| UI-7 | Done | `t()` everywhere; `tools/extract_strings.py` writes `locales/en.json` |
| UI-8 | Done | `?` tooltip with link on each category and driver area |
| UI-9 | Done | Review tree with the exact planned files |
| UI-10 | Done | Collapsible side menu, four groups |
| UI-11 | Done | Command search, project name, database chip, status bar |
| UI-12 | Done | Completion ticks; Generate disabled with a stated reason |
| UI-13 | Done | Splash driven by real start-up steps (web + native Tk splash). **Tk splash not seen on a real desktop here** |
| UI-14 | Done | Plain-language error and View log |
| UI-15, UI-16 | Done | About from Help and Settings; version, designer, company, site, Yocto baseline, database version, licences, copy version info |
| UI-17 | Done | `branding.json` is the single source (UI, PDF, CLI, installer metadata) |

## Reports

| ID | Status | Note |
|---|---|---|
| RPT-1 | Done | Offline |
| RPT-2 | Done | Reads `bsp-manifest.json` only; tested by editing the manifest |
| RPT-3 | Done | TOC, bookmarks, page numbers, searchable text; PDF/A-2b through Ghostscript when installed (tested: `pdfaid:part=2`, outlines kept; status written to the Report stage log). Without Ghostscript a standard PDF is produced. Not checked with a PDF/A validator such as veraPDF |
| RPT-4 | Done | Markdown and JSON |
| RPT-5 | Not done | Per-organisation templates (could-have) |

## Non-functional

| ID | Status | Note |
|---|---|---|
| NFR-1 | Partial | ~6 ms on the shipped catalogue (about a hundred records). **Not measured at 50,000 records** |
| NFR-2 | Done | ~4 ms per validation |
| NFR-3 | Done | A full generation including the PDF takes a few seconds |
| NFR-4 | Done | Resource check against 16 cores / 32 GB / 200 GB with a warning |
| NFR-5 | Done | Autosave every 30 s client and server side, atomic writes |
| NFR-6 | Partial | Build backend reuses kas download/sstate directories; not verified with a real build |
| NFR-7 | Partial | Hash-verified pictures and snapshot; scripts run in the container backend. No signature verification |
| NFR-8 | Done | `creds.py` uses the OS keychain (needs `keyring`); refuses to store otherwise |
| NFR-9 | Done | No telemetry exists; the setting is shown off |
| NFR-10 | Done | Releases, kernels, layers are data files |
| NFR-11 | Done | `plugins.py`: data packs, Kconfig deps, report sections. Basic; in-process, trusted plugins only |
| NFR-12 | Done | Search, validation, generation, report all work offline (tested with `--offline`) |
| NFR-13 | Partial | One codebase. **Only Linux was run.** Windows build is scripted in CI |
| NFR-14 | Partial | Licence gate shown and tracked per layer; YBSG never accepts a licence for the user. No per-component licence inventory of fetched sources |
| NFR-15 | Partial | Determinism and generation covered by automated tests on the shipped boards; **not on real hardware** |
| NFR-16 | Not measured | No usability test with first-time users |

## Packaging and process

| ID | Status | Note |
|---|---|---|
| PKG-1 | Done, unexercised | `packaging/ybsg.spec`, `ybsg.iss`, CI. **Not built on Windows, not signed** (signing step is a documented placeholder) |
| PKG-2 | Partial | Linux one-file and one-folder binaries built and run here; `.tar.gz` and `.deb` are scripted; **AppImage needs `appimagetool` and was not built** |
| PKG-3 | Not verified | Ubuntu 22.04 / 24.04 not tested |
| PKG-4 | Partial | Backend detection reports what is missing; WSL2 detection is simple |
| PKG-5 | Done, unexercised | `.github/workflows/build.yml` with SHA-256 sums; never run |
| PKG-6 | Done | Same binary: `ybsg generate project.ybsg` |
| PKG-7 | Not done | Self-update |
| PKG-8 | Not done | macOS (could-have) |
| DOC-1 | Partial | Screenshots in `docs/screenshots` come from headless Chromium on Linux, not from both operating systems |

## What could not be proven here

Boot on real hardware (the SRS acceptance bar), a real BitBake build, `yocto-check-layer`, live layer-index sync, Windows and AppImage packaging, signing. Each needs a machine or network access that the sandbox did not have.
