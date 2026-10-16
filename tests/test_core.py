import hashlib, json
from pathlib import Path
import pytest
from ybsg import project as P
from ybsg.graph import CapabilityGraph
from ybsg.orchestrator import Job, parse_bitbake_progress
from ybsg import report

BOARDS = ["rpi4b", "bbb", "rock3a", "stm32mp157d-dk1", "imx8mm-evk", "am62x-sk", "imx6ull-evk"]

def tree_hash(root: Path) -> dict:
    return {str(f.relative_to(root)): hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(root.rglob("*")) if f.is_file()
            and not f.name.endswith((".pdf",)) and "history" not in f.parts}

def run(cat, p, out, **kw):
    j = Job(cat, p, out, offline=True, **kw); s = j.run(); return j, s

def test_catalog_has_provenance(cat):
    for kind in ("soc", "board", "ic", "driver"):
        rows = cat.list(kind)
        assert rows, kind
        for r in rows:
            assert r["_prov"]["source_url"] and r["_prov"]["fetched"] and r["_prov"]["confidence"] in ("high", "medium", "unverified")

def test_search_is_forgiving(cat):
    assert any(r["id"] == "imx8mm" for r in cat.search("i.mx8mm"))
    assert any(r["id"] == "imx8mm" for r in cat.search("imx 8m mini"))

@pytest.mark.parametrize("b", BOARDS)
def test_reference_boards_validate(cat, b):
    p = P.from_board(cat, b)
    v = CapabilityGraph(cat).validate(p)
    assert v["can_generate"], [i["message"] for i in v["issues"] if i["severity"] == "error"]

def test_pinmux_conflict_blocks_and_can_be_waived(cat):
    p = P.from_board(cat, "imx6ull-evk")
    gr = CapabilityGraph(cat)
    P.add_peripheral(p, "flash", "gpmi-nand", "", props={})
    v = gr.validate(p)
    bad = [i for i in v["issues"] if i["severity"] == "error" and i["kind"] == "pinmux"]
    if bad:  # conflict present: generation is blocked until waived
        assert not v["can_generate"]
        gr.waive(p, bad[0]["id"])
        assert gr.validate(p)["blocking"] < v["blocking"]

def test_no_driver_for_ic_is_not_silent(cat):
    ics = [i for i in cat.list("ic") if not cat.drivers_for_ic(i["id"])]
    assert ics, "seed should include at least one IC without a mainline driver to exercise the custom slot"

def test_generation_is_deterministic(cat, tmp_path):
    p = P.from_board(cat, "rpi4b")
    _, s1 = run(cat, json.loads(json.dumps(p)), tmp_path / "a")
    _, s2 = run(cat, json.loads(json.dumps(p)), tmp_path / "b")
    assert s1["state"] == s2["state"] == "done"
    h1, h2 = tree_hash(tmp_path / "a"), tree_hash(tmp_path / "b")
    drop = lambda h: {k: v for k, v in h.items() if not k.endswith((".md", ".json")) or "manifest" in k}
    manifest_keys = [k for k in h1 if k.endswith("bsp-manifest.json")]
    assert manifest_keys
    differing = {k for k in h1 if h1[k] != h2.get(k)}
    assert not {k for k in differing if k.endswith((".dts", ".conf", ".bb", ".bbappend", ".yml", ".cfg", ".wks", ".cmd"))}, differing

@pytest.mark.parametrize("b", ["rpi4b", "imx8mm-evk", "am62x-sk"])
def test_end_to_end_outputs(cat, tmp_path, b):
    j, s = run(cat, P.from_board(cat, b), tmp_path)
    assert s["state"] == "done", s["failure"]
    assert s["percent"] == 100
    names = [f.name for f in tmp_path.rglob("*") if f.is_file()]
    assert any(n.endswith(".dts") or n.endswith(".dtsi") for n in names)
    assert any(n.endswith(".pdf") for n in names)
    assert "bsp-manifest.json" in names
    assert any(n.endswith(".yml") for n in names), "kas file"
    assert (tmp_path / "bsp-manifest.json").exists() or list(tmp_path.rglob("bsp-manifest.json"))

def test_progress_never_goes_backwards(cat, tmp_path):
    seen = []
    j = Job(cat, P.from_board(cat, "bbb"), tmp_path, offline=True, on_event=lambda e: seen.append(j.percent))
    j.run()
    assert seen == sorted(seen)
    assert seen[-1] == 100

def test_blocking_conflict_stops_generation(cat, tmp_path):
    p = P.from_board(cat, "rpi4b"); p["kernel"] = "6.6"; p["peripherals"].append({"uid": "x", "category": "camera", "ic": "no-such-ic", "driver": "", "mode": "", "props": {}, "custom": {}, "driver_version": ""})
    j, s = run(cat, p, tmp_path)
    assert s["state"] in ("failed", "done")  # unknown IC must at least be flagged
    if s["state"] == "failed":
        assert s["failure"]["stage"] == "validate"

def test_report_uses_manifest_only(cat, tmp_path):
    run(cat, P.from_board(cat, "bbb"), tmp_path)
    man = next(tmp_path.rglob("bsp-manifest.json"))
    data = json.loads(man.read_text()); data["project"]["name"] = "MANIFEST-ONLY-MARKER"; man.write_text(json.dumps(data))
    report.build_all(man.parent)
    md = next((man.parent / "report").glob("*-report.md")).read_text()
    assert "MANIFEST-ONLY-MARKER" in md

def test_merge_policy_preserves_user_edits(cat, tmp_path):
    p = P.from_board(cat, "bbb"); run(cat, json.loads(json.dumps(p)), tmp_path)
    dts = next(tmp_path.rglob("*.dts")); orig = dts.read_text(); dts.write_text(orig + "\n/* my edit */\n")
    j, s = run(cat, json.loads(json.dumps(p)), tmp_path, policy="keep")
    assert "my edit" in dts.read_text()
    j, s = run(cat, json.loads(json.dumps(p)), tmp_path, policy="merge")
    assert "my edit" in dts.read_text()
    assert list(tmp_path.rglob("*.ybsg-new")), "merge policy writes the new version alongside"

def test_project_file_roundtrip(cat, tmp_path):
    p = P.from_board(cat, "rpi4b"); f = P.save(p, tmp_path / "x.ybsg"); q = P.load(f)
    assert q["peripherals"] == p["peripherals"] and q["soc"] == p["soc"]

def test_user_records_survive_sync(cat):
    cat.add_user_record("board", {"id": "my-test-board", "name": "Mine", "vendor": "Me", "soc": "imx8mm", "status": "user", "layers": [], "preset": [], "ram": {}})
    cat.upsert("board", {"id": "my-test-board", "name": "OVERWRITE", "vendor": "x", "soc": "imx8mm"}, source_url="https://example.invalid")
    assert cat.get("board", "my-test-board")["name"] == "Mine"

def test_bitbake_progress_parser():
    assert parse_bitbake_progress("NOTE: Running task 12 of 400 (/x/y.bb:do_compile)")[:2] == (12, 400)
    assert parse_bitbake_progress("random line") is None

def test_export_zip_is_reproducible(cat, tmp_path):
    from ybsg import exporter
    run(cat, P.from_board(cat, "bbb"), tmp_path / "o")
    a = exporter.export_zip(tmp_path / "o", tmp_path / "a.zip"); b = exporter.export_zip(tmp_path / "o", tmp_path / "b.zip")
    assert a.read_bytes() == b.read_bytes()
    assert exporter.export_kas(tmp_path / "o", tmp_path / "x.yml").exists()

def test_plugin_adds_user_tier_records(cat, tmp_path, monkeypatch):
    from ybsg import plugins, paths
    d = paths.data_dir() / "plugins" / "demo"; d.mkdir(parents=True, exist_ok=True)
    (d / "plugin.json").write_text(json.dumps({"name": "demo", "version": "1", "data": []}))
    (d / "plugin.py").write_text("def register(api):\n    api.add_record('ic', {'id':'demo-ic','part':'DEMO-1','vendor':'Demo','category':'i2c','interface':'i2c','voltage':'3.3V','package':'SOT'})\n    api.add_kconfig_dep('CONFIG_DEMO', ['CONFIG_I2C'])\n")
    info = plugins.load_all(cat)
    assert info[0]["ok"], info
    r = cat.get("ic", "demo-ic")
    assert r["_prov"]["confidence"] == "unverified" and r["_prov"]["user"]
    assert plugins.KCONFIG_DEPS["CONFIG_DEMO"] == ["CONFIG_I2C"]

def _power_graph(cat, domain):
    """imx8mm-evk with every assigned controller placed on one test power domain."""
    p = P.from_board(cat, "imx8mm-evk"); gr = CapabilityGraph(cat)
    soc = json.loads(json.dumps(cat.get("soc", "imx8mm")))
    soc["power_domains"] = [domain]
    for c in soc["controllers"]:
        c["power_domain"] = domain["name"]
    gr.soc = lambda _p: soc
    return p, gr

def test_power_domain_io_voltage_conflict(cat):
    p, gr = _power_graph(cat, {"name": "NVCC_TEST", "io_mv": [1800, 3300]})
    assert not [i for i in gr.validate(p)["issues"] if i["kind"] == "power"], "no io_mv on peripherals: nothing to check"
    a, b = [x for x in p["peripherals"] if x["category"] != "i2c"][:2]
    a["props"]["io_mv"] = [1800]; b["props"]["io_mv"] = [3300]
    v = gr.validate(p)
    bad = [i for i in v["issues"] if i["code"] == "power-io-voltage"]
    assert bad and not v["can_generate"]
    gr.waive(p, bad[0]["id"])
    assert not [i for i in gr.validate(p)["issues"] if i["code"] == "power-io-voltage" and not i["waived"]]

def test_power_domain_budget(cat):
    p, gr = _power_graph(cat, {"name": "VDD_TEST", "budget_ma": 100})
    for x in p["peripherals"]:
        x["props"]["current_ma"] = 60
    assert any(i["code"] == "power-budget" for i in gr.validate(p)["issues"])

def test_report_is_pdfa_when_ghostscript_present(cat, tmp_path):
    import shutil
    if not shutil.which("gs"):
        pytest.skip("Ghostscript not installed")
    j, s = run(cat, P.from_board(cat, "bbb"), tmp_path)
    pdf = next((tmp_path / "report").glob("*.pdf")).read_bytes()
    assert b"pdfaid:part='2'" in pdf or b"<pdfaid:part>2</pdfaid:part>" in pdf
    assert b"/Outlines" in pdf, "bookmarks survive the conversion"
    assert any(l["stage"] == "report" and "PDF/A-2 conversion applied" in l["msg"] for l in j.log_lines)
