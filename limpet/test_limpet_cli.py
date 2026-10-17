"""Tests for the limpet CLI: exit codes, --json, and that example manifests
validate. Drives main(argv) with --plugins-dir/--state-dir pinned to tmp
(outside the /etc,/var defaults -- falsifiable boundary)."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cli  # noqa: E402

EXAMPLES = os.path.join(os.path.dirname(__file__), "examples")


def test_validate_example_manifests_ok(capsys):
    for name in ("ghidra", "ghidra-mcp", "hackrf", "component-id", "fw-diff"):
        rc = cli.main(["--json", "validate", os.path.join(EXAMPLES, f"{name}.toml")])
        assert rc == 0
        out = json.loads(capsys.readouterr().out)
        assert out["valid"] is True and out["name"] == name


def test_validate_bad_manifest_exit_2(tmp_path, capsys):
    bad = tmp_path / "bad.toml"
    bad.write_text('[plugin]\nname="x"\n')  # uppercase, missing fields
    rc = cli.main(["--json", "validate", str(bad)])
    assert rc == 2
    assert json.loads(capsys.readouterr().out)["valid"] is False


def test_enable_disable_roundtrip(tmp_path, capsys):
    plugins = tmp_path / "plugins.d"
    state = tmp_path / "state"
    plugins.mkdir()
    (plugins / "ghidra.toml").write_text(
        '[plugin]\nname="ghidra"\nversion="11.2"\nkind="tool"\n')
    base = ["--plugins-dir", str(plugins), "--state-dir", str(state), "--no-health", "--json"]

    assert cli.main(base + ["disable", "ghidra"]) == 0
    capsys.readouterr()
    assert cli.main(base + ["reconcile"]) == 0
    unified = json.loads(capsys.readouterr().out)
    assert unified["plugins"]["ghidra"]["state"] == "disabled"


def test_list_category_filter(tmp_path, capsys):
    plugins = tmp_path / "plugins.d"
    plugins.mkdir()
    (plugins / "hackrf.toml").write_text(
        '[plugin]\nname="hackrf"\nversion="1.0"\nkind="hardware"\ncategory="sdr"\n')
    (plugins / "ghidra.toml").write_text(
        '[plugin]\nname="ghidra"\nversion="1.0"\nkind="tool"\ncategory="disassembler"\n')
    base = ["--plugins-dir", str(plugins), "--state-dir", str(tmp_path / "state"),
            "--no-health", "--json"]

    rc = cli.main(base + ["list", "--category", "sdr"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert list(out["plugins"]) == ["hackrf"]


def test_enable_unknown_plugin_exit_1(tmp_path, capsys):
    plugins = tmp_path / "plugins.d"
    plugins.mkdir()
    rc = cli.main(["--plugins-dir", str(plugins), "--state-dir", str(tmp_path / "s"),
                   "enable", "nope"])
    assert rc == 1
    assert "no installed plugin" in capsys.readouterr().err
