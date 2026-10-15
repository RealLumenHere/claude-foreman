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


def test_optional_dependencies_parsed_and_validated():
    man = m.parse_manifest(
        '[plugin]\nname="a"\nversion="1.0"\nkind="tool"\n'
        '[optional_dependencies]\nb = ">=2.0"\n')
    assert man.optional_dependencies == {"b": ">=2.0"}
    with pytest.raises(m.ManifestError) as exc:
        m.parse_manifest(
            '[plugin]\nname="a"\nversion="1.0"\nkind="tool"\n'
            '[optional_dependencies]\nb = "~>2"\n')
    assert any("optional_dependencies" in e for e in exc.value.errors)


def test_conflicts_parsed_and_validated():
    man = m.parse_manifest(
        '[plugin]\nname="a"\nversion="1.0"\nkind="hardware"\nconflicts=["b", "c"]\n')
    assert man.conflicts == ["b", "c"]
    with pytest.raises(m.ManifestError) as exc:
        m.parse_manifest('[plugin]\nname="a"\nversion="1.0"\nkind="hardware"\nconflicts=["a"]\n')
    assert any("itself" in e for e in exc.value.errors)
    with pytest.raises(m.ManifestError):
        m.parse_manifest('[plugin]\nname="a"\nversion="1.0"\nkind="hardware"\nconflicts=["BAD NAME"]\n')


def test_mutation_boundary_defaults_to_no_sudo():
    # No [mutation] table => undeclared (None), NOT silently read-only (wq#3998).
    man = m.parse_manifest('[plugin]\nname="a"\nversion="1.0"\nkind="tool"\n')
    assert man.mutation is None
    # An empty [mutation] table => REA's own boundary: no escalation, no network,
    # writes nothing.
    man = m.parse_manifest('[plugin]\nname="a"\nversion="1.0"\nkind="tool"\n[mutation]\n')
    assert man.mutation == m.MutationBoundary(escalates=False, network=False, writes=("none",))


def test_mutation_boundary_declares_wider_scope():
    man = m.parse_manifest(
        '[plugin]\nname="flash"\nversion="1.0"\nkind="hardware"\n'
        '[mutation]\nescalates=true\nnetwork=false\nwrites=["device"]\n')
    assert man.mutation.escalates is True
    assert man.mutation.writes == ("device",)


@pytest.mark.parametrize("bad, needle", [
    ('[mutation]\nwrites=["devices"]\n', "writes"),          # typo'd scope fails closed
    ('[mutation]\nescalates="yes"\n', "escalates"),          # non-bool flag
    ('[mutation]\nwrites=["none", "target"]\n', "none"),     # none cannot combine
    ('[mutation]\nwrites=[]\n', "non-empty"),                # empty writes list
])
def test_mutation_boundary_invalid_rejected(bad, needle):
    with pytest.raises(m.ManifestError) as exc:
        m.parse_manifest('[plugin]\nname="a"\nversion="1.0"\nkind="tool"\n' + bad)
    assert any(needle in e for e in exc.value.errors)


def test_model_routes_defaults_to_empty():
    # No [model_routes] table => every step is "general" (wq#3968).
    man = m.parse_manifest('[plugin]\nname="a"\nversion="1.0"\nkind="tool"\n')
    assert man.model_routes == {}


def test_model_routes_declares_step_capability():
    man = m.parse_manifest(
        '[plugin]\nname="llm4decompile"\nversion="0"\nkind="tool"\n'
        '[model_routes]\nrename_function = "re-naming"\ninfer_types = "re-types"\n')
    assert man.model_routes == {"rename_function": "re-naming", "infer_types": "re-types"}


@pytest.mark.parametrize("bad, needle", [
    ('[model_routes]\nrename_function = "re-name"\n', "re-naming"),      # typo'd capability
    ('[model_routes]\nrename_function = "fast"\n', "must be one of"),    # not a declared capability
])
def test_model_routes_invalid_rejected(bad, needle):
    with pytest.raises(m.ManifestError) as exc:
        m.parse_manifest('[plugin]\nname="a"\nversion="1.0"\nkind="tool"\n' + bad)
    assert any(needle in e for e in exc.value.errors)


def test_model_routes_non_table_rejected():
    # model_routes set at the top level (before [plugin]) so it is not
    # folded into the [plugin] table by TOML's own table-scoping rules.
    with pytest.raises(m.ManifestError) as exc:
        m.parse_manifest('model_routes = "oops"\n[plugin]\nname="a"\nversion="1.0"\nkind="tool"\n')
    assert any("table of step" in e for e in exc.value.errors)


def test_canonical_source_parsed_and_validated():
    # Anti-impersonation (wq#3998): the authoritative upstream is declared so a
    # name-squatting copy with the same README is detectable by the index.
    man = m.parse_manifest(
        '[plugin]\nname="rea"\nversion="1.0"\nkind="tool"\n'
        '[source]\ncanonical="https://github.com/morluto/rea"\n')
    assert man.canonical_source == "https://github.com/morluto/rea"
    # Present-but-empty is a declaration error, not a silent "" default.
    with pytest.raises(m.ManifestError) as exc:
        m.parse_manifest(
            '[plugin]\nname="a"\nversion="1.0"\nkind="tool"\n[source]\ncanonical=""\n')
    assert any("canonical" in e for e in exc.value.errors)


def test_canonical_source_absent_defaults_empty():
    man = m.parse_manifest('[plugin]\nname="a"\nversion="1.0"\nkind="tool"\n')
    assert man.canonical_source == ""


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
