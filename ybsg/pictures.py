"""Board photos (DB-12, DB-18) from Wikimedia Commons, where every file carries a licence and an author.

Commons search is loose ("BeaglePlay" returns beagle dogs), so a photo is accepted only when its file name names
the board. Boards without a matching photo keep the generated drawing; the user can add their own picture, which
stays on this machine. Look-ups run one at a time in the background, at most one request per second, and a board
with no photo is not asked about again for 30 days.
"""
from __future__ import annotations

import html
import json
import queue
import re
import threading
import time
import urllib.parse
import urllib.request

from .catalog import Catalog, now

API = "https://commons.wikimedia.org/w/api.php"
UA = "YBSG/1.0 (https://codewavelabs.ca; board pictures for the YBSG desktop app)"
RETRY_DAYS = 30
GENERIC = {"board", "development", "dev", "machine", "kit", "the", "with", "for", "and", "bit", "bits", "mode", "configuration",
           "module", "carrier", "nxp", "ti", "texas", "instruments", "nvidia", "rockchip", "stmicroelectronics", "edition"}
FREE = re.compile(r"^(cc0|public domain|pd|cc by(-sa)?( \d\.\d)?|gfdl|mit|attribution)", re.I)


def _compact(s: str) -> str:
    return re.sub(r"[\W_]+", "", s.lower())


def _key_tokens(name: str) -> list[str]:
    words = re.findall(r"[a-z]+|\d+[a-z]*", name.lower().replace("(", " ").replace(")", " "))
    return [w for w in words if w not in GENERIC and not re.fullmatch(r"\d+bit", w)]


def title_matches(name: str, title: str) -> bool:
    """Every distinctive word of the board name appears in the file name (spacing and punctuation ignored)."""
    toks = _key_tokens(name)
    t = _compact(title.removeprefix("File:"))
    phrase = _compact("".join(toks))  # in order and adjacent: "pi 3" must not match a date, "zero w" not "zero 2 w"
    return len(phrase) >= 6 and phrase in t


def _get(url: str, timeout: float = 20) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def lookup(name: str) -> dict | None:
    """Best Commons photo for a board name, or None. Returns {thumb, page, licence, artist, mime}."""
    words = " ".join(_key_tokens(re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name))) or name  # RaspberryPi -> Raspberry Pi
    q = urllib.parse.urlencode({"action": "query", "format": "json", "generator": "search", "gsrnamespace": 6, "gsrlimit": 20,
                                "gsrsearch": f"{words} filetype:bitmap", "prop": "imageinfo", "iiprop": "url|mime|extmetadata",
                                "iiextmetadatafilter": "LicenseShortName|Artist", "iiurlwidth": 640})
    pages = sorted((json.loads(_get(f"{API}?{q}")).get("query") or {}).get("pages", {}).values(), key=lambda p: p.get("index", 0))
    for p in pages:
        ii = (p.get("imageinfo") or [{}])[0]
        meta = ii.get("extmetadata", {})
        lic = meta.get("LicenseShortName", {}).get("value", "")
        if ii.get("mime") in ("image/jpeg", "image/png") and title_matches(name, p["title"]) and FREE.match(lic) and ii.get("thumburl"):
            artist = html.unescape(re.sub(r"<[^>]+>", "", meta.get("Artist", {}).get("value", ""))).strip()
            return {"thumb": ii["thumburl"], "page": ii.get("descriptionurl", ""), "licence": lic, "artist": artist, "mime": ii["mime"]}
    return None


def fetch_for(cat: Catalog, board: dict, force: bool = False) -> str:
    """Look up and store a photo for one board. Returns 'saved', 'none', 'skipped' or 'failed: ...'."""
    img = cat.get_image(board["id"])
    if img and img["source_url"] != "ybsg:generated" and not force:
        return "skipped"
    miss = cat.get_meta(f"picture-miss:{board['id']}")
    if miss and not force and time.time() - time.mktime(time.strptime(miss, "%Y-%m-%dT%H:%M:%SZ")) < RETRY_DAYS * 86400:
        return "none"
    try:
        hit = lookup(board["name"])
        if not hit:
            cat.set_meta(f"picture-miss:{board['id']}", now())
            return "none"
        blob = _get(hit["thumb"])
        cat.set_image(board["id"], hit["mime"], blob, source_url=hit["page"], licence=hit["licence"],
                      attribution=f"{hit['artist'] or 'unknown author'} / Wikimedia Commons")
        return "saved"
    except Exception as e:  # noqa: BLE001
        return f"failed: {e}"


class PictureQueue:
    """Background look-ups, one at a time and at most one per second, so browsing never waits for the network."""

    def __init__(self, cat: Catalog):
        self.cat, self.q, self.pending = cat, queue.Queue(), set()
        threading.Thread(target=self._run, daemon=True).start()

    def want(self, board: dict) -> None:
        if board["id"] not in self.pending:
            self.pending.add(board["id"])
            self.q.put(board)

    def _run(self) -> None:
        while True:
            b = self.q.get()
            try:
                fetch_for(self.cat, b)
            finally:
                self.pending.discard(b["id"])
                time.sleep(1.0)
