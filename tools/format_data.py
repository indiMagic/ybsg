"""Rewrite data/*.json in the repository's layout: top-level keys on their own lines, one compact record per line.
Keeps diffs of the catalogue readable. Usage: python tools/format_data.py ybsg/data/seed.json ybsg/data/layers.json
Re-sign seed.json afterwards (tools/sign_snapshot.py)."""
import json
import sys
from pathlib import Path


def dump(d: dict) -> str:
    c = lambda v: json.dumps(v, ensure_ascii=False)
    out = ["{"]
    items = list(d.items())
    for i, (k, v) in enumerate(items):
        end = "," if i < len(items) - 1 else ""
        if isinstance(v, list) and v:
            out.append(f"  {c(k)}: [")
            out += [f"    {c(x)}" + ("," if j < len(v) - 1 else "") for j, x in enumerate(v)]
            out.append("  ]" + end)
        elif isinstance(v, dict) and v and all(isinstance(x, dict) for x in v.values()):
            out.append(f"  {c(k)}: {{")
            ks = list(v)
            out += [f"    {c(x)}: {c(v[x])}" + ("," if j < len(ks) - 1 else "") for j, x in enumerate(ks)]
            out.append("  }" + end)
        else:
            out.append(f"  {c(k)}: {c(v)}{end}")
    return "\n".join(out + ["}"]) + "\n"


if __name__ == "__main__":
    for f in sys.argv[1:]:
        p = Path(f)
        p.write_text(dump(json.loads(p.read_text(encoding="utf-8"))), encoding="utf-8")
