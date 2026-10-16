"""Collect every t("...") string from the web UI into ybsg/web/locales/en.json (gettext-style keys).
Translators copy en.json to <lang>.json and fill in the values; the UI falls back to the English text."""
import json, re
from pathlib import Path
root = Path(__file__).resolve().parent.parent / "ybsg" / "web"
src = (root / "app.js").read_text() + (root / "index.html").read_text()
found = set(re.findall(r"""\bt\(\s*'((?:[^'\\]|\\.)*)'\s*\)""", src)) | set(re.findall(r"""\bt\(\s*"((?:[^"\\]|\\.)*)"\s*\)""", src))
found |= set(re.findall(r"""\bt\(\s*`([^`$]*)`\s*\)""", src))
(root / "locales").mkdir(exist_ok=True)
(root / "locales" / "en.json").write_text(json.dumps({k: "" for k in sorted(found)}, indent=1, ensure_ascii=False) + "\n")
print(len(found), "strings written")
