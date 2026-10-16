# PyInstaller spec. Build:  pyinstaller packaging/ybsg.spec --noconfirm
# Produces dist/ybsg (one folder) and dist/ybsg-portable (one file) for the current OS.
import sys
from pathlib import Path
root = Path(SPECPATH).parent
datas = [(str(root / "ybsg" / "data"), "ybsg/data"), (str(root / "ybsg" / "web"), "ybsg/web"), (str(root / "ybsg" / "branding.json"), "ybsg")]
hidden = ["reportlab.graphics.barcode.code128", "tkinter", "webview"]
a = Analysis([str(root / "run_ybsg.py")], pathex=[str(root)], datas=datas, hiddenimports=hidden, excludes=["pytest", "playwright"])
pyz = PYZ(a.pure)
icon = str(root / "packaging" / "ybsg.ico") if (root / "packaging" / "ybsg.ico").exists() and sys.platform == "win32" else None
exe_dir = EXE(pyz, a.scripts, [], exclude_binaries=True, name="ybsg", console=False, icon=icon)
coll = COLLECT(exe_dir, a.binaries, a.datas, name="ybsg")
exe_one = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="ybsg-portable", console=False, icon=icon)
