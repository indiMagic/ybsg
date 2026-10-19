"""Add a board from a link (DB-16): a BSP layer git repository, one machine .conf file, or a product web page.

Links come from the user, so only http(s) is accepted, git may use nothing but https/http (no ext:: or file://
transports), pages are size-limited and robots.txt is honoured. Records made from a product page are user records
(unverified); a picture taken from such a page stays on this machine and is never exported.
"""
from __future__ import annotations

import html
import os
import re
import subprocess
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

from . import discover as D
from .catalog import Catalog

UA = "YBSG/1.0 (+https://codewavelabs.ca; add a board from a link)"
MAX_PAGE = 3 * 1024 * 1024
GIT_ENV = {**os.environ, "GIT_ALLOW_PROTOCOL": "https:http", "GIT_TERMINAL_PROMPT": "0"}


class LinkError(ValueError):
    pass


def _check(url: str) -> urllib.parse.SplitResult:
    u = urllib.parse.urlsplit(url.strip())
    if u.scheme not in ("https", "http") or not u.netloc or url.strip().startswith("-"):
        raise LinkError("Paste an https:// link: a BSP layer git repository, a conf/machine/*.conf file, or a product page")
    return u


def _get(url: str, limit: int = MAX_PAGE) -> tuple[bytes, str, str]:
    from .sync import PoliteFetcher
    if not PoliteFetcher(min_interval=0).allowed(url):
        raise LinkError(f"{urllib.parse.urlsplit(url).netloc} does not allow automated access to this page (robots.txt)")
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,text/plain,*/*"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read(limit + 1)[:limit], r.headers.get_content_type(), r.geturl()


def _raw_url(url: str) -> str:
    """github.com/o/r/blob/b/p -> raw.githubusercontent.com/o/r/b/p; gitlab /-/blob/ -> /-/raw/; cgit /tree/ -> /plain/."""
    u = urllib.parse.urlsplit(url)
    if u.netloc == "github.com" and "/blob/" in u.path:
        o, r, _, rest = u.path.strip("/").split("/", 3)
        return f"https://raw.githubusercontent.com/{o}/{r}/{rest}"
    if "/-/blob/" in u.path:
        return url.replace("/-/blob/", "/-/raw/", 1)
    if "/tree/" in u.path and u.netloc.startswith("git."):
        return url.replace("/tree/", "/plain/", 1)
    return url


def _git_repo(url: str) -> tuple[str, list[str]] | None:
    """(clone URL, branch names) when the link is a git repository; github/gitlab tree links are cut to the repo."""
    u = urllib.parse.urlsplit(url)
    cands = [url]
    parts = u.path.strip("/").split("/")
    if u.netloc in ("github.com", "gitlab.com") and len(parts) >= 2:
        cands.insert(0, f"https://{u.netloc}/{parts[0]}/{parts[1].removesuffix('.git')}")
    if "/-/" in u.path:
        cands.insert(0, url.split("/-/", 1)[0])
    for c in dict.fromkeys(cands):
        try:
            r = subprocess.run(["git", "ls-remote", "--heads", "--", c], capture_output=True, text=True, timeout=30, env=GIT_ENV)
        except (OSError, subprocess.SubprocessError):
            return None
        if r.returncode == 0 and r.stdout.strip():
            return c, [ln.split("refs/heads/", 1)[1] for ln in r.stdout.splitlines() if "refs/heads/" in ln]
    return None


def _pick_branch(branches: list[str], release: str) -> str:
    for want in (release, f"{release}-", "master", "main"):
        hit = [b for b in branches if b == want or (want.endswith("-") and b.startswith(want))]
        if hit:
            return sorted(hit)[-1]
    return branches[0]


def from_git(cat: Catalog, url: str, branches: list[str], release: str = "wrynose") -> dict:
    name = re.sub(r"\.git$", "", urllib.parse.urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]) or "layer"
    branch = _pick_branch(branches, release)
    from . import paths
    cache = paths.cache_dir() / "links"
    cache.mkdir(parents=True, exist_ok=True)
    repo = D.fetch_conf(re.sub(r"[^\w.-]", "_", f"{urllib.parse.urlsplit(url).netloc}_{name}"), url, branch, cache)
    layers = D.scan_layers(repo, name)
    machines = D.scan_machines(repo, name, cat.list("soc"), layers)
    if not machines:
        raise LinkError(f"{url} ({branch}) has no conf/machine/*.conf: it is not a BSP layer")
    for m in machines:
        m["repo"] = name
    repos = {name: {"url": url, "branches": {release: branch, "master": branch}}}
    D.save_overlay(D.resolve_needs({k: {"repo": name, **v} for k, v in layers.items()}, D.load_layers()["layer_catalog"]), repos)
    known = {b.get("machine") for b in cat.list("board")}
    recs = D.board_records(machines, repos, known)
    with cat.bulk():
        for r in recs:
            cat.upsert("board", r, source_url=url, confidence="medium", from_sync=True)
    note = "" if branch == release or branch.startswith(release) else f" (no {release} branch; used {branch}: check that it builds with {release})"
    return {"kind": "git", "source": f"{url} @ {branch}{note}", "added": len(recs), "found": len(machines),
            "boards": [{"id": r["id"], "name": r["name"], "soc": r["soc"]} for r in recs][:200]}


def from_machine_conf(cat: Catalog, url: str, text: str) -> dict:
    stem = re.sub(r"\.conf$", "", urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1])
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp) / "meta-link" / "conf" / "machine"
        d.mkdir(parents=True)
        (d / f"{stem}.conf").write_text(text)
        m = D.scan_machines(Path(tmp) / "meta-link", "meta-link", cat.list("soc"), {})[0]
    parts = urllib.parse.urlsplit(url).path.split("/")
    layer = next((parts[i - 1] for i in range(len(parts)) if parts[i] == "conf" and i > 0), "") or "unknown-layer"
    rec = {"id": f"u-{stem}", "name": m["name"], "vendor": D.VENDOR_BY_LAYER.get(layer, layer), "soc": m["soc"], "status": "user",
           "layers": [layer], "machine": stem, "description": m["description"], "url": url, "preset": [],
           "ram": {"type": "", "size_mb": 0, "bus_width": 0}, "source": "machine file link"}
    cat.add_user_record("board", rec)
    return {"kind": "machine", "source": url, "added": 1, "found": 1, "boards": [{"id": rec["id"], "name": rec["name"], "soc": rec["soc"]}],
            "note": f"Add the repository of layer {layer} as a link too, so a generated BSP can include it"}


def _meta(page: str, *names: str) -> str:
    for n in names:
        m = re.search(r'<meta[^>]+(?:property|name)=["\']' + re.escape(n) + r'["\'][^>]*content=["\']([^"\']+)', page, re.I) or \
            re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]*(?:property|name)=["\']' + re.escape(n), page, re.I)
        if m:
            return html.unescape(m.group(1)).strip()
    return ""


def guess_soc(text: str, socs: list[dict]) -> str:
    """The catalogue SoC named most often on the page ('i.MX 8M Mini', 'AM3358' -> am335x, 'RK3568')."""
    t = re.sub(r"[\W_]+", "", text.lower())
    best, count = "", 0
    for s in socs:
        keys = {re.sub(r"[\W_]+", "", k.lower()) for k in (s["id"], s.get("name", ""))} - {""}
        n = sum(t.count(k) for k in keys if len(k) >= 5)
        if n > count:
            best, count = s["id"], n
    return best


def from_page(cat: Catalog, url: str, body: bytes, final: str) -> dict:
    page = body.decode("utf-8", errors="replace")
    title = _meta(page, "og:title", "twitter:title") or html.unescape(re.sub(r"\s+", " ", (re.search(r"<title[^>]*>(.*?)</title>", page, re.I | re.S) or [None, ""])[1])).strip()
    title = re.split(r"\s+[|–—-]\s+", title)[0].strip() or urllib.parse.urlsplit(final).netloc
    desc = _meta(page, "og:description", "description", "twitter:description")
    text = re.sub(r"<[^>]+>", " ", re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", page))
    soc = guess_soc(f"{title} {desc} {text}", cat.list("soc"))
    host = urllib.parse.urlsplit(final).netloc.removeprefix("www.")
    rid = "u-" + re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60]
    rec = {"id": rid, "name": title, "vendor": _meta(page, "og:site_name") or host, "soc": soc, "status": "user", "layers": [], "machine": "",
           "description": desc, "url": final, "preset": [], "ram": {"type": "", "size_mb": 0, "bus_width": 0}, "source": "product page link"}
    cat.add_user_record("board", rec)
    img = _meta(page, "og:image", "twitter:image")
    pic = ""
    if img:
        try:
            data, ctype, _ = _get(urllib.parse.urljoin(final, img), 5 * 1024 * 1024)
            if ctype in ("image/jpeg", "image/png", "image/webp"):
                cat.set_image(rid, ctype, data, source_url=urllib.parse.urljoin(final, img), licence=f"© {host} (product page)",
                              attribution=host, shareable=False)
                pic = "picture saved on this machine only"
        except Exception:  # noqa: BLE001
            pic = "picture could not be downloaded"
    return {"kind": "page", "source": final, "added": 1, "found": 1, "boards": [{"id": rid, "name": title, "soc": soc}],
            "note": ("SoC recognised from the page: " + soc if soc else "SoC not recognised: open the board and choose it, or add the BSP layer's git link")
                    + (f"; {pic}" if pic else "") + ". A product page has no Yocto machine: add the BSP layer's git link to generate."}


def add_from_url(cat: Catalog, url: str, release: str = "wrynose") -> dict:
    _check(url)
    url = url.strip()
    if not urllib.parse.urlsplit(url).path.endswith(".conf"):
        repo = _git_repo(url)
        if repo:
            return from_git(cat, repo[0], repo[1], release)
    raw = _raw_url(url)
    body, ctype, final = _get(raw)
    if raw.endswith(".conf") or ctype == "text/plain" and b"MACHINE" in body[:20000] or re.search(rb"^#@TYPE:\s*Machine", body, re.M):
        return from_machine_conf(cat, url, body.decode("utf-8", errors="replace"))
    if ctype not in ("text/html", "application/xhtml+xml"):
        raise LinkError(f"{final} is {ctype}, not a git repository, machine file or web page")
    return from_page(cat, url, body, final)
