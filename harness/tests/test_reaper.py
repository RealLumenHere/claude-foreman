"""harness/reaper.py — RELIABILITY_SPEC.md §E / wq#647 (fq#614 fix). Zero-quota: a real
`wq reap` + a real `aq.py add` subprocess call against scratch WQ_DB/AQ_DB, no claude
involved. Companion to test_wq_reap.py, which covers `wq reap` itself in isolation.
"""
import datetime
import os
import sqlite3
import subprocess
import sys

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REAPER = os.path.join(HARNESS_DIR, "reaper.py")
WQ_BIN = "/home/lumen/bin/wq"
AQ_CLI = "/home/lumen/agents/alert-queue/aq.py"
FMT = "%Y-%m-%d %H:%M:%S"


def make_env(tmp_path, agent="test-reaper-agent"):
    env = dict(os.environ)
    env["WQ_DB"] = str(tmp_path / "scratch_wq.db")
    env["AQ_DB"] = str(tmp_path / "scratch_aq.db")
    env["WQ_AGENT"] = agent
    env["FOREMAN_WQ_BIN"] = WQ_BIN
    env["FOREMAN_AQ_CLI"] = AQ_CLI
    return env


def wq(env, *args):
    return subprocess.run([WQ_BIN] + list(args), capture_output=True, text=True, env=env)


def wq_add(env, title):
    p = wq(env, "add", "--title", title)
    assert p.returncode == 0, p.stdout + p.stderr
    return int(p.stdout.strip().split()[2])


def force_expire(db_path, ticket_id, minutes_ago=5):
    now = datetime.datetime.now(datetime.timezone.utc)
    past = (now - datetime.timedelta(minutes=minutes_ago)).strftime(FMT)
    c = sqlite3.connect(db_path)
    c.execute("UPDATE tasks SET lease_expires=? WHERE id=?", (past, ticket_id))
    c.commit()
    c.close()


def aq_open_rows(env):
    if not os.path.exists(env["AQ_DB"]):
        return []
    c = sqlite3.connect(env["AQ_DB"])
    rows = c.execute(
        "SELECT source, key, title, message, count FROM alerts WHERE status='open'").fetchall()
    c.close()
    return rows


def run_reaper(env, *args):
    cmd = [sys.executable, REAPER] + list(args)
    return subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=30)


def test_sweep_posts_an_aq_row_per_reap(tmp_path):
    env = make_env(tmp_path)
    ticket_id = wq_add(env, "scratch reaper ticket")
    wq(env, "claim", "--id", str(ticket_id))
    force_expire(env["WQ_DB"], ticket_id)

    result = run_reaper(env, "sweep")
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"reclaimed wq#{ticket_id}" in result.stdout

    rows = aq_open_rows(env)
    assert len(rows) == 1
    source, key, title, message, count = rows[0]
    assert source == "foreman-reaper"
    assert key == f"wq{ticket_id}"
    assert f"wq#{ticket_id}" in title
    assert "holder=" in message

    show = wq(env, "show", str(ticket_id)).stdout
    assert "status       : open" in show


def test_sweep_with_nothing_to_reap_posts_no_aq_row(tmp_path):
    env = make_env(tmp_path)
    wq_add(env, "scratch untouched ticket")

    result = run_reaper(env, "sweep")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "nothing to reap" in result.stdout
    assert aq_open_rows(env) == []


def test_sweep_dedupes_repeat_reaps_of_the_same_ticket_into_one_bumped_row(tmp_path):
    # a ticket that keeps getting claimed and dying keeps reaping — aq's own (source,
    # key) dedup means that shows up as one row with count>1, not a flood.
    env = make_env(tmp_path)
    ticket_id = wq_add(env, "scratch repeat-reap ticket")

    for _ in range(2):
        wq(env, "claim", "--id", str(ticket_id))
        force_expire(env["WQ_DB"], ticket_id)
        result = run_reaper(env, "sweep")
        assert result.returncode == 0, result.stdout + result.stderr

    rows = aq_open_rows(env)
    assert len(rows) == 1
    assert rows[0][4] == 2  # count
