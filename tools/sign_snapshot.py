"""Sign the shipped catalogue snapshot (DB-10). The private key stays outside the repository.

    python tools/sign_snapshot.py --new-key ~/.config/ybsg-signing/ed25519.seed   # once: creates the key, prints the public key
    python tools/sign_snapshot.py --key ~/.config/ybsg-signing/ed25519.seed        # each release: writes ybsg/data/seed.json.sig

In CI, keep the key in a secret and pass its path with --key. Add the printed public key to ybsg/data/signing.json.
"""
import argparse, json, os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ybsg import signing

ap = argparse.ArgumentParser()
ap.add_argument("--key", help="path of the 32-byte private seed (hex)")
ap.add_argument("--new-key", help="create a new private seed at this path and print its public key")
ap.add_argument("--file", default=str(signing.DATA / "seed.json"))
a = ap.parse_args()
if a.new_key:
    k = Path(a.new_key).expanduser()
    if k.exists():
        sys.exit(f"{k} exists; refusing to overwrite a signing key")
    k.parent.mkdir(parents=True, exist_ok=True)
    seed = signing.new_seed()
    fd = os.open(k, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.write(fd, seed.hex().encode()); os.close(fd)
    print(json.dumps({"public_key": signing.public_key(seed).hex()}))
    sys.exit(0)
if not a.key:
    sys.exit("--key is required")
seed = bytes.fromhex(Path(a.key).expanduser().read_text().strip())
pub = signing.public_key(seed)
if pub not in signing.trusted_keys():
    sys.exit(f"public key {pub.hex()} is not in ybsg/data/signing.json; add it first")
f = Path(a.file)
f.with_suffix(f.suffix + ".sig").write_text(signing.sign(seed, f.read_bytes()).hex() + "\n")
print(f"signed {f} ->", signing.snapshot_status(f))
