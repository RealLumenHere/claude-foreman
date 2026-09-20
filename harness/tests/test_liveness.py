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
# preflight re-pinned wq#2184 (advisory_paths param); liveness still does not touch it.
PRE_LIVENESS_FN_SHA = {"policy_grants": "7edf6f40ee0d5c82", "preflight": "cdddd9f2725e8f49"}

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


# ---- wq#1492 backlog probes (LIVENESS_GATE.md §13.1): deterministic, UNKNOWN on any
# transport/parse error, never raise (every case goes through liveness.run) ------------

def test_ticket_done_probe(tmp_path, monkeypatch):
    import sqlite3
    db = tmp_path / "q.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE tasks (id INTEGER PRIMARY KEY, status TEXT)")
    con.executemany("INSERT INTO tasks VALUES (?, ?)", [(1, "done"), (2, "blocked"), (3, "open")])
    con.commit(); con.close()
    monkeypatch.setattr(liveness, "WQ_DB", str(db))
    assert liveness.run("ticket_done", "1")[0] == liveness.SATISFIED
    assert liveness.run("ticket_done", "wq#1")[0] == liveness.SATISFIED
    assert liveness.run("ticket_done", "2")[0] == liveness.STILL_BLOCKED
    assert liveness.run("ticket_done", "3")[0] == liveness.STILL_BLOCKED
    assert liveness.run("ticket_done", "999")[0] == liveness.UNKNOWN      # no such row
    assert liveness.run("ticket_done", "abc")[0] == liveness.UNKNOWN      # bad id
    monkeypatch.setattr(liveness, "WQ_DB", str(tmp_path / "missing.db"))
    v, d = liveness.run("ticket_done", "1")                              # db unreadable
    assert v == liveness.UNKNOWN and "queue.db" in d


def test_pr_merged_probe(monkeypatch, tmp_path):
    tok = tmp_path / "t"; tok.write_text("secret-token")
    monkeypatch.setattr(liveness, "GITEA_TOKEN", str(tok))
    monkeypatch.setattr(liveness, "GH_TOKEN", str(tok))
    seen = {}
    def fake_http(url, headers=None, data=None, method=None):
        seen["url"], seen["headers"] = url, headers or {}
        return fake_http.reply
    monkeypatch.setattr(liveness, "_http", fake_http)
    fake_http.reply = (200, '{"state": "closed", "merged": true}')
    v, d = liveness.run("pr_merged", "lumen/mailbot, 3")
    assert v == liveness.SATISFIED and "merged" in d and "secret-token" not in d
    assert seen["url"].endswith("/api/v1/repos/lumen/mailbot/pulls/3")
    assert seen["headers"]["Authorization"] == "token secret-token"
    fake_http.reply = (200, '{"state": "open", "merged": false}')
    assert liveness.run("pr_merged", "lumen/mailbot, 3")[0] == liveness.STILL_BLOCKED
    assert liveness.run("pr_merged", "lumen/mailbot, 3, closed")[0] == liveness.STILL_BLOCKED
    fake_http.reply = (200, '{"state": "closed", "merged": false}')
    assert liveness.run("pr_merged", "lumen/mailbot, 3")[0] == liveness.STILL_BLOCKED   # closed != merged
    assert liveness.run("pr_merged", "lumen/mailbot, 3, closed")[0] == liveness.SATISFIED
    fake_http.reply = (404, "")
    assert liveness.run("pr_merged", "lumen/mailbot, 3")[0] == liveness.UNKNOWN         # invisible, not a verdict
    fake_http.reply = (0, "timed out")
    assert liveness.run("pr_merged", "lumen/mailbot, 3")[0] == liveness.UNKNOWN         # transport error
    fake_http.reply = (200, "not json")
    assert liveness.run("pr_merged", "lumen/mailbot, 3")[0] == liveness.UNKNOWN
    fake_http.reply = (200, '{"state": "closed", "merged": true}')
    v, _ = liveness.run("pr_merged", "frankstupak/x, 9, merged, github")
    assert v == liveness.SATISFIED and seen["url"].startswith("https://api.github.com/repos/frankstupak/x/pulls/9")
    assert seen["headers"]["Authorization"] == "Bearer secret-token"
    assert liveness.run("pr_merged", "a/b, 1, deleted")[0] == liveness.UNKNOWN
    assert liveness.run("pr_merged", "a/b, 1, merged, bitbucket")[0] == liveness.UNKNOWN


def test_cred_alive_names_the_account_endpoint_on_a_user_verify_401(monkeypatch, tmp_path):
    """wq#2109: a 401 from Cloudflare's user-token endpoint is as likely to mean
    'account-owned token, wrong endpoint' as 'dead token' — wq#2105 read it as death and
    paged Frank for a re-mint of a live token. The detail has to name the alternative."""
    tok = tmp_path / "cf.token"; tok.write_text("account-owned-secret")
    monkeypatch.setattr(liveness, "_http", lambda *a, **k: (401, '{"code": 1000}'))
    v, d = liveness.cred_alive(str(tok), liveness.CF_USER_VERIFY)
    assert v == liveness.STILL_BLOCKED
    assert "/accounts/<account_id>/tokens/verify" in d and "account-owned-secret" not in d

    acct = "https://api.cloudflare.com/client/v4/accounts/abc/tokens/verify"
    assert "accounts/<account_id>" not in liveness.cred_alive(str(tok), acct)[1]
    monkeypatch.setattr(liveness, "_http", lambda *a, **k: (200, "{}"))
    assert liveness.cred_alive(str(tok), acct)[0] == liveness.SATISFIED


def test_file_contains_and_json_gte_probes(tmp_path):
    f = tmp_path / "settings.json"; f.write_text('{"allow": ["Read", "Skill"], "cloud_ceiling": 10, "n": {"m": 2}}')
    v, d = liveness.run("file_contains", f"{f}, Skill")
    assert v == liveness.SATISFIED and "Skill" in d and "Read" not in d          # detail echoes the needle only
    assert liveness.run("file_contains", f"{f}, Bash")[0] == liveness.STILL_BLOCKED
    assert liveness.run("file_contains", f"{tmp_path}/nope, Skill")[0] == liveness.UNKNOWN
    assert liveness.run("file_contains", f"{f}, ")[0] == liveness.UNKNOWN            # empty needle -> bad arity/UNKNOWN
    assert liveness.run("json_gte", f"{f}, cloud_ceiling, 5")[0] == liveness.SATISFIED
    assert liveness.run("json_gte", f"{f}, cloud_ceiling, 11")[0] == liveness.STILL_BLOCKED
    assert liveness.run("json_gte", f"{f}, n.m, 2")[0] == liveness.SATISFIED
    assert liveness.run("json_gte", f"{f}, missing, 1")[0] == liveness.UNKNOWN
    assert liveness.run("json_gte", f"{f}, allow, 1")[0] == liveness.UNKNOWN          # not a number
    assert liveness.run("json_gte", f"{f}, cloud_ceiling, x")[0] == liveness.UNKNOWN
    assert liveness.run("json_gte", f"{tmp_path}/nope.json, k, 1")[0] == liveness.UNKNOWN
    bad = tmp_path / "bad.json"; bad.write_text("{")
    assert liveness.run("json_gte", f"{bad}, k, 1")[0] == liveness.UNKNOWN


def test_backlog_probes_registered_and_aggregate_fail_safe():
    for name in ("ticket_done", "pr_merged", "file_contains", "json_gte"):
        assert name in liveness.PROBES
    # a SATISFIED ticket_done next to an UNKNOWN pr_merged must never aggregate SATISFIED
    assert liveness.aggregate([(liveness.SATISFIED, ""), (liveness.UNKNOWN, "")]) == liveness.UNKNOWN


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


# ---- wq#1676 items 3: git_clean, wq_field -------------------------------------------

def test_git_clean_probe(tmp_path, monkeypatch):
    import subprocess
    repo = tmp_path / "repo"; repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    assert liveness.run("git_clean", str(repo))[0] == liveness.SATISFIED
    (repo / "f").write_text("x")
    v, d = liveness.run("git_clean", str(repo))
    assert v == liveness.STILL_BLOCKED and "dirty" in d
    assert liveness.run("git_clean", str(tmp_path / "nope"))[0] == liveness.UNKNOWN   # not a directory
    monkeypatch.setattr(liveness, "_sh", lambda argv, timeout=1, cwd=None: (99, "", "boom"))
    assert liveness.run("git_clean", str(repo))[0] == liveness.UNKNOWN                # git/subprocess error


def test_wq_field_probe(tmp_path, monkeypatch):
    import sqlite3
    db = tmp_path / "q.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE tasks (id INTEGER PRIMARY KEY, status TEXT, review_verdict TEXT)")
    con.executemany("INSERT INTO tasks VALUES (?, ?, ?)", [(1, "done", "accept"), (2, "open", None)])
    con.commit(); con.close()
    monkeypatch.setattr(liveness, "WQ_DB", str(db))
    assert liveness.run("wq_field", "1, status, done")[0] == liveness.SATISFIED
    assert liveness.run("wq_field", "1, status, blocked")[0] == liveness.STILL_BLOCKED
    assert liveness.run("wq_field", "2, review_verdict, accept")[0] == liveness.STILL_BLOCKED  # NULL != 'accept'
    assert liveness.run("wq_field", "999, status, done")[0] == liveness.UNKNOWN        # no such row
    assert liveness.run("wq_field", "abc, status, done")[0] == liveness.UNKNOWN        # bad id
    v, d = liveness.run("wq_field", "1, drop table tasks;--, done")                    # untrusted column, whitelisted
    assert v == liveness.UNKNOWN and "no column" in d
    monkeypatch.setattr(liveness, "WQ_DB", str(tmp_path / "missing.db"))
    assert liveness.run("wq_field", "1, status, done")[0] == liveness.UNKNOWN          # db unreadable
