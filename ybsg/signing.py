"""Ed25519 signatures for the shipped catalogue snapshot (DB-10, NFR-7).

Pure Python after RFC 8032 section 6, so verification needs no extra dependency. Speed is irrelevant here: one
signature is checked when a snapshot is loaded. The private key never ships; `tools/sign_snapshot.py` signs a
release with a key kept outside the repository, and the app holds only the public key (data/signing.json).
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

DATA = Path(__file__).parent / "data"
_p = 2 ** 255 - 19
_L = 2 ** 252 + 27742317777372353535851937790883648493
_d = -121665 * pow(121666, _p - 2, _p) % _p
_I = pow(2, (_p - 1) // 4, _p)


def _H(m: bytes) -> bytes:
    return hashlib.sha512(m).digest()


def _xrecover(y: int) -> int:
    xx = (y * y - 1) * pow(_d * y * y + 1, _p - 2, _p)
    x = pow(xx, (_p + 3) // 8, _p)
    if (x * x - xx) % _p:
        x = x * _I % _p
    if x % 2:
        x = _p - x
    return x


_By = 4 * pow(5, _p - 2, _p) % _p
_B = (_xrecover(_By), _By, 1, _xrecover(_By) * _By % _p)


def _add(P, Q):
    A = (P[1] - P[0]) * (Q[1] - Q[0]) % _p
    B = (P[1] + P[0]) * (Q[1] + Q[0]) % _p
    C = 2 * P[3] * Q[3] * _d % _p
    D = 2 * P[2] * Q[2] % _p
    E, F, G, H = B - A, D - C, D + C, B + A
    return (E * F % _p, G * H % _p, F * G % _p, E * H % _p)


def _mul(s: int, P):
    Q = (0, 1, 1, 0)
    while s:
        if s & 1:
            Q = _add(Q, P)
        P = _add(P, P)
        s >>= 1
    return Q


def _enc(P) -> bytes:
    zi = pow(P[2], _p - 2, _p)
    x, y = P[0] * zi % _p, P[1] * zi % _p
    return (y | ((x & 1) << 255)).to_bytes(32, "little")


def _dec(s: bytes):
    y = int.from_bytes(s, "little")
    sign, y = y >> 255, y & ((1 << 255) - 1)
    if y >= _p:
        raise ValueError("bad point")
    x = _xrecover(y)
    if (x & 1) != sign:
        x = _p - x
    P = (x, y, 1, x * y % _p)
    if (-x * x + y * y - 1 - _d * x * x * y * y) % _p:
        raise ValueError("point not on curve")
    return P


def _eq(P, Q) -> bool:
    return (P[0] * Q[2] - Q[0] * P[2]) % _p == 0 and (P[1] * Q[2] - Q[1] * P[2]) % _p == 0


def _secret(seed: bytes) -> tuple[int, bytes]:
    h = _H(seed)
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a, h[32:]


def public_key(seed: bytes) -> bytes:
    return _enc(_mul(_secret(seed)[0], _B))


def sign(seed: bytes, msg: bytes) -> bytes:
    a, prefix = _secret(seed)
    A = _enc(_mul(a, _B))
    r = int.from_bytes(_H(prefix + msg), "little") % _L
    R = _enc(_mul(r, _B))
    k = int.from_bytes(_H(R + A + msg), "little") % _L
    return R + ((r + k * a) % _L).to_bytes(32, "little")


def verify(pub: bytes, msg: bytes, sig: bytes) -> bool:
    try:
        if len(sig) != 64 or len(pub) != 32:
            return False
        A, R = _dec(pub), _dec(sig[:32])
        s = int.from_bytes(sig[32:], "little")
        if s >= _L:
            return False
        k = int.from_bytes(_H(sig[:32] + pub + msg), "little") % _L
        return _eq(_mul(s, _B), _add(R, _mul(k, A)))
    except ValueError:
        return False


def new_seed() -> bytes:
    return os.urandom(32)


# ---- snapshot ------------------------------------------------------------------------------------------
def trusted_keys() -> list[bytes]:
    f = DATA / "signing.json"
    return [bytes.fromhex(k["public_key"]) for k in json.loads(f.read_text())["keys"]] if f.exists() else []


def snapshot_status(seed_file: Path) -> dict:
    """{state: signed | unsigned | invalid, detail}. `invalid` means the snapshot changed after it was signed."""
    sig_file = seed_file.with_suffix(seed_file.suffix + ".sig")
    if not sig_file.exists():
        return {"state": "unsigned", "detail": f"no {sig_file.name}"}
    try:
        sig = bytes.fromhex(sig_file.read_text().strip())
    except ValueError:
        return {"state": "invalid", "detail": f"{sig_file.name} is not hex"}
    body = seed_file.read_bytes()
    for k in trusted_keys():
        if verify(k, body, sig):
            return {"state": "signed", "detail": f"Ed25519, key {k.hex()[:16]}"}
    return {"state": "invalid", "detail": "signature does not match any trusted key: the snapshot was changed after signing"}
