"""wq#3807 — tests for the LIMPET tool-registry manifest subsystem.

Driven by a committed, rev-pinned fixture catalog (``fixtures/tool-catalog.sample.json``)
whose entries deliberately use the catalog's own key names (``binary``, ``caps``,
``healthcheck``, ...) rather than the manifest schema's — a falsifiable probe of
the importer's alias mapping, from outside the canonical field set (wq#2630).
"""

import json
import os
from pathlib import Path

import pytest

from limpet.registry import registry, schema, watch
from limpet.registry import register as register_mod
from limpet.registry import health as health_mod
from limpet.registry import cli
from limpet.registry.import_catalog import import_catalog, load_catalog

FIXTURE = Path(__file__).parent / "fixtures" / "tool-catalog.sample.json"

VALID = {
    "name": "ghidra",
    "path": "/opt/ghidra/ghidraRun",
    "capabilities": ["decompilation"],
    "health_check": "/opt/ghidra/ghidraRun -version",
}


def _write(tools_dir, name, body):
    (tools_dir / f"{name}.yaml").write_text(body, encoding="utf-8")


# --- schema ---------------------------------------------------------------

def test_validate_rejects_missing_required():
    errors = schema.validate_tool({"name": "x"})
    assert any("path" in e for e in errors)
    assert any("capabilities" in e for e in errors)
    assert any("health_check" in e for e in errors)


def test_validate_rejects_bad_name_and_unknown_field():
    errors = schema.validate_tool({**VALID, "name": "Bad Name", "bogus": 1})
    assert any("illegal chars" in e for e in errors)
    assert any("unknown field" in e for e in errors)


def test_validate_rejects_wrong_types():
    errors = schema.validate_tool({**VALID, "capabilities": "notalist"})
    assert any("capabilities must be a list" in e for e in errors)


def test_normalize_fills_defaults():
    tool = schema.normalize_tool(VALID)
    assert tool["version"] is None
    assert tool["input_formats"] == []
    assert tool["hardware_requirements"] == {}
    assert tool["mcp_endpoint"] is None


def test_normalize_raises_on_invalid():
    with pytest.raises(schema.ManifestError):
        schema.normalize_tool({"name": "x"})


# --- load_tool_dir --------------------------------------------------------

def test_load_tool_dir_skips_malformed_and_dedups(tmp_path):
    _write(tmp_path, "good", "name: good\npath: /bin/good\ncapabilities: [a]\nhealth_check: /bin/good -v\n")
    _write(tmp_path, "broken", "name: broken\npath: /bin/broken\n")  # no caps/health
    _write(tmp_path, "notyaml", ": : not valid : :\n- [\n")
    tools, errors = registry.load_tool_dir(str(tmp_path))
    names = [t["name"] for t in tools]
    assert names == ["good"]
    assert len(errors) == 2  # broken + notyaml, good survives


def test_load_tool_dir_reports_duplicate_name(tmp_path):
    body = "name: dup\npath: /bin/dup\ncapabilities: [a]\nhealth_check: /bin/dup -v\n"
    _write(tmp_path, "aaa", body)
    _write(tmp_path, "zzz", body)
    tools, errors = registry.load_tool_dir(str(tmp_path))
    assert len(tools) == 1
    assert any("duplicate tool name" in e for e in errors)


def test_load_missing_dir():
    tools, errors = registry.load_tool_dir("/nonexistent/limpet/tools.d")
    assert tools == []
    assert any("does not exist" in e for e in errors)


# --- build_manifest -------------------------------------------------------

def test_build_manifest_indices():
    tools = [
        schema.normalize_tool({**VALID, "name": "a", "capabilities": ["x", "y"]}),
        schema.normalize_tool({**VALID, "name": "b", "capabilities": ["y"],
                               "mcp_endpoint": "http://h/mcp"}),
    ]
    m = registry.build_manifest(tools, host="limpet", generated_at="T")
    assert m["tool_count"] == 2
    assert m["capabilities"]["y"] == ["a", "b"]
    assert m["capabilities"]["x"] == ["a"]
    assert m["mcp_endpoints"] == {"b": "http://h/mcp"}


# --- rebuild (idempotency + atomic write) ---------------------------------

def test_rebuild_writes_and_is_idempotent(tmp_path):
    tools_dir = tmp_path / "tools.d"
    tools_dir.mkdir()
    _write(tools_dir, "good", "name: good\npath: /bin/good\ncapabilities: [a]\nhealth_check: /bin/good -v\n")
    manifest_path = tmp_path / "tools.json"

    registry.rebuild(str(tools_dir), str(manifest_path))
    assert manifest_path.exists()
    mtime1 = manifest_path.stat().st_mtime_ns

    # No change in tools.d -> no rewrite (watcher stays quiet).
    registry.rebuild(str(tools_dir), str(manifest_path))
    assert manifest_path.stat().st_mtime_ns == mtime1

    # A new tool -> content changes -> rewrite.
    _write(tools_dir, "two", "name: two\npath: /bin/two\ncapabilities: [b]\nhealth_check: /bin/two -v\n")
    registry.rebuild(str(tools_dir), str(manifest_path))
    data = json.loads(manifest_path.read_text())
    assert data["tool_count"] == 2


# --- register round-trip --------------------------------------------------

def test_register_round_trip(tmp_path):
    dest = register_mod.register_tool(VALID, str(tmp_path))
    assert os.path.basename(dest) == "ghidra.yaml"
    tools, errors = registry.load_tool_dir(str(tmp_path))
    assert errors == []
    assert tools[0]["name"] == "ghidra"


def test_register_no_overwrite_raises(tmp_path):
    register_mod.register_tool(VALID, str(tmp_path))
    with pytest.raises(FileExistsError):
        register_mod.register_tool(VALID, str(tmp_path), overwrite=False)


def test_register_invalid_raises(tmp_path):
    with pytest.raises(schema.ManifestError):
        register_mod.register_tool({"name": "x"}, str(tmp_path))


def test_unregister(tmp_path):
    register_mod.register_tool(VALID, str(tmp_path))
    assert register_mod.unregister_tool("ghidra", str(tmp_path)) is True
    assert register_mod.unregister_tool("ghidra", str(tmp_path)) is False


# --- import_catalog (alias mapping, skip malformed) -----------------------

def test_import_catalog_from_fixture(tmp_path):
    written, errors = import_catalog(str(FIXTURE), str(tmp_path))
    assert len(written) == 3  # unblob, jadx, rea-mcp; the 4th row is skipped
    assert len(errors) == 1

    tools, load_errors = registry.load_tool_dir(str(tmp_path))
    assert load_errors == []
    by_name = {t["name"]: t for t in tools}

    # Alias mapping: unblob used binary/ver/caps/inputs/arches/healthcheck.
    unblob = by_name["unblob"]
    assert unblob["path"] == "/usr/local/bin/unblob"
    assert unblob["version"] == "24.3.14"
    assert "firmware-extraction" in unblob["capabilities"]
    assert unblob["input_formats"] == ["firmware-blob", "img"]
    assert unblob["health_check"] == "/usr/local/bin/unblob --help"

    # mcp alias -> mcp_endpoint.
    assert by_name["rea-mcp"]["mcp_endpoint"] == "http://127.0.0.1:8737/mcp"


def test_import_synthesizes_health_check(tmp_path):
    # jadx in the fixture has no health field; importer derives "<path> --version".
    import_catalog(str(FIXTURE), str(tmp_path))
    tools = {t["name"]: t for t in registry.load_tool_dir(str(tmp_path))[0]}
    assert tools["jadx"]["health_check"] == "/opt/jadx/bin/jadx --version"


def test_import_no_synth_skips_tools_without_health(tmp_path):
    written, errors = import_catalog(str(FIXTURE), str(tmp_path), synth_health_check=False)
    # jadx has no health field and now gets no synthesized one -> skipped.
    assert "jadx" not in [os.path.basename(p)[:-5] for p in written]
    assert any("jadx" in e and "health_check" in e for e in errors)


def test_load_catalog_accepts_bare_list(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps([{"name": "t", "path": "/bin/t"}]))
    assert load_catalog(str(p)) == [{"name": "t", "path": "/bin/t"}]


# --- watch ----------------------------------------------------------------

def test_watch_signature_changes_on_add(tmp_path):
    assert watch._signature(str(tmp_path)) == ()
    _write(tmp_path, "good", "name: good\npath: /bin/g\ncapabilities: [a]\nhealth_check: /bin/g -v\n")
    assert watch._signature(str(tmp_path)) != ()


def test_watch_initial_rebuild(tmp_path):
    tools_dir = tmp_path / "tools.d"
    tools_dir.mkdir()
    _write(tools_dir, "good", "name: good\npath: /bin/g\ncapabilities: [a]\nhealth_check: /bin/g -v\n")
    manifest_path = tmp_path / "tools.json"
    rebuilds = watch.watch(str(tools_dir), str(manifest_path),
                           interval=0.01, max_iterations=1)
    assert rebuilds >= 1
    assert manifest_path.exists()


# --- health ---------------------------------------------------------------

def test_health_check_pass_and_fail():
    assert health_mod.run_health_check("true")["ok"] is True
    assert health_mod.run_health_check("false")["ok"] is False


def test_health_check_timeout():
    r = health_mod.run_health_check("sleep 5", timeout=0.1)
    assert r["ok"] is False
    assert "timed out" in r["reason"]


def test_check_tools_reports_per_tool():
    tools = [
        {"name": "a", "health_check": "true"},
        {"name": "b", "health_check": "false"},
    ]
    results = {r["name"]: r for r in health_mod.check_tools(tools)}
    assert results["a"]["ok"] is True
    assert results["b"]["ok"] is False


# --- cli integration ------------------------------------------------------

def test_cli_build_list_health(tmp_path, capsys):
    tools_dir = tmp_path / "tools.d"
    tools_dir.mkdir()
    _write(tools_dir, "t", "name: t\npath: /bin/true\ncapabilities: [probe]\nhealth_check: 'true'\n")
    manifest_path = tmp_path / "tools.json"

    assert cli.main(["build", "--tools-dir", str(tools_dir),
                     "--manifest", str(manifest_path)]) == 0
    assert manifest_path.exists()

    assert cli.main(["list", "--tools-dir", str(tools_dir),
                     "--capability", "probe"]) == 0
    assert "t" in capsys.readouterr().out

    assert cli.main(["health", "--tools-dir", str(tools_dir)]) == 0


def test_cli_build_nonzero_on_bad_manifest(tmp_path):
    tools_dir = tmp_path / "tools.d"
    tools_dir.mkdir()
    _write(tools_dir, "bad", "name: bad\npath: /bin/bad\n")  # no caps/health
    assert cli.main(["build", "--tools-dir", str(tools_dir),
                     "--manifest", str(tmp_path / "tools.json")]) == 1
