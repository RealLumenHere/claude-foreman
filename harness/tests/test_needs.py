"""needs.py — wq#1331 unit coverage (WORKER_EXEC_CAPABILITY_GAP.md §2.3/§3.2).

The parser feeding router.needs_repo_hint() and fworker.add_dirs_for(): the needs
column, the detail's `Needs:` line, the FOREMAN_NEEDS_DISABLE rollback, and the
closed-vocabulary validation `wq edit --needs` calls before storing.
"""
import os
import sys

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HARNESS_DIR)
import needs  # noqa: E402


def test_parse_column_tags():
    n = needs.parse("repo(/home/lumen/questfree), dir(/home/lumen/nle-flasher)")
    assert n.repo == "/home/lumen/questfree"
    assert n.dirs == ["/home/lumen/nle-flasher"]
    assert bool(n)


def test_parse_detail_needs_line_unions_with_column():
    n = needs.parse("dir(/a)", "Fix the frob.\nNeeds: ssh(isis), dir(/b)\nMore prose.")
    assert n.dirs == ["/a", "/b"]
    assert ("ssh", "isis") in n.tags
    assert n.repo is None


def test_parse_prose_tags_outside_needs_line_are_ignored():
    # only an explicit Needs: line in detail carries tags — prose mentioning
    # "restart(ollama@isis)" mid-sentence must not grant anything.
    n = needs.parse("", "the fix is to restart(ollama@isis) manually")
    assert not n


def test_parse_accepts_needs_prefixed_tags():
    n = needs.parse("needs:repo(/home/lumen/pokemon)")
    assert n.repo == "/home/lumen/pokemon"


def test_parse_empty_and_none():
    assert not needs.parse(None, None)
    assert not needs.parse("", "")
    assert needs.parse("", "").repo is None
    assert needs.parse("", "").dirs == []


def test_disable_env_is_the_rollback(monkeypatch):
    monkeypatch.setenv("FOREMAN_NEEDS_DISABLE", "1")
    assert needs.disabled()
    n = needs.parse("repo(/home/lumen/questfree)", "Needs: dir(/b)")
    assert not n and n.repo is None and n.dirs == []
    monkeypatch.delenv("FOREMAN_NEEDS_DISABLE")
    assert needs.parse("repo(/home/lumen/questfree)").repo == "/home/lumen/questfree"


def test_validate_accepts_the_closed_vocabulary():
    ok, err = needs.validate(
        "ssh(isis), root(isis), restart(ollama@isis), copy(isis:/opt/x), "
        "repo(/home/lumen/questfree), dir(/home/lumen/kb), bench(thor,adb), power(maat)")
    assert ok, err
    assert needs.validate("")[0] and needs.validate(None)[0]


def test_validate_rejects_unknown_tag_and_bad_shapes():
    assert not needs.validate("frobnicate(isis)")[0]
    assert not needs.validate("repo(relative/path)")[0]
    assert not needs.validate("dir()")[0]
    assert not needs.validate("just some prose")[0]


# wq#1332 (§2.4): fleet-exec grant resolution reads these three accessors.

def test_ssh_nodes():
    n = needs.parse("ssh(isis), ssh(thor)")
    assert n.ssh_nodes == ["isis", "thor"]


def test_copy_targets_parses_node_colon_dir():
    n = needs.parse("copy(isis:/home/isis/lumen-drop)")
    assert n.copy_targets == [("isis", "/home/isis/lumen-drop")]


def test_copy_targets_skips_malformed_arg():
    n = needs.parse("copy(no-colon-here)")
    assert n.copy_targets == []


def test_restart_targets_parses_unit_at_node():
    n = needs.parse("restart(ollama@isis)")
    assert n.restart_targets == [("isis", "ollama")]


def test_restart_targets_skips_malformed_arg():
    n = needs.parse("restart(no-at-sign)")
    assert n.restart_targets == []


def test_peer_targets_parses_name_at_node():
    n = needs.parse("peer(bgblink-peer@thor)")
    assert n.peer_targets == [("thor", "bgblink-peer")]
    assert needs.validate("peer(bgblink-peer@thor)")[0]


def test_peer_targets_skips_malformed_arg():
    n = needs.parse("peer(no-at-sign)")
    assert n.peer_targets == []
