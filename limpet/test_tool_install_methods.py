"""wq#4081: limpet/tool-install-methods.json reconciles the project catalog (104),
the foreman catalog (44) and the data/*.toml tool names, one row per tool."""
import json
import pathlib

import pytest

HERE = pathlib.Path(__file__).parent
METHODS = json.loads((HERE / "tool-install-methods.json").read_text())
CATALOG = json.loads((HERE / "tool-catalog.json").read_text())
FIXTURE = json.loads((HERE / "tests/fixtures/wq4081_project_tool_names.json").read_text())
ROWS = METHODS["tools"]
VOCAB = METHODS["install_method_vocabulary"]
NO_PACKAGE = {"vendor", "hardware", "limpet-native", "unresolved"}


def _index(field):
    idx = {}
    for row in ROWS:
        names = row[field] if field == "toml_names" else [row[field]] if row[field] else []
        for name in names:
            assert name not in idx, f"{field} {name!r} mapped twice"
            idx[name] = row
    return idx


def test_row_ids_unique():
    ids = [r["id"] for r in ROWS]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("field,names", [
    ("project_name", FIXTURE["project_tools"]),
    ("toml_names", FIXTURE["toml_tools"]),
    ("foreman_name", [t["name"] for t in CATALOG["tools"]]),
])
def test_every_source_name_maps_to_exactly_one_row(field, names):
    idx = _index(field)
    assert set(idx) == set(names)


def test_fixture_counts_match_the_audit():
    assert len(FIXTURE["project_tools"]) == 104
    assert len(CATALOG["tools"]) == 44


@pytest.mark.parametrize("row", ROWS, ids=lambda r: r["id"])
def test_method_in_vocabulary_and_package_rule(row):
    assert row["install_method"] in VOCAB
    assert (row["package"] is None) == (row["install_method"] in NO_PACKAGE)
    assert row["evidence"]


def test_foreman_catalog_carries_the_same_decision():
    rows = _index("foreman_name")
    for tool in CATALOG["tools"]:
        row = rows[tool["name"]]
        assert tool["install_method"] == row["install_method"], tool["name"]
        assert tool["package"] == row["package"], tool["name"]


# Name-only registry hits the limpet-iso classifier flagged NEEDS-EYEBALL that
# turned out to be unrelated projects; none may survive as the chosen package.
@pytest.mark.parametrize("project_name,wrong", [
    ("REA", "rea"), ("Kong", "kong"), ("Capa", "capa"), ("Keystone", "keystone"),
    ("Gepetto", "gepetto"), ("FACT", "fact"), ("EMUX", "emux"), ("edl (bkerler)", "edl"),
    ("Thor (Samsung-Loki)", "thor"), ("uConfig", "uconfig"), ("Pretender", "pretender"),
    ("Diaphora", "diaphora-python"), ("Ghidra MCP (bethington)", "ghidramcp"),
])
def test_eyeballed_name_collisions_were_not_adopted(project_name, wrong):
    row = _index("project_name")[project_name]
    assert row["limpet_iso_class"] == "NEEDS-EYEBALL"
    assert row["package"] != wrong
