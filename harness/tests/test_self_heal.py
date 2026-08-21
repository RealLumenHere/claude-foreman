"""harness/self_heal.py — wq#725. Real `wq` subprocess against a scratch WQ_DB, same
zero-quota pattern as test_reaper.py: no claude involved, no fworker spawn needed
since the structured block-reason prefix (fworker.block_reason_class) can be written
directly via `wq block <id> "<reason>"`.
"""
import datetime
import json
import os
import subprocess
import sys

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SELF_HEAL = os.path.join(HARNESS_DIR, "self_heal.py")
WQ_BIN = "/home/lumen/bin/wq"
TS_FMT = "%Y-%m-%dT%H:%M:%SZ"


def make_env(tmp_path, agent="test-self-heal-agent"):
    env = dict(os.environ)
    env["WQ_DB"] = str(tmp_path / "scratch_wq.db")
    env["WQ_AGENT"] = agent
    env["FOREMAN_WQ_BIN"] = WQ_BIN
    env["FOREMAN_SELF_HEAL_STATE"] = str(tmp_path / "self_heal_state.json")
    # tests never expect a live ntfy-send binary on PATH
    env["FOREMAN_NTFY_BIN"] = "/bin/true"
    return env


def wq(env, *args):
    return subprocess.run([WQ_BIN] + list(args), capture_output=True, text=True, env=env)


def wq_add(env, title):
    p = wq(env, "add", "--title", title)
    assert p.returncode == 0, p.stdout + p.stderr
    return int(p.stdout.strip().split()[2])


def wq_show(env, ticket_id):
    p = wq(env, "show", str(ticket_id))
    out = {}
    for line in p.stdout.splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        out[k.strip()] = v.strip()
    return out


def block_with_reason(env, ticket_id, cls, minutes_ago, detail="test detail"):
    wq(env, "claim", "--id", str(ticket_id))
    at = (datetime.datetime.now(datetime.timezone.utc)
          - datetime.timedelta(minutes=minutes_ago)).strftime(TS_FMT)
    reason = f"{cls}[at={at}]: {detail}"
    p = wq(env, "block", str(ticket_id), reason)
    assert p.returncode == 0, p.stdout + p.stderr
    return reason


def run_self_heal(env, *args):
    cmd = [sys.executable, SELF_HEAL] + list(args)
    return subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=30)


def test_sweep_releases_fresh_infra_crash(tmp_path):
    env = make_env(tmp_path)
    ticket_id = wq_add(env, "scratch fresh infra-crash ticket")
    block_with_reason(env, ticket_id, "INFRA_CRASH", minutes_ago=2)

    result = run_self_heal(env, "sweep")
    assert result.returncode == 0, result.stdout + result.stderr
    assert wq_show(env, ticket_id)["status"] == "open"

    state = json.load(open(env["FOREMAN_SELF_HEAL_STATE"]))
    assert state[str(ticket_id)]["heals"] == 1


def test_sweep_never_releases_work_blocked(tmp_path):
    env = make_env(tmp_path)
    ticket_id = wq_add(env, "scratch work-blocked ticket")
    block_with_reason(env, ticket_id, "WORK_BLOCKED", minutes_ago=2)

    result = run_self_heal(env, "sweep")
    assert result.returncode == 0, result.stdout + result.stderr
    assert wq_show(env, ticket_id)["status"] == "blocked"


def test_sweep_never_releases_limit_hit(tmp_path):
    env = make_env(tmp_path)
    ticket_id = wq_add(env, "scratch limit-hit ticket")
    block_with_reason(env, ticket_id, "LIMIT_HIT", minutes_ago=2)

    result = run_self_heal(env, "sweep")
    assert result.returncode == 0, result.stdout + result.stderr
    assert wq_show(env, ticket_id)["status"] == "blocked"


def test_sweep_leaves_unstructured_block_reason_alone(tmp_path):
    # a ticket a human blocked by hand (no prefix at all) has no evidence it's
    # environmental — never guessed as auto-healable.
    env = make_env(tmp_path)
    ticket_id = wq_add(env, "scratch human-blocked ticket")
    wq(env, "claim", "--id", str(ticket_id))
    wq(env, "block", str(ticket_id), "needs Frank to pick the auth provider")

    result = run_self_heal(env, "sweep")
    assert result.returncode == 0, result.stdout + result.stderr
    assert wq_show(env, ticket_id)["status"] == "blocked"


def test_sweep_leaves_stale_infra_crash_blocked_and_notifies_once(tmp_path):
    env = make_env(tmp_path)
    ticket_id = wq_add(env, "scratch stale infra-crash ticket")
    block_with_reason(env, ticket_id, "INFRA_CRASH", minutes_ago=45)

    result = run_self_heal(env, "sweep")
    assert result.returncode == 0, result.stdout + result.stderr
    assert wq_show(env, ticket_id)["status"] == "blocked"

    state = json.load(open(env["FOREMAN_SELF_HEAL_STATE"]))
    assert state[str(ticket_id)]["notified"] is True

    # a second sweep must not re-notify (state carries the notified flag forward)
    result2 = run_self_heal(env, "sweep")
    assert result2.returncode == 0
    assert "notifying Frank" not in result2.stdout


def test_sweep_stops_auto_releasing_after_max_heals(tmp_path):
    env = make_env(tmp_path)
    ticket_id = wq_add(env, "scratch repeat infra-crash ticket")

    for _ in range(5):
        block_with_reason(env, ticket_id, "INFRA_CRASH", minutes_ago=2)
        result = run_self_heal(env, "sweep")
        assert result.returncode == 0, result.stdout + result.stderr
        assert wq_show(env, ticket_id)["status"] == "open"

    # 6th block: heal count is already at the ceiling, must stay blocked this time
    block_with_reason(env, ticket_id, "INFRA_CRASH", minutes_ago=2)
    result = run_self_heal(env, "sweep")
    assert result.returncode == 0, result.stdout + result.stderr
    assert wq_show(env, ticket_id)["status"] == "blocked"


def test_dry_run_touches_nothing(tmp_path):
    env = make_env(tmp_path)
    ticket_id = wq_add(env, "scratch dry-run ticket")
    block_with_reason(env, ticket_id, "INFRA_CRASH", minutes_ago=2)

    result = run_self_heal(env, "sweep", "--dry-run")
    assert result.returncode == 0, result.stdout + result.stderr
    assert wq_show(env, ticket_id)["status"] == "blocked"
    assert not os.path.exists(env["FOREMAN_SELF_HEAL_STATE"])


def test_sweep_with_nothing_blocked_is_a_noop(tmp_path):
    env = make_env(tmp_path)
    wq_add(env, "scratch open ticket, never blocked")

    result = run_self_heal(env, "sweep")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "nothing blocked" in result.stdout
