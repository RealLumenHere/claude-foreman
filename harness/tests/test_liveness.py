"""Liveness Gate Slices 1-2 (research/LIVENESS_GATE.md §9): test-to-failure rows.

(ii)  a `Liveness:` line parses via the SAME parser as `Needs:` (needs.parse_tags,
      needs._TAG_RE — identity, not a look-alike).
(iii) the exec allow/deny path is untouched: preflight() and policy_grants() source
      is byte-identical to the pre-liveness hash and neither mentions liveness.
(iv)  UNKNOWN leaves the ticket alone: a raising probe, an unknown probe, and a
      non-verdict return all aggregate to UNKNOWN, never SATISFIED; the sweep module
      has no write path (no wq subprocess, no sqlite write mode).
(v)   firewall: liveness tags never enter Needs.tags, validate() rejects them in the
      needs column, and policy_grants over a Liveness-only ticket grants nothing.
"""
import ast
import hashlib
import inspect
import os
import sys

import pytest

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HARNESS_DIR)
import capability  # noqa: E402
import liveness  # noqa: E402
import needs as needs_mod  # noqa: E402

# sha256 of the exact source segment of each function BEFORE liveness landed
# (captured 2026-09-07 from capability.py 7e2036f1..., see LIVENESS_GATE.md §9 iii).
PRE_LIVENESS_FN_SHA = {"policy_grants": "7edf6f40ee0d5c82", "preflight": "48bda835f17157e9"}

DETAIL = ("Do the thing.\n"
          "Needs: ssh(isis), dir(/home/lumen/foreman)\n"
          "Liveness: repo_is(private, frankstupak/lumen-content), path_exists(/tmp)\n")


# ---- (ii) same parser -----------------------------------------------------------------

def test_liveness_line_parses_with_needs_parser():
    lv = needs_mod.parse_liveness(DETAIL)
    assert lv.tags == [("repo_is", "private, frankstupak/lumen-content"), ("path_exists", "/tmp")]
    # identity: parse_liveness is built on parse_tags / _TAG_RE, not a copy
    src = inspect.getsource(needs_mod.parse_liveness)
    assert "parse_tags(" in src
    assert needs_mod._LIVENESS_LINE_RE.pattern.replace("Liveness", "Needs") == needs_mod._NEEDS_LINE_RE.pattern


def test_liveness_field_and_rollback(monkeypatch):
    assert needs_mod.parse_liveness(liveness_field="ts_key_expiry_disabled()").tags == [("ts_key_expiry_disabled", "")]
    monkeypatch.setenv("FOREMAN_LIVENESS_DISABLE", "1")
    assert not needs_mod.parse_liveness(DETAIL)
    assert liveness.run("path_exists", "/tmp")[0] == liveness.UNKNOWN


# ---- (iii) exec allow/deny untouched ---------------------------------------------------

def _fn_sha(name):
    path = os.path.join(HARNESS_DIR, "capability.py")
    src = open(path).read()
    tree = ast.parse(src)
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    return hashlib.sha256(ast.get_source_segment(src, node).encode()).hexdigest()[:16]


@pytest.mark.parametrize("fn", sorted(PRE_LIVENESS_FN_SHA))
def test_exec_grant_path_byte_identical(fn):
    assert _fn_sha(fn) == PRE_LIVENESS_FN_SHA[fn]
    assert "liveness" not in inspect.getsource(getattr(capability, fn)).lower()


def test_fworker_does_not_import_liveness():
    src = open(os.path.join(HARNESS_DIR, "fworker.py")).read()
    assert "liveness_verdict" not in src and "import liveness" not in src


# ---- (iv) UNKNOWN never clears ---------------------------------------------------------

def test_unknown_probe_is_unknown():
    assert liveness.run("no_such_probe", "")[0] == liveness.UNKNOWN


def test_raising_probe_is_unknown(monkeypatch):
    def boom(*a):
        raise RuntimeError("network on fire")
    monkeypatch.setitem(liveness.PROBES, "boom", boom)
    assert liveness.run("boom", "x")[0] == liveness.UNKNOWN


def test_bad_arity_and_non_verdict_are_unknown(monkeypatch):
    monkeypatch.setitem(liveness.PROBES, "weird", lambda: ("YES", "nope"))
    assert liveness.run("weird", "")[0] == liveness.UNKNOWN
    assert liveness.run("path_exists", "/a, /b, /c")[0] == liveness.UNKNOWN  # arity


def test_aggregate_fail_safe():
    S, B, U = liveness.SATISFIED, liveness.STILL_BLOCKED, liveness.UNKNOWN
    assert liveness.aggregate([]) == U
    assert liveness.aggregate([(S, ""), (U, "")]) == U
    assert liveness.aggregate([(S, ""), (B, ""), (U, "")]) == B
    assert liveness.aggregate([(S, ""), (S, "")]) == S


def test_liveness_verdict_unknown_and_no_tag(monkeypatch):
    monkeypatch.setitem(liveness.PROBES, "boom", lambda *a: (_ for _ in ()).throw(RuntimeError()))
    v, res = capability.liveness_verdict({"id": 1, "detail": "Liveness: boom(x)"})
    assert v == liveness.UNKNOWN and res[0][2] == liveness.UNKNOWN
    assert capability.liveness_verdict({"id": 2, "detail": "nothing"}) == (capability.LIVENESS_NO_TAG, [])
    # ledger binding is used only when the ticket has no line of its own
    v, res = capability.liveness_verdict({"id": 3, "detail": "x"}, {"3": "path_exists(/tmp)"})
    assert v == liveness.SATISFIED
    v, _ = capability.liveness_verdict({"id": 3, "detail": "Liveness: path_exists(/definitely/missing)"},
                                       {"3": "path_exists(/tmp)"})
    assert v == liveness.STILL_BLOCKED


def test_sweep_has_no_write_path():
    """Code tokens only (docstrings/comments stripped) — the first run of this test
    failed on the docstring's own 'Zero writes', which is the test doing its job."""
    import io
    import tokenize
    src = open(os.path.join(HARNESS_DIR, "liveness_sweep.py")).read()
    names = [t.string for t in tokenize.generate_tokens(io.StringIO(src).readline)
             if t.type == tokenize.NAME]
    assert 'file:{db}?mode=ro' in src and "sqlite3" in names and "connect" in names
    # no wq mutation, no shell, no file/db write verbs anywhere in code tokens
    for forbidden in ("apply_tags", "_wq", "subprocess", "dump", "write", "writelines",
                      "commit", "executescript", "system", "popen", "remove", "rename"):
        assert forbidden not in names, forbidden
    # the only open() is the read of the ledger
    assert names.count("open") == 1 and "safe_load" in names


# ---- (v) firewall ----------------------------------------------------------------------

def test_liveness_tags_never_enter_needs():
    n = needs_mod.parse(None, DETAIL)
    assert n.tags == [("ssh", "isis"), ("dir", "/home/lumen/foreman")]
    assert not any(name in liveness.PROBE_NAMES for name, _ in n.tags)


def test_needs_column_rejects_liveness_vocabulary():
    assert needs_mod.validate("liveness(repo_is(private, x))")[0] is False
    for probe in liveness.PROBE_NAMES:
        assert needs_mod.validate(f"{probe}(x)")[0] is False
        assert probe not in needs_mod.TAG_NAMES


def test_policy_grants_ignores_liveness(tmp_path):
    p = tmp_path / "policy.yaml"
    p.write_text("never:\n  nodes: [thor]\n  power: []\n  restart: {}\n  sudo_argv_deny: []\n"
                 "run_readonly_cmds: [true]\ncopy_dirs: {}\nbench_tools: {}\n")
    only_liveness = needs_mod.parse(None, "Liveness: node_healthy(thor), service_active(sshd@thor)")
    assert capability.policy_grants(only_liveness, policy_path=str(p)) == ([], [])
    assert capability.needs_nodes(only_liveness) == []
