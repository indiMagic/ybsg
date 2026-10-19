"""Write a signed self-update feed (PKG-7) for a release.

    python tools/make_update_feed.py --key ~/.config/ybsg-signing/ed25519.seed --version 1.0.1 \
        --asset linux-x86_64=https://example/ybsg-portable=dist/ybsg-portable \
        --asset windows-x64=https://example/ybsg-portable.exe=dist/ybsg-portable.exe -o update.json

Each --asset is KEY=URL=LOCAL_FILE; the SHA-256 is taken from the local file. Publish update.json at the
`update_url` in branding.json.
"""
import argparse, hashlib, json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ybsg import signing, update

ap = argparse.ArgumentParser()
ap.add_argument("--key", required=True); ap.add_argument("--version", required=True); ap.add_argument("--notes", default="")
ap.add_argument("--asset", action="append", default=[]); ap.add_argument("-o", "--out", default="update.json")
a = ap.parse_args()
seed = bytes.fromhex(Path(a.key).expanduser().read_text().strip())
if signing.public_key(seed) not in signing.trusted_keys():
    sys.exit("this key is not in ybsg/data/signing.json")
assets = {}
for spec in a.asset:
    key, url, local = spec.split("=", 2)
    assets[key] = {"url": url, "sha256": hashlib.sha256(Path(local).read_bytes()).hexdigest()}
feed = {"version": a.version, "date": time.strftime("%Y-%m-%d"), "notes": a.notes, "assets": assets}
feed["signature"] = signing.sign(seed, update.canonical(feed)).hex()
Path(a.out).write_text(json.dumps(feed, indent=2) + "\n")
print(f"wrote {a.out}: {a.version}, {len(assets)} asset(s)")
