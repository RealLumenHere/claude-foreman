"""harness/model_alias_sweep.py — wq#1442 one-off sweep. Zero-quota: a real
`wq add`/`wq edit` subprocess against a scratch WQ_DB, no claude involved.
resolve() is exercised directly against synthetic ids/aliases (decoupled from
needing a full, routing-validated research/models.yaml fixture); sweep()'s
write path is exercised against the real wq CLI so it re-validates through the
same wq#1442 norm_tuple() a human `wq edit` invocation would.
"""
import os
import sqlite3
import subprocess
import sys

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WQ_BIN = "/home/lumen/bin/wq"

sys.path.insert(0, HARNESS_DIR)
import model_alias_sweep as sweep_mod  # noqa: E402

IDS = {"claude-opus-5", "claude-sonnet-5", "deepseek-v4-flash"}
ALIASES = {"opus-5": "claude-opus-5", "opus": "claude-opus-5", "sonnet": "claude-sonnet-5"}


def make_env(tmp_path):
    env = dict(os.environ)
    env["WQ_DB"] = str(tmp_path / "scratch_wq.db")
    return env


def wq(env, *args):
    return subprocess.run([WQ_BIN] + list(args), capture_output=True, text=True, env=env)


def wq_add(env, title):
    p = wq(env, "add", "--title", title)
    assert p.returncode == 0, p.stdout + p.stderr
    return int(p.stdout.strip().split()[2])


def set_model(db_path, ticket_id, model, status="open"):
    # Direct write bypassing wq edit's own validation — simulates a row stored
    # before wq#1442 (or by any other pre-fix path) that the sweep must clean up.
    c = sqlite3.connect(db_path)
    c.execute("UPDATE tasks SET model=?, status=? WHERE id=?", (model, status, ticket_id))
    c.commit()
    c.close()


def read_model(db_path, ticket_id):
    c = sqlite3.connect(db_path)
    row = c.execute("SELECT model FROM tasks WHERE id=?", (ticket_id,)).fetchone()
    c.close()
    return row[0]


# ---- resolve() — pure classification, no I/O ---------------------------------------

def test_resolve_keeps_a_full_id():
    assert sweep_mod.resolve("claude-opus-5", IDS, ALIASES) == ("keep", "claude-opus-5")


def test_resolve_canonicalizes_a_bare_alias():
    assert sweep_mod.resolve("opus-5", IDS, ALIASES) == ("canonicalize", "claude-opus-5")


def test_resolve_clears_an_unknown_string():
    assert sweep_mod.resolve("totally-made-up-model", IDS, ALIASES) == ("clear", None)


# ---- sweep() — real wq add/edit against a scratch DB -------------------------------

def test_canonicalizes_alias_via_wq_edit(tmp_path, monkeypatch):
    env = make_env(tmp_path)
    monkeypatch.setenv("WQ_DB", env["WQ_DB"])
    tid = wq_add(env, "wq1442 sweep: canonicalize case")
    set_model(env["WQ_DB"], tid, "opus-5")

    results = sweep_mod.sweep(db_path=env["WQ_DB"], wq_bin=WQ_BIN, ids=IDS, aliases=ALIASES)

    entry = next(r for r in results if r["id"] == tid)
    assert entry == {"id": tid, "model": "opus-5", "action": "canonicalize",
                      "new_model": "claude-opus-5", "applied": True}
    assert read_model(env["WQ_DB"], tid) == "claude-opus-5"


def test_clears_unknown_model_via_wq_edit(tmp_path, monkeypatch):
    env = make_env(tmp_path)
    monkeypatch.setenv("WQ_DB", env["WQ_DB"])
    tid = wq_add(env, "wq1442 sweep: clear case")
    set_model(env["WQ_DB"], tid, "totally-made-up-model")

    results = sweep_mod.sweep(db_path=env["WQ_DB"], wq_bin=WQ_BIN, ids=IDS, aliases=ALIASES)

    entry = next(r for r in results if r["id"] == tid)
    assert entry["action"] == "clear"
    assert entry["applied"] is True
    assert read_model(env["WQ_DB"], tid) is None


def test_leaves_canonical_model_untouched(tmp_path, monkeypatch):
    env = make_env(tmp_path)
    monkeypatch.setenv("WQ_DB", env["WQ_DB"])
    tid = wq_add(env, "wq1442 sweep: keep case")
    set_model(env["WQ_DB"], tid, "claude-opus-5")

    results = sweep_mod.sweep(db_path=env["WQ_DB"], wq_bin=WQ_BIN, ids=IDS, aliases=ALIASES)

    entry = next(r for r in results if r["id"] == tid)
    assert entry == {"id": tid, "model": "claude-opus-5", "action": "keep",
                      "new_model": "claude-opus-5"}
    assert read_model(env["WQ_DB"], tid) == "claude-opus-5"


def test_dry_run_reports_without_writing(tmp_path, monkeypatch):
    env = make_env(tmp_path)
    monkeypatch.setenv("WQ_DB", env["WQ_DB"])
    tid = wq_add(env, "wq1442 sweep: dry run case")
    set_model(env["WQ_DB"], tid, "opus-5")

    results = sweep_mod.sweep(db_path=env["WQ_DB"], wq_bin=WQ_BIN, ids=IDS, aliases=ALIASES,
                               dry_run=True)

    entry = next(r for r in results if r["id"] == tid)
    assert entry["action"] == "canonicalize"
    assert entry["applied"] == "dry_run"
    assert read_model(env["WQ_DB"], tid) == "opus-5"  # untouched


def test_done_ticket_with_bad_model_is_not_swept(tmp_path, monkeypatch):
    env = make_env(tmp_path)
    monkeypatch.setenv("WQ_DB", env["WQ_DB"])
    tid = wq_add(env, "wq1442 sweep: done ticket ignored")
    set_model(env["WQ_DB"], tid, "totally-made-up-model", status="done")

    results = sweep_mod.sweep(db_path=env["WQ_DB"], wq_bin=WQ_BIN, ids=IDS, aliases=ALIASES)

    assert all(r["id"] != tid for r in results)
    assert read_model(env["WQ_DB"], tid) == "totally-made-up-model"


def test_blocked_ticket_is_swept(tmp_path, monkeypatch):
    env = make_env(tmp_path)
    monkeypatch.setenv("WQ_DB", env["WQ_DB"])
    tid = wq_add(env, "wq1442 sweep: blocked ticket swept")
    set_model(env["WQ_DB"], tid, "opus-5", status="blocked")

    results = sweep_mod.sweep(db_path=env["WQ_DB"], wq_bin=WQ_BIN, ids=IDS, aliases=ALIASES)

    entry = next(r for r in results if r["id"] == tid)
    assert entry["action"] == "canonicalize"
    assert entry["applied"] is True
