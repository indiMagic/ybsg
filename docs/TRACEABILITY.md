# Requirement traceability (SRS: Yocto Board Support Generator, 16 Oct 2026)

Status words are literal. **Done** = implemented and exercised in the build sandbox (Linux, no network to the layer index, no BitBake, no dtc, no Docker). **Done, unexercised** = code is written but the environment needed to run it was not available here. **Partial** = part of the requirement is met; the gap is stated. **Not done** = not implemented.

Evidence: `pytest` (tests/test_core.py, 44 tests), `tests/ui_a11y.py`, `tests/ui_perf.py`, `tests/ui_smoke.py` and `tests/ui_dnd.py` (headless Chromium), CLI runs for rpi4b, imx8mm-evk, am62x-sk and bbb, and a PyInstaller Linux build that was run. A second pass on a Linux desktop with `dtc` 1.7, Ghostscript 10.02 and Google Chrome re-ran all of these and generated all seven shipped boards offline.

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
| FR-16 | Done, partly exercised | Local kas (run from the output folder, `KAS_WORK_DIR` set), kas-container, WSL2 (PKG-4) and a **remote builder over ssh + rsync** (`--remote`, Settings, or `YBSG_REMOTE_BUILDER`). Exercised: kas checkout, `bitbake -p` (3,182 recipes, 0 errors), `bitbake -n` of the bbb image (4,502 tasks resolved) and `bitbake -e`. Remote and local flows tested with stand-in ssh/kas. **A full BitBake build was not run** (this host has 8 cores and 7.5 GB RAM) |
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
| DB-5 | Partial | Sync logic exercised on real layer-index data (wrynose: 468 machines, 129 layers) through `ybsg db check/update/find --from DIR`; that run found and fixed a layer-mapping bug. **The live API answers YBSG's honest user agent with a Cloudflare bot challenge**; YBSG reports this plainly and does not disguise itself. Live sync needs the operators to allow it |
| DB-6 | Done | Vendor aliases (`data/aliases.json`) in search and filters; `find_duplicates` and `merge` (`ybsg db dupes`, `ybsg db merge KEEP DROP`) keep the dropped id as an alias, so old projects still resolve; user records are never merged (DB-7). Merging is on request, not automatic |
| DB-7 | Done | User records never overwritten by sync (a test found and fixed a hole here) |
| DB-8 | Done | high / medium / unverified, disputed flag with resolution, device-tree value wins |
| DB-9 | Done | robots.txt, rate limit, user agent. Licence-restricted sources are not scraped |
| DB-10 | Done | `seed.json` is signed with Ed25519 (`seed.json.sig`, pure-Python RFC 8032, checked against the RFC test vectors). The app holds the public key (`data/signing.json`) and **refuses a snapshot that fails the check**. Re-sign after editing the seed: `tools/sign_snapshot.py`. The private key is outside the repository |
| DB-11 | Done | `ybsg db links` run live: 21 URLs in 51 s (HEAD then ranged GET, robots.txt and one request per second per host, hosts in parallel). It found a dead NXP i.MX6ULL EVK page (404); st.com and analog.com time out for bots; raspberrypi.com refuses checks |
| DB-12 | Done | Picture beside specs; labelled placeholder when none |
| DB-13 | Done | Local store, read offline |
| DB-14 | Done | Check one/all, Update |
| DB-15 | Done | Banner with all states of the SRS |
| DB-16 | Done | Exercised on real layer-index data: 462 new machines, each filed under its real layer |
| DB-17 | Done | Pause, resume, cancel |
| DB-18 | Done | Source URL, licence, attribution kept; unclear-licence pictures stay local and are excluded from exports |
| DB-19 | Done | Size, last checked, remove a board |

## Generation

| ID | Status | Note |
|---|---|---|
| GEN-1 | Partial | Built-in check passes, and `yocto-check-layer` was run on the generated bbb layer: test_parse, test_layerseries_compat, test_readme, test_security, test_signatures, test_world and test_bsp_* passed on the dependency layers. A missing meta-ti dependency (cifs-utils from meta-networking) was found and is now declared. **The final rerun on the generated layer was cut short** by the host running out of memory |
| GEN-2 | Done | Exercised online: all seven boards pinned to real commits. Found that **poky has no wrynose branch**: wrynose and master use openembedded-core + bitbake 2.18 + meta-yocto. Layers, collections and branches are verified data in `layers.json` (`layer_catalog`) |
| GEN-3 | Done | Thin overlay with machine-qualified assignments (`:<machine>` beats vendor overrides). For TI the kernel profile is chosen (`TI_PREFERRED_BSP`) and providers pinned; `bitbake -e` confirms linux-ti-staging 6.18, u-boot-ti-staging, our DTB and our WKS for bbb. **The other five boards' providers have not been checked with `bitbake -e`** |
| GEN-4 | Done | SoC `.dtsi` included by reference. Syntax is checked by `dtc` when installed, else a built-in check. With `dtc` installed, the files for bbb and imx8mm-evk pass outright; for the other five, `dtc` errors because the SoC `.dtsi` lives in the kernel tree, so those errors are logged as a warning. **Node labels are medium confidence**; verify on a real build |
| GEN-5 | Partial | Memory node and reuse of the vendor DRAM configuration. **No DRAM training/timing is generated**; the UI and report say it must be validated on hardware |
| GEN-6 | Done | linux-yocto, vendor, mainline LTS; patches listed |
| GEN-7 | Done | FLASHING.md per board, also in the PDF |
| GEN-8 | Partial | kas run natively on Ubuntu 24.04 (checkout, parse, dry run). kas-container not run (no Docker or Podman here) |
| GEN-9 | Done, partly exercised | A documented procedure per distribution (debootstrap, mmdebstrap, dnf with gpgcheck, Arch Linux ARM tarball with md5, Alpine minirootfs with sha256), a qemu-binfmt check, loud failures, and the correct `wic --rootfs-dir`. Unsupported pairs are refused (Fedora has no armhf). The Alpine armv7 download and checksum were run; **the full assembly needs root and a built image** |
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
| UI-5 | Done | `tests/ui_perf.py` with 509 boards (462 real ones from the layer index): cards, table and graph each scroll at 60 fps (worst frame 16.8 ms) in Chrome; switching views takes 14–20 ms. Headless Chrome, software rendering |
| UI-6 | Partial | `tests/ui_a11y.py` (axe-core 4.10, WCAG 2.1 A/AA) on 13 screens in both themes: **0 serious or critical**. Fixed along the way: dark-theme contrast (3.89 to 4.67:1 or better) and an unlabelled select. **No screen-reader test** |
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
| RPT-5 | Done | Per-organisation templates (`<data>/report-templates/*.json`: organisation, colours, logo, prepared-for, cover note, extra sections), embedded in the manifest so RPT-2 holds; selected per project (Reports page, `--template`) |

## Non-functional

| ID | Status | Note |
|---|---|---|
| NFR-1 | Done | 50,000 records: search takes 50–160 ms (was 4–5.5 s) through a cached index and trigram fuzzy candidates; bulk insert of 50,000 records takes 2.6 s (was 228 s). The index rebuild after a write takes about 2 s at this size |
| NFR-2 | Done | ~4 ms per validation |
| NFR-3 | Done | A full generation including the PDF takes a few seconds |
| NFR-4 | Done | Resource check against 16 cores / 32 GB / 200 GB with a warning |
| NFR-5 | Done | Autosave every 30 s client and server side, atomic writes |
| NFR-6 | Partial | Build backend reuses kas download/sstate directories; not verified with a real build |
| NFR-7 | Done | Signed snapshot (DB-10) and signed, hash-checked update feed (PKG-7); picture hashes kept |
| NFR-8 | Done | `creds.py` uses the OS keychain (needs `keyring`); refuses to store otherwise |
| NFR-9 | Done | No telemetry exists; the setting is shown off |
| NFR-10 | Done | Releases, kernels, layers are data files |
| NFR-11 | Done | `plugins.py`: data packs, Kconfig deps, report sections. Basic; in-process, trusted plugins only |
| NFR-12 | Done | Search, validation, generation, report all work offline (tested with `--offline`) |
| NFR-13 | Partial | One codebase. **Only Linux was run.** Windows build is scripted in CI |
| NFR-14 | Done, unexercised | After a build, the image `license.manifest` becomes a per-package table in the PDF and Markdown (tested with a fixture; not from a real build) |
| NFR-15 | Partial | Determinism and generation covered by automated tests on the shipped boards; **not on real hardware** |
| NFR-16 | Not measured | No usability test with first-time users |

## Packaging and process

| ID | Status | Note |
|---|---|---|
| PKG-1 | Done, unexercised | `packaging/ybsg.spec`, `ybsg.iss`, CI. **Not built on Windows, not signed** (signing step is a documented placeholder) |
| PKG-2 | Done | tar.gz, .deb, one-file portable and **AppImage** built here; all run on Ubuntu 24.04.4 |
| PKG-3 | Partial | Ubuntu 24.04.4: binaries, AppImage, kas and BitBake parse verified. **22.04 not tested** |
| PKG-4 | Done, unexercised | WSL2 detection (UTF-16 `wsl -l -v`, WSL1 vs WSL2, kas and rsync present) and builds inside the WSL filesystem. Tested with a mocked wsl.exe; **not run on Windows** |
| PKG-5 | Done, unexercised | `.github/workflows/build.yml` with SHA-256 sums; never run |
| PKG-6 | Done | Same binary: `ybsg generate project.ybsg` |
| PKG-7 | Done | `ybsg update`, Settings > Check for updates: signed feed, SHA-256-checked download, atomic replace of the portable binary or AppImage; pip and system installs are told what to do. Tested live on the packaged binary. **`update_url` is empty until a release host exists** (`tools/make_update_feed.py` writes the feed) |
| PKG-8 | Not done | macOS (could-have) |
| DOC-1 | Partial | Screenshots refreshed after today's UI changes; Linux only |

## What could not be proven here

Boot on real hardware (the SRS acceptance bar), a full BitBake build, `bitbake -e` provider checks for five of the seven boards, a complete `yocto-check-layer` pass on the generated layer, live layer-index sync (Cloudflare blocks the YBSG user agent), Windows packaging, Authenticode signing, Ubuntu 22.04, a screen-reader test. UI-2 (package ball map) and GEN-5 (DRAM training) need vendor data that is not available.
