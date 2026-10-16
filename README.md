# YBSG: Yocto Board Support Generator

Pick a SoC or board, then peripherals, ICs and drivers, kernel, Yocto release and image type. YBSG checks every choice against what the silicon and kernel support, and generates a Yocto BSP layer, machine config, device tree, U-Boot and kernel config, a pinned kas file, a flashing guide and a PDF report.

Baseline: Yocto Project 6.0 "Wrynose" LTS, Linux 6.18. Designed by Indeep Sidhu, Codewave Labs, Canada.

## Run

```
pip install -e .[window]        # window = optional native window (pywebview)
ybsg                            # opens the desktop app
ybsg gui --no-window --port 8765   # server only, open http://127.0.0.1:8765
```

From a terminal (same binary):

```
ybsg new --board imx8mm-evk -o my.ybsg
ybsg validate my.ybsg
ybsg generate my.ybsg --offline -o out        # add --build to run BitBake through kas
ybsg export out --zip bsp.zip --git bsp-repo
ybsg search "imx 8m mini"
ybsg doctor                                   # build backend and host resources
```

Without a native window the app opens in a Chromium `--app` window or your browser. Data lives in `~/.local/share/ybsg` (set `YBSG_HOME` to change it).

## Building images

File generation and the report need nothing else. To run BitBake from YBSG install `kas` with Docker or Podman (`kas-container`), or use WSL2 on Windows. A full build wants 16 cores, 32 GB RAM and 200 GB disk. Vendor licences (for example the NXP EULA) are shown and must be accepted by you; YBSG never accepts one.

## Data

`ybsg/data/seed.json` is a curated snapshot with source URL, fetch date and a confidence level on every record. Records marked medium or unverified, notably device-tree node labels, should be checked against the vendor documentation. Local database > Check for updates / Find more boards pulls from public sources with robots.txt and rate limits respected. Your own records are never overwritten by a sync.

## Develop

```
pip install -e .[dev]
pytest                                  # 27 tests
python -m ybsg gui --port 8765 --no-window &
python tests/ui_smoke.py && python tests/ui_dnd.py   # headless Chromium, writes docs/screenshots
                                        # YBSG_CHROMIUM=/usr/bin/google-chrome to use an installed browser
python tools/extract_strings.py         # refresh locales/en.json
python packaging/build.py               # executables + SHA256SUMS for this OS
```

Plugins: see `ybsg/plugins.py`. Requirement status and gaps: `docs/TRACEABILITY.md`.
