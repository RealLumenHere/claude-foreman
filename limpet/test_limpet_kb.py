"""Tests for the limpet-kb front end (kb.py): catalog parse, FTS5 loader, search,
status, and pull across the SHIP/FETCH/LINK tiers.

kb.py is loaded by absolute path via importlib so the probe runs from any cwd and
dodges the registry.py/registry-package name collision that breaks a bare `import`
of the cli-importing modules in this dir. The shipped-catalog test is pinned to the
committed kb-sources.d drop-ins (immutable committed fixtures), and the FTS5 tests
build their own tmp kb-root + sources-dir -- a boundary outside the /usr,/var defaults.
"""
import importlib.util
import json
import os
import sys

import pytest

_HERE = os.path.dirname(__file__)
_spec = importlib.util.spec_from_file_location("limpet_kb", os.path.join(_HERE, "kb.py"))
kb = importlib.util.module_from_spec(_spec)
sys.modules["limpet_kb"] = kb  # dataclass decorator resolves cls.__module__ via sys.modules
_spec.loader.exec_module(kb)

SHIPPED_SOURCES_DIR = os.path.join(_HERE, "kb-sources.d")


# --------------------------------------------------------------------------- catalog

def test_shipped_catalog_parses_and_is_valid():
    sources = kb.load_sources([SHIPPED_SOURCES_DIR])
    assert len(sources) >= 13  # the design §8 TL;DR table
    assert {"exploitdb", "defaultcreds", "cwe", "getsploit", "fccid"} <= set(sources)
    for s in sources.values():
        assert s.tier in kb.TIERS
        assert s.category in kb.CATEGORIES
    tiers = {s.tier for s in sources.values()}
    assert tiers == {"ship", "fetch", "link"}  # all three tiers present
    assert sources["exploitdb"].indexable
    assert not sources["fccid"].indexable  # LINK is never indexed


def test_parse_rejects_bad_tier():
    with pytest.raises(kb.KBError, match="tier must be"):
        kb.parse_source('[source]\nname="x"\ntier="SHIP"\ncategory="creds"\n')


def test_parse_rejects_bad_category():
    with pytest.raises(kb.KBError, match="category must be"):
        kb.parse_source('[source]\nname="x"\ntier="ship"\ncategory="nope"\n')


def test_parse_ship_requires_ship_table():
    with pytest.raises(kb.KBError, match="needs a .ship."):
        kb.parse_source('[source]\nname="x"\ntier="ship"\ncategory="creds"\n')


def test_parse_fetch_requires_command():
    with pytest.raises(kb.KBError, match="command is required"):
        kb.parse_source('[source]\nname="x"\ntier="fetch"\ncategory="exploits"\n'
                        '[fetch]\nrequires="a key"\n')


def test_duplicate_name_in_one_dir_raises(tmp_path):
    (tmp_path / "a.toml").write_text(
        '[source]\nname="dup"\ntier="link"\ncategory="devices"\n[link]\nurl="u"\n')
    (tmp_path / "b.toml").write_text(
        '[source]\nname="dup"\ntier="link"\ncategory="devices"\n[link]\nurl="u2"\n')
    with pytest.raises(kb.KBError, match="duplicate source name"):
        kb.load_sources([str(tmp_path)])


def test_etc_override_shadows_shipped(tmp_path):
    shipped = tmp_path / "ship"
    site = tmp_path / "site"
    shipped.mkdir()
    site.mkdir()
    (shipped / "x.toml").write_text(
        '[source]\nname="x"\ntier="link"\ncategory="devices"\n[link]\nurl="shipped"\n')
    (site / "x.toml").write_text(
        '[source]\nname="x"\ntier="link"\ncategory="devices"\n[link]\nurl="site"\n')
    sources = kb.load_sources([str(shipped), str(site)])  # later dir wins
    assert sources["x"].link_url == "site"


# --------------------------------------------------------------------------- loader fixtures

def _mk_kb(tmp_path):
    """A tmp KB root + sources dir covering csv/json/jsonl/markdown/lines + fetch + link."""
    root = tmp_path / "kb"
    srcs = tmp_path / "sources"
    for sub in ("creds", "devices", "hw", "cwe", "exploits"):
        (root / sub).mkdir(parents=True)
    srcs.mkdir()
    (root / "creds" / "creds.csv").write_text(
        "productvendor,username,password\nLinksys WRT54G,admin,admin\nCisco IOS,cisco,cisco\n")
    (srcs / "creds.toml").write_text(
        '[source]\nname="creds"\ntier="ship"\ncategory="creds"\n'
        '[ship]\nformat="csv"\npath="creds/creds.csv"\n'
        'title_fields=["productvendor"]\nbody_fields=["productvendor","username","password"]\n')
    (root / "devices" / "toh.json").write_text(json.dumps(
        [{"brand": "TP-Link", "model": "Archer C7", "cpu": "QCA9558"}]))
    (srcs / "toh.toml").write_text(
        '[source]\nname="toh"\ntier="ship"\ncategory="devices"\n'
        '[ship]\nformat="json"\npath="devices/toh.json"\n'
        'title_fields=["brand","model"]\nbody_fields=["brand","model","cpu"]\n')
    (root / "cwe" / "cwe.jsonl").write_text(
        '{"id":"CWE-78","name":"OS Command Injection","description":"improper neutralization"}\n')
    (srcs / "cwe.toml").write_text(
        '[source]\nname="cwe"\ntier="ship"\ncategory="cwe"\n'
        '[ship]\nformat="jsonl"\npath="cwe/cwe.jsonl"\n'
        'title_fields=["id","name"]\nbody_fields=["id","name","description"]\n')
    (root / "hw" / "uart.md").write_text("# UART pinout\nbaud 115200 TX RX GND Vcc\n")
    (srcs / "hw.toml").write_text(
        '[source]\nname="hw"\ntier="ship"\ncategory="hw"\n'
        '[ship]\nformat="markdown"\npath="hw/*.md"\n')
    (srcs / "fetchit.toml").write_text(
        '[source]\nname="fetchit"\ntier="fetch"\ncategory="exploits"\n'
        '[fetch]\ncommand="echo pulled"\nrequires="a key"\ndest="exploits/f.db"\n')
    (srcs / "linkit.toml").write_text(
        '[source]\nname="linkit"\ntier="link"\ncategory="devices"\n'
        '[link]\nurl="https://x/{query}"\nhelper="limpet x"\n')
    return str(root), str(srcs)


def test_build_index_counts_rows(tmp_path):
    root, srcs = _mk_kb(tmp_path)
    db = str(tmp_path / "kb.db")
    sources = kb.load_sources([srcs])
    stats = kb.build_index(sources, root, db)
    assert stats["creds"]["rows"] == 2
    assert stats["toh"]["rows"] == 1
    assert stats["cwe"]["rows"] == 1
    assert stats["hw"]["rows"] == 1
    assert "fetchit" not in stats and "linkit" not in stats  # non-SHIP never indexed


def test_search_finds_across_sources(tmp_path):
    root, srcs = _mk_kb(tmp_path)
    db = str(tmp_path / "kb.db")
    kb.build_index(kb.load_sources([srcs]), root, db)
    hits = kb.search(db, "admin")
    assert any(h["source"] == "creds" and "Linksys" in h["title"] for h in hits)
    cwe_hits = kb.search(db, "command injection")
    assert any(h["source"] == "cwe" for h in cwe_hits)
    only_hw = kb.search(db, "baud", category="hw")
    assert only_hw and all(h["category"] == "hw" for h in only_hw)
    assert kb.search(db, "admin", category="devices") == []  # category filter excludes


def test_search_query_with_fts_punctuation_is_safe(tmp_path):
    root, srcs = _mk_kb(tmp_path)
    db = str(tmp_path / "kb.db")
    kb.build_index(kb.load_sources([srcs]), root, db)
    # Bare FTS5 operators/quotes would be a syntax error if not escaped; must not raise.
    assert isinstance(kb.search(db, 'admin OR "*'), list)  # punctuation neutralized, no raise
    assert any(h["source"] == "creds" for h in kb.search(db, "admin)"))  # trailing paren safe


def test_update_is_idempotent(tmp_path):
    root, srcs = _mk_kb(tmp_path)
    db = str(tmp_path / "kb.db")
    sources = kb.load_sources([srcs])
    kb.build_index(sources, root, db)
    kb.build_index(sources, root, db)  # second pass must not double-count
    hits = kb.search(db, "admin", limit=50)
    assert len([h for h in hits if h["source"] == "creds"]) == 1


def test_search_without_index_raises(tmp_path):
    with pytest.raises(kb.KBError, match="no index"):
        kb.search(str(tmp_path / "missing.db"), "anything")


# --------------------------------------------------------------------------- status + pull

def test_status_reflects_presence_and_tiers(tmp_path):
    root, srcs = _mk_kb(tmp_path)
    db = str(tmp_path / "kb.db")
    sources = kb.load_sources([srcs])
    meta = kb._index_meta(db)  # empty before any update
    creds_before = kb.source_status(sources["creds"], root, meta)
    assert creds_before["present"] and not creds_before["indexed"]
    kb.build_index(sources, root, db)
    meta = kb._index_meta(db)
    creds_after = kb.source_status(sources["creds"], root, meta)
    assert creds_after["indexed"] and creds_after["rows"] == 2
    assert kb.source_status(sources["fetchit"], root, meta)["fetched"] is False
    link = kb.source_status(sources["linkit"], root, meta)
    assert link["tier"] == "link" and link["url"].endswith("{query}")


def test_pull_dry_run_plans_without_running(tmp_path):
    root, srcs = _mk_kb(tmp_path)
    plan = kb.pull(kb.load_sources([srcs])["fetchit"], root, run=False)
    assert plan["ran"] is False and plan["command"] == "echo pulled"
    assert "dry run" in plan["note"]


def test_pull_rejects_non_fetch_source(tmp_path):
    root, srcs = _mk_kb(tmp_path)
    with pytest.raises(kb.KBError, match="not FETCH"):
        kb.pull(kb.load_sources([srcs])["creds"], root, run=False)


def test_pull_run_executes_and_stamps_marker(tmp_path):
    root, srcs = _mk_kb(tmp_path)
    plan = kb.pull(kb.load_sources([srcs])["fetchit"], root, run=True)
    assert plan["ran"] and plan["exit_code"] == 0 and "fetched_at" in plan
    marker = kb._fetch_marker(kb.load_sources([srcs])["fetchit"], root)
    assert os.path.exists(marker)


# --------------------------------------------------------------------------- CLI

def test_cli_list_json(tmp_path, capsys):
    _, srcs = _mk_kb(tmp_path)
    rc = kb.main(["--json", "--sources-dir", srcs, "list"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert {r["name"] for r in out} >= {"creds", "fetchit", "linkit"}


def test_cli_list_tier_filter(tmp_path, capsys):
    _, srcs = _mk_kb(tmp_path)
    rc = kb.main(["--json", "--sources-dir", srcs, "list", "--tier", "link"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert [r["name"] for r in out] == ["linkit"]


def test_cli_update_then_search(tmp_path, capsys):
    root, srcs = _mk_kb(tmp_path)
    db = str(tmp_path / "kb.db")
    base = ["--json", "--kb-root", root, "--db", db, "--sources-dir", srcs]
    assert kb.main(base + ["update"]) == 0
    capsys.readouterr()
    assert kb.main(base + ["search", "cisco"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert any(h["source"] == "creds" for h in out)


def test_cli_update_unknown_source_exit_1(tmp_path, capsys):
    root, srcs = _mk_kb(tmp_path)
    rc = kb.main(["--kb-root", root, "--db", str(tmp_path / "k.db"),
                  "--sources-dir", srcs, "update", "nope"])
    assert rc == 1


def test_cli_pull_unknown_exit_1(tmp_path):
    _, srcs = _mk_kb(tmp_path)
    assert kb.main(["--sources-dir", srcs, "pull", "ghost"]) == 1
