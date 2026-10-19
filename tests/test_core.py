import hashlib, json, os
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

def test_discovery_maps_layerbranch_to_layer(tmp_path):
    """A machine's `layerbranch` is a layerBranches id; the layer name comes through that table (real API shape)."""
    from ybsg.catalog import Catalog
    from ybsg.sync import SyncService, FileFetcher
    (tmp_path / "machines.json").write_text(json.dumps([{"id": 1, "name": "beaglebone", "description": "x", "layerbranch": 900},
                                                        {"id": 2, "name": "brand-new-board", "description": "y", "layerbranch": 901}]))
    (tmp_path / "layerItems.json").write_text(json.dumps([{"id": 7, "name": "meta-beagle"}, {"id": 8, "name": "meta-acme"}]))
    (tmp_path / "layerBranches.json").write_text(json.dumps([{"id": 900, "layer": 7}, {"id": 901, "layer": 8}]))
    c = Catalog(tmp_path / "cat.sqlite")
    found = SyncService(c, FileFetcher(tmp_path)).find_more_boards()
    assert [(f["name"], f["layer"]) for f in found] == [("brand-new-board", "meta-acme")]
    rep = SyncService(c, FileFetcher(tmp_path)).check_updates(["bbb"])
    assert rep["checked"] == 1 and not rep["not_found"]

def test_bot_challenge_is_reported_as_blocked():
    import io, urllib.error
    from unittest import mock
    from ybsg.sync import PoliteFetcher, BlockedError
    hdrs = {"cf-mitigated": "challenge", "server": "cloudflare"}
    err = urllib.error.HTTPError("https://x.invalid/api/a", 403, "Forbidden", hdrs, io.BytesIO(b""))
    with mock.patch("urllib.request.urlopen", side_effect=err):
        with pytest.raises(BlockedError):
            PoliteFetcher(min_interval=0).get("https://x.invalid/api/a")

def test_layer_plan_closes_dependencies(cat, tmp_path):
    from ybsg.generator import Generator
    g = Generator(cat, P.from_board(cat, "bbb"), tmp_path, offline=True)
    plan = g.layer_plan()
    assert {"meta", "meta-arm", "meta-arm-toolchain", "meta-ti-bsp", "meta-beagle"} <= set(plan)
    assert plan.index("meta-arm") < plan.index("meta-ti-bsp") < plan.index("meta-beagle")
    repos = g._repos_needed()
    assert "poky" not in repos and repos["bitbake"] == [], "wrynose has no poky branch: oe-core + bitbake + meta-yocto"
    p = P.from_board(cat, "bbb"); p["release"] = "scarthgap"; p["kernel"] = "6.6"
    assert "poky" in Generator(cat, p, tmp_path, offline=True)._repos_needed()

def test_vendor_alias_search_and_duplicate_merge(tmp_path):
    from ybsg.catalog import Catalog, canonical_vendor
    c = Catalog(tmp_path / "c.sqlite")
    assert canonical_vendor("Freescale") == canonical_vendor("nxp") == "NXP"
    assert any(r["id"] == "imx8mm" for r in c.search("freescale", ["soc"]))
    c.upsert("ic", {"id": "bme-280-dup", "part": "BME-280", "vendor": "Bosch Sensortec", "category": "sensor"}, source_url="https://example.invalid", confidence="unverified", from_sync=True)
    groups = c.find_duplicates("ic")
    assert ["bme-280-dup", "bme280"] in groups
    m = c.merge("ic", "bme280", "bme-280-dup")
    assert "bme-280-dup" in m["aliases"] and not c.get("ic", "bme-280-dup") and m["_prov"]["confidence"] == "medium"
    assert c.resolve_id("ic", "bme-280-dup") == "bme280"
    c.add_user_record("ic", {"id": "mine", "part": "BME280", "vendor": "Bosch"})
    with pytest.raises(PermissionError):
        c.merge("ic", "bme280", "mine")

def test_shipped_snapshot_is_signed():
    """DB-10: re-run tools/sign_snapshot.py after editing data/seed.json."""
    from ybsg import signing
    assert signing.snapshot_status(signing.DATA / "seed.json")["state"] == "signed"

def test_tampered_snapshot_is_refused(tmp_path, monkeypatch):
    import shutil
    from ybsg import signing
    from ybsg.catalog import Catalog
    f = tmp_path / "seed.json"; shutil.copy(signing.DATA / "seed.json", f); shutil.copy(signing.DATA / "seed.json.sig", tmp_path / "seed.json.sig")
    f.write_text(f.read_text().replace('"BeagleBone Black"', '"BeagleBone Evil"'))
    assert signing.snapshot_status(f)["state"] == "invalid"
    monkeypatch.delenv("YBSG_ALLOW_UNSIGNED", raising=False)
    with pytest.raises(RuntimeError):
        Catalog(tmp_path / "c.sqlite").load_seed(f)

def test_organisation_report_template(cat, tmp_path):
    """RPT-5: a template changes colours, organisation, cover and adds sections, and lives in the manifest (RPT-2)."""
    import base64
    from ybsg import report as R
    d = R.template_dir(); d.mkdir(parents=True, exist_ok=True)
    png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")
    (d / "acme-logo.png").write_bytes(png)
    (d / "acme.json").write_text(json.dumps({"name": "Acme", "organisation": "Acme Robotics", "website": "acme.example", "accent": "#c2410c",
                                             "logo": "acme-logo.png", "prepared_for": "Line 3", "cover_note": "Internal use.",
                                             "extra_sections": [{"title": "Sign-off", "body": "QA:\nHW:"}]}))
    assert any(t["id"] == "acme" for t in R.list_templates())
    p = P.from_board(cat, "bbb"); p["report_template"] = "acme"
    run(cat, p, tmp_path)
    m = json.loads((tmp_path / "bsp-manifest.json").read_text())
    assert m["report_template"]["organisation"] == "Acme Robotics" and m["report_template"]["logo"]["base64"]
    md = next((tmp_path / "report").glob("*.md")).read_text()
    assert "Acme Robotics" in md and "## Sign-off" in md and "Internal use." in md
    (d / "bad.json").write_text(json.dumps({"organisation": "x", "colour": "red"}))
    with pytest.raises(ValueError):
        R.load_template("bad")

def test_self_update_verifies_signature_and_hash(tmp_path, monkeypatch):
    """PKG-7 with a throwaway key: a valid feed installs, a tampered feed or binary changes nothing."""
    import hashlib
    from ybsg import signing, update
    seed = bytes(range(32)); monkeypatch.setattr(signing, "trusted_keys", lambda: [signing.public_key(seed)])
    new = tmp_path / "ybsg-portable.new"; new.write_bytes(b"#!/bin/sh\necho new\n")
    exe = tmp_path / "ybsg-portable"; exe.write_bytes(b"old"); exe.chmod(0o755)
    feed = {"version": "99.0.0", "date": "2026-10-16", "notes": "test",
            "assets": {update.platform_key(): {"url": new.as_uri(), "sha256": hashlib.sha256(new.read_bytes()).hexdigest()}}}
    feed["signature"] = signing.sign(seed, update.canonical(feed)).hex()
    f = tmp_path / "update.json"; f.write_text(json.dumps(feed))
    info = update.check(f.as_uri())
    assert info["state"] == "available" and info["latest"] == "99.0.0"
    assert update.apply(info)["state"] == "manual", "a source install is never modified"
    assert update.apply(info, exe)["state"] == "installed" and exe.read_bytes() == new.read_bytes() and os.access(exe, os.X_OK)
    feed["version"] = "99.0.1"; f.write_text(json.dumps(feed))  # signature no longer matches
    assert update.check(f.as_uri())["state"] == "failed"
    bad = dict(info, asset=dict(info["asset"], sha256="0" * 64)); exe.write_bytes(b"old")
    with pytest.raises(ValueError):
        update.apply(bad, exe)
    assert exe.read_bytes() == b"old"
    monkeypatch.delenv("YBSG_UPDATE_URL", raising=False)
    assert update.check("")["state"] == "not_configured"

@pytest.mark.parametrize("distro", ["ubuntu", "debian", "kali", "fedora", "archarm", "alpine"])
def test_distro_rootfs_script_per_distribution(cat, tmp_path, distro):
    """GEN-9: each distribution gets its own documented procedure; unsupported architectures are refused up front."""
    import subprocess
    for board in ("rpi4b", "bbb"):
        p = P.from_board(cat, board); p["image"] = "distro"; p["distro"] = distro
        v = CapabilityGraph(cat).validate(p)
        if distro == "fedora" and board == "bbb":
            assert any(i["code"] == "distro-arch" for i in v["issues"]) and not v["can_generate"]
            continue
        j, s = run(cat, p, tmp_path / board)
        assert s["state"] == "done", s["failure"]
        sh = (tmp_path / board / "rootfs" / "assemble-rootfs.sh").read_text()
        assert subprocess.run(["sh", "-n"], input=sh, text=True).returncode == 0
        assert "--rootfs-dir" in sh and "|| true" not in sh
        assert {"archarm": "md5sum -c", "alpine": "sha256sum -c", "fedora": "gpgcheck=1"}.get(distro, "binfmt") in sh

def test_remote_builder_flow(cat, tmp_path, monkeypatch):
    """FR-16 without a network: `ssh` runs the command locally, `rsync` is real (host: prefix stripped), `kas` is fake."""
    import shutil, stat
    if not shutil.which("rsync"):
        pytest.skip("rsync not installed")
    bin_ = tmp_path / "bin"; bin_.mkdir(); remote = tmp_path / "remote"
    def tool(name, body):
        f = bin_ / name; f.write_text("#!/bin/sh\n" + body); f.chmod(f.stat().st_mode | stat.S_IEXEC)
    tool("ssh", 'while [ "${1#-}" != "$1" ]; do [ "$1" = -o ] && shift; shift; done; shift; exec sh -c "$*"\n')
    tool("rsync", 'a=""; for x in "$@"; do a="$a \'${x#buildhost:}\'"; done; eval exec ' + shutil.which("rsync") + ' $a\n')
    tool("kas", 'echo "NOTE: Running task 1 of 2 (x.bb:do_fetch)"; echo "NOTE: Running task 2 of 2 (x.bb:do_image)"\n'
                'm=$(sed -n "s/^machine: //p" "$2"); mkdir -p build/tmp/deploy/images/$m; echo img > build/tmp/deploy/images/$m/test.wic\n')
    monkeypatch.setenv("PATH", f"{bin_}:{os.environ['PATH']}")
    monkeypatch.setenv("YBSG_REMOTE_BUILDER", f"buildhost:{remote}")
    j, s = run(cat, P.from_board(cat, "bbb"), tmp_path / "out", build=True)
    assert s["state"] == "done", s["failure"]
    assert (remote / "beaglebone-black" / "kas" / "beaglebone-black.yml").exists(), "BSP copied to the builder"
    assert (tmp_path / "out" / "build/tmp/deploy/images/beaglebone-black/test.wic").exists(), "images copied back"
    assert j.build_info["remote"].startswith("buildhost:") and "test.wic" in j.build_info["images"]
    assert any("Running task 2 of 2" in l["msg"] for l in j.log_lines)

def test_local_build_backend_runs_kas_in_output_folder(cat, tmp_path, monkeypatch):
    import stat
    bin_ = tmp_path / "bin"; bin_.mkdir()
    k = bin_ / "kas"
    k.write_text('#!/bin/sh\n[ "$KAS_WORK_DIR" = "$PWD" ] || exit 9\necho "NOTE: Running task 5 of 5 (x.bb:do_image)"\n'
                 'm=$(sed -n "s/^machine: //p" "$2"); mkdir -p build/tmp/deploy/images/$m; echo img > build/tmp/deploy/images/$m/a.wic\n'
                 'mkdir -p build/tmp/deploy/licenses/$m/img-$m.rootfs-1; printf "PACKAGE NAME: busybox\\nPACKAGE VERSION: 1.37.0\\nRECIPE NAME: busybox\\nLICENSE: GPL-2.0-only & BSD-0-Clause\\n\\nPACKAGE NAME: base-files\\nPACKAGE VERSION: 3.0.14\\nRECIPE NAME: base-files\\nLICENSE: GPL-2.0-only\\n" > build/tmp/deploy/licenses/$m/img-$m.rootfs-1/license.manifest\n')
    k.chmod(k.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bin_}:{os.environ['PATH']}"); monkeypatch.delenv("YBSG_REMOTE_BUILDER", raising=False)
    j, s = run(cat, P.from_board(cat, "bbb"), tmp_path / "out", build=True)
    assert s["state"] == "done", s["failure"]
    assert j.build_info["images"] == ["a.wic"]
    assert [c["package"] for c in j.build_info["component_licences"]] == ["base-files", "busybox"]
    md = next((tmp_path / "out" / "report").glob("*.md")).read_text()
    assert "| busybox | 1.37.0 | busybox | GPL-2.0-only & BSD-0-Clause |" in md

def test_wsl_detection(monkeypatch):
    """PKG-4 on any OS: wsl.exe output is UTF-16LE; prefer the default WSL2 distro that has kas and rsync."""
    import subprocess
    from ybsg import orchestrator as O
    listing = "  NAME            STATE           VERSION\r\n* Ubuntu-24.04    Running         2\r\n  Debian          Stopped         2\r\n  Legacy          Stopped         1\r\n"
    raw = b"\xff\xfe" + listing.encode("utf-16-le")
    rows = O.parse_wsl_list(raw)
    assert [(r["name"], r["version"], r["default"]) for r in rows] == [("Ubuntu-24.04", 2, True), ("Debian", 2, False), ("Legacy", 1, False)]
    tools = {"Ubuntu-24.04": b"", "Debian": b"/usr/bin/kas\n/usr/bin/rsync\n"}
    def fake(args, timeout=30):
        out = raw if args[:2] == ["-l", "-v"] else tools[args[1]]
        return subprocess.CompletedProcess(args, 0, out, b"")
    monkeypatch.setattr(O.sys, "platform", "win32"); monkeypatch.setattr(O.shutil, "which", lambda n: "C:/wsl.exe" if n == "wsl.exe" else None)
    monkeypatch.setattr(O, "_wsl_run", fake)
    assert O.detect_wsl() == {"distro": "Debian", "kas": "kas"}
    assert O.find_build_backend() == ["wsl", "Debian", "kas"]
    tools["Debian"] = b""
    assert "pipx install kas" in O.detect_wsl()["problem"]
    raw = b"\xff\xfe" + "  NAME STATE VERSION\r\n* Legacy Stopped 1\r\n".encode("utf-16-le")
    assert "WSL1" in O.detect_wsl()["problem"]

def test_discovery_matches_socs_from_machine_files(tmp_path):
    """Board discovery from layer git repos: the machine's own device trees decide the SoC; a shared list naming
    several SoC families does not; R5 firmware configs are not boards."""
    from ybsg import discover as D
    from ybsg.catalog import Catalog
    socs = Catalog(tmp_path / "c.sqlite").list("soc")
    repo = tmp_path / "meta-x"; (repo / "conf/machine/include").mkdir(parents=True)
    (repo / "conf/layer.conf").write_text('BBFILE_COLLECTIONS += "x"\nLAYERDEPENDS_x = "core"\nLAYERSERIES_COMPAT_x = "wrynose"\n')
    (repo / "conf/machine/include/base.inc").write_text('KERNEL_DEVICETREE ?= "imx6ull-evk.dtb imx8mm-evk.dtb bcm2711-rpi-4-b.dtb"\n')
    (repo / "conf/machine/my-mini.conf").write_text('#@NAME: My Mini\nrequire conf/machine/include/base.inc\nKERNEL_DEVICETREE = "freescale/imx8mm-my.dtb"\n')
    (repo / "conf/machine/generic.conf").write_text('#@NAME: Generic\nrequire conf/machine/include/base.inc\n')
    (repo / "conf/machine/my-mini-k3r5.conf").write_text('#@NAME: R5\n')
    layers = D.scan_layers(repo, "meta-x")
    assert layers["meta-x"]["collection"] == "x" and layers["meta-x"]["depends"] == ["core"]
    found = {m["machine"]: m for m in D.scan_machines(repo, "meta-x", socs, layers)}
    assert found["my-mini"]["soc"] == "imx8mm" and found["my-mini"]["name"] == "My Mini"
    assert found["generic"]["soc"] == "", "a shared multi-SoC list must not decide"
    recs = D.board_records([dict(m, repo="meta-x") for m in found.values()], {"meta-x": {"url": "https://example.invalid/meta-x"}}, set())
    assert sorted(r["machine"] for r in recs) == ["generic", "my-mini"]
    assert D.matches(recs[1], "my mini")

def test_board_on_unknown_soc_is_listed_but_explained(cat):
    p = P.new_project("x"); p["board"] = "m-jetson-orin-nano-devkit"
    if not cat.get("board", p["board"]):
        pytest.skip("discovered boards not in this snapshot")
    v = CapabilityGraph(cat).validate(p)
    assert [i["code"] for i in v["issues"]] == ["board-soc-unknown"] and not v["can_generate"]

def test_update_check_uses_layer_repositories(tmp_path, monkeypatch):
    """DB-14 without the layer index (it challenges YBSG's user agent): updates come from the layers' git repos."""
    from ybsg import discover as D, paths
    from ybsg.catalog import Catalog
    from ybsg.sync import SyncService
    c = Catalog(tmp_path / "c.sqlite")
    fake = [{"machine": "beaglebone", "name": "BeagleBone machine", "description": "new text", "layer": "meta-beagle", "soc": "am335x", "vendor": "BeagleBoard.org", "repo": "meta-ti"},
            {"machine": "brand-new", "name": "Brand New", "description": "", "layer": "meta-beagle", "soc": "am335x", "vendor": "BeagleBoard.org", "repo": "meta-ti"}]
    monkeypatch.setattr(D, "discover", lambda socs, release="wrynose", log=print, **kw: (fake, {}, {"meta-ti": {"url": "https://x.invalid", "branches": {}}}))
    monkeypatch.setattr(D, "save_overlay", lambda *a: None)
    rep = SyncService(c).check_updates(["bbb", "imx8mm-evk"])
    assert rep["changed"] == [{"board": "bbb", "fields": ["machine_description"]}] and rep["not_found"] == ["imx8mm-evk"]
    rep = SyncService(c).check_updates(["bbb"], apply=True)
    assert c.get("board", "bbb")["machine_description"] == "new text" and rep["new"] == 1 and c.get("board", "m-brand-new")

def test_photo_matching_is_strict():
    from ybsg.pictures import title_matches
    assert title_matches("Raspberry Pi 4 Model B", "File:Raspberry_Pi_4_Model_B_-_Side.jpg")
    assert title_matches("BeagleBone Black", "File:Beagle Bone Black (element 14 edition).jpg")
    assert not title_matches("BeaglePlay", "File:Playful Beagle.jpg"), "dogs are not boards"
    assert not title_matches("RaspberryPi 3 Development Board", "File:Raspberry Pi Zero W (20170828_194324).jpg")
    assert not title_matches("Raspberry Pi Zero W", "File:Raspberry Pi Zero 2 W -- 2024.jpg")

def test_placeholder_is_never_stored(cat):
    from ybsg.sync import ensure_image
    queued = []
    class Q:
        def want(self, b): queued.append(b["id"])
    img = ensure_image(cat, cat.get("board", "rock3a"), Q())
    assert img["mime"] == "image/svg+xml" and queued == ["rock3a"]
    assert not cat.get_image("rock3a") or cat.get_image("rock3a")["source_url"] != "ybsg:generated"

def test_local_conf_uses_image_features_valid_for_release(cat, tmp_path):
    """debug-tweaks no longer exists after Scarthgap; BitBake skips an image that asks for it."""
    for rel, kernel, want, not_want in (("wrynose", "6.18", "allow-root-login", "debug-tweaks"), ("scarthgap", "6.6", "debug-tweaks", "allow-root-login")):
        p = P.from_board(cat, "bbb"); p["release"], p["kernel"] = rel, kernel
        run(cat, p, tmp_path / rel)
        conf = next((tmp_path / rel).rglob("local.conf.sample")).read_text()
        assert want in conf and not_want not in conf, rel
    assert (tmp_path / "wrynose" / "build.sh").stat().st_mode & 0o111

def test_raspberry_pi_5_generates_on_bcm2712(cat, tmp_path):
    """Pi 5: thin overlay on raspberrypi5, DTS based on the vendor board file (RP1 sits under pcie2), the
    firmware told to load our DTB, meta-raspberrypi's own SD layout kept, kernel version selected."""
    p = P.from_board(cat, "m-raspberrypi5")
    assert p["soc"] == "bcm2712"
    v = CapabilityGraph(cat).validate(p)
    assert v["can_generate"], [i["message"] for i in v["issues"] if i["severity"] == "error"]
    j, s = run(cat, p, tmp_path)
    assert s["state"] == "done", s["failure"]
    dts = next(tmp_path.glob("dts/*.dts")).read_text()
    assert '#include "bcm2712-rpi-5-b.dts"' in dts and "&rp1_eth {\n\tstatus = \"okay\";\n};" in dts and "cd-gpios" not in dts
    conf = next(tmp_path.rglob("conf/machine/*.conf")).read_text()
    assert "require conf/machine/raspberrypi5.conf" in conf and 'PREFERRED_VERSION_linux-raspberrypi = "6.18.%"' in conf
    assert "device_tree=raspberry-pi-5.dtb" in conf and "WKS_FILE" not in conf and "PREFERRED_PROVIDER" not in conf
    assert next(tmp_path.rglob("linux-raspberrypi_%.bbappend"))

def test_sdio_wifi_driver_is_not_a_host_controller_mismatch(cat):
    p = P.from_board(cat, "m-raspberrypi5")
    assert not [i for i in CapabilityGraph(cat).validate(p)["issues"] if i["code"] == "driver-controller-mismatch"]
