"""Unit tests for manifest.py: parsing, validation, and the version
constraint comparator. Pure, no filesystem. (wq#3810 lifecycle steps 1-2,6.)"""
import pytest

import manifest as m

GOOD = """
[plugin]
name = "ghidra-mcp"
version = "0.3.1"
kind = "tool"
summary = "Ghidra MCP server"

[dependencies]
ghidra = ">=11.0"

[health]
command = "ghidra-mcp --health"
interval_seconds = 30
timeout_seconds = 5

[runtime]
start = "start-it"
stop = "stop-it"
"""


def test_parse_good_manifest():
    man = m.parse_manifest(GOOD)
    assert man.name == "ghidra-mcp"
    assert man.version == "0.3.1"
    assert man.kind == "tool"
    assert man.dependencies == {"ghidra": ">=11.0"}
    assert man.health.command == "ghidra-mcp --health"
    assert man.health.interval_seconds == 30
    assert man.start == "start-it" and man.stop == "stop-it"


def test_no_health_table_is_allowed():
    man = m.parse_manifest('[plugin]\nname="hackrf"\nversion="1.0"\nkind="hardware"\n')
    assert man.health is None
    assert man.dependencies == {}


@pytest.mark.parametrize("bad, needle", [
    ('[plugin]\nname="X"\nversion="1.0"\nkind="tool"\n', "name"),            # uppercase name
    ('[plugin]\nname="a"\nversion="x"\nkind="tool"\n', "version"),           # non-numeric version
    ('[plugin]\nname="a"\nversion="1.0"\nkind="gadget"\n', "kind"),          # bad kind
    ('name="a"\n', "[plugin]"),                                             # no [plugin] table
])
def test_invalid_manifests_raise(bad, needle):
    with pytest.raises(m.ManifestError) as exc:
        m.parse_manifest(bad)
    assert any(needle in e for e in exc.value.errors)


def test_bad_dependency_constraint_rejected():
    bad = '[plugin]\nname="a"\nversion="1.0"\nkind="tool"\n[dependencies]\nb = "~>2"\n'
    with pytest.raises(m.ManifestError):
        m.parse_manifest(bad)


def test_category_optional_and_validated():
    man = m.parse_manifest('[plugin]\nname="a"\nversion="1.0"\nkind="hardware"\ncategory="sdr"\n')
    assert man.category == "sdr"
    man = m.parse_manifest('[plugin]\nname="a"\nversion="1.0"\nkind="hardware"\n')
    assert man.category == ""
    with pytest.raises(m.ManifestError) as exc:
        m.parse_manifest('[plugin]\nname="a"\nversion="1.0"\nkind="hardware"\ncategory="made-up"\n')
    assert any("category" in e for e in exc.value.errors)


def test_data_kind_is_valid():
    man = m.parse_manifest('[plugin]\nname="a"\nversion="1.0"\nkind="data"\n')
    assert man.kind == "data"


def test_conflicts_parsed_and_validated():
    man = m.parse_manifest(
        '[plugin]\nname="a"\nversion="1.0"\nkind="hardware"\nconflicts=["b", "c"]\n')
    assert man.conflicts == ["b", "c"]
    with pytest.raises(m.ManifestError) as exc:
        m.parse_manifest('[plugin]\nname="a"\nversion="1.0"\nkind="hardware"\nconflicts=["a"]\n')
    assert any("itself" in e for e in exc.value.errors)
    with pytest.raises(m.ManifestError):
        m.parse_manifest('[plugin]\nname="a"\nversion="1.0"\nkind="hardware"\nconflicts=["BAD NAME"]\n')


def test_malformed_toml_fails_closed():
    with pytest.raises(m.ManifestError) as exc:
        m.parse_manifest('[plugin\nname=')
    assert any("invalid TOML" in e for e in exc.value.errors)


@pytest.mark.parametrize("version, constraint, ok", [
    ("11.2", ">=11.0", True),
    ("11.0", ">=11.0", True),
    ("10.9", ">=11.0", False),
    ("11.2", ">11.2", False),
    ("11.3", ">11.2", True),
    ("1.0.0", "==1.0", True),      # zero-padding: 1.0.0 == 1.0
    ("2.0", "<=2.0", True),
    ("2.1", "<2.0", False),
    ("9.9", "*", True),
    ("9.9", "any", True),
    ("9.9", "", True),
    ("9.9", "garbage", False),      # unparseable constraint fails closed
])
def test_version_satisfies(version, constraint, ok):
    assert m.version_satisfies(version, constraint) is ok
