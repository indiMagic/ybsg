"""Build the desktop executables for the current OS and write SHA-256 checksums.
Usage: python packaging/build.py
Windows: dist/ybsg-portable.exe and (if Inno Setup is installed) dist/ybsg-setup.exe
Linux:   dist/ybsg-<ver>-linux-x86_64.tar.gz, dist/ybsg_<ver>_amd64.deb and dist/ybsg-portable
"""
import hashlib, os, shutil, subprocess, sys, tarfile, textwrap
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from ybsg import __version__ as V

def sh(*a, **k):
    print("+", " ".join(map(str, a))); subprocess.run(a, check=True, **k)

def sha(files):
    lines = [f"{hashlib.sha256(f.read_bytes()).hexdigest()}  {f.name}" for f in files]
    (ROOT / "dist" / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n"); print("\n".join(lines))

def main():
    os.chdir(ROOT)
    sh(sys.executable, "-m", "PyInstaller", "packaging/ybsg.spec", "--noconfirm", "--clean")
    dist = ROOT / "dist"; out = []
    if sys.platform == "win32":
        out.append(dist / "ybsg-portable.exe")
        iss = ROOT / "packaging" / "ybsg.iss"
        iscc = shutil.which("iscc") or r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
        if Path(iscc).exists():
            sh(iscc, f"/DAppVersion={V}", str(iss)); out.append(dist / "ybsg-setup.exe")
        else:
            print("Inno Setup not found: skipping the installer, the portable exe is still built.")
    else:
        tgz = dist / f"ybsg-{V}-linux-x86_64.tar.gz"
        with tarfile.open(tgz, "w:gz") as t: t.add(dist / "ybsg", arcname="ybsg")
        out += [tgz, dist / "ybsg-portable"]
        deb = dist / f"ybsg_{V}_amd64.deb"; stage = dist / "deb"
        shutil.rmtree(stage, ignore_errors=True)
        (stage / "DEBIAN").mkdir(parents=True); (stage / "opt").mkdir(); (stage / "usr/bin").mkdir(parents=True); (stage / "usr/share/applications").mkdir(parents=True)
        shutil.copytree(dist / "ybsg", stage / "opt/ybsg")
        (stage / "usr/bin/ybsg").symlink_to("/opt/ybsg/ybsg")
        (stage / "usr/share/applications/ybsg.desktop").write_text("[Desktop Entry]\nName=YBSG\nComment=Yocto Board Support Generator\nExec=/opt/ybsg/ybsg\nTerminal=false\nType=Application\nCategories=Development;\n")
        (stage / "DEBIAN/control").write_text(textwrap.dedent(f"""\
            Package: ybsg
            Version: {V}
            Architecture: amd64
            Maintainer: Codewave Labs <info@codewavelabs.ca>
            Description: Yocto Board Support Generator
             Generates Yocto BSP layers and a PDF report from a SoC, board and peripheral selection.
            """))
        sh("dpkg-deb", "--build", str(stage), str(deb)); out.append(deb)
        appdir = dist / "YBSG.AppDir"; shutil.rmtree(appdir, ignore_errors=True)
        shutil.copytree(dist / "ybsg", appdir / "usr/bin")
        (appdir / "AppRun").write_text('#!/bin/sh\nexec "$(dirname "$0")/usr/bin/ybsg" "$@"\n'); (appdir / "AppRun").chmod(0o755)
        (appdir / "ybsg.desktop").write_text("[Desktop Entry]\nName=YBSG\nExec=ybsg\nIcon=ybsg\nType=Application\nCategories=Development;\n")
        shutil.copy(ROOT / "packaging" / "ybsg.svg", appdir / "ybsg.svg")
        tool = shutil.which("appimagetool")
        if tool:
            sh(tool, str(appdir), str(dist / f"YBSG-{V}-x86_64.AppImage")); out.append(dist / f"YBSG-{V}-x86_64.AppImage")
        else:
            print("appimagetool not found: skipping the AppImage (tar.gz and .deb are still built).")
    sha([f for f in out if f.exists()])

if __name__ == "__main__":
    main()
