"""fworker.resolve_exec_grant unit coverage (wq#1332, §2.2 point 2 / §2.4).

Covers: a ticket with no exec-shaped needs gets no grant at all; ssh(node) is always
grantable (argv allowlist is a per-invocation broker concern, not knowable at spawn
time); copy/restart tags that the static policy forbids outright are a SpawnRefused
(uncharged), never a silent downgrade; the written grant.json is 0400 and its sha256
matches what fleet_exec_policy.load_grant would recompute; FOREMAN_NEEDS_DISABLE=1 is
the rollback (no grant, no refusal, needs-blind).
"""
import json
import os
import stat
import sys

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HARNESS_DIR)
import fworker  # noqa: E402
import needs as needs_mod  # noqa: E402
import fleet_exec_policy  # noqa: E402

POLICY_YAML = """
never:
  nodes: [thor]
  power: [thor, lumen, maat, sobek, bes]
  restart:
    lumen: ["vaultwarden*", "postgresql*"]
    maat: ["*"]
  sudo_argv_deny: [ip]
run_readonly_cmds: [true, uptime]
copy_dirs:
  isis: [/home/isis/lumen-drop]
  thor: [/home/thor/lumen-drop]
bench_tools: {}
"""


def setup_policy(tmp_path, monkeypatch):
    p = tmp_path / "fleet-exec-policy.yaml"
    p.write_text(POLICY_YAML)
    monkeypatch.setattr(fworker, "FLEET_EXEC_POLICY_PATH", str(p))
    monkeypatch.setattr(fworker, "RUNS_ROOT", str(tmp_path / "_runs"))


def test_no_exec_needs_returns_no_grant(tmp_path, monkeypatch):
    setup_policy(tmp_path, monkeypatch)
    n = needs_mod.parse("dir(/home/lumen/kb)")
    grant, sha, path = fworker.resolve_exec_grant(1332, 1, n, 3600)
    assert (grant, sha, path) == (None, None, None)


def test_ssh_tag_always_grantable(tmp_path, monkeypatch):
    setup_policy(tmp_path, monkeypatch)
    n = needs_mod.parse("ssh(isis)")
    grant, sha, path = fworker.resolve_exec_grant(1332, 1, n, 3600)
    assert grant["grants"] == [{"kind": "ssh", "node": "isis", "verbs": ["run"]}]
    assert grant["ticket"] == 1332 and grant["attempt"] == 1
    assert sha == fleet_exec_policy.sha256_bytes(open(path, "rb").read())


def test_ssh_tag_for_thor_still_grantable_readonly_run_only_at_broker_time(tmp_path, monkeypatch):
    """Grant-time resolution doesn't know the eventual argv, so ssh(thor) grants —
    the broker's own check_run() is what actually confines thor to readonly."""
    setup_policy(tmp_path, monkeypatch)
    n = needs_mod.parse("ssh(thor)")
    grant, sha, path = fworker.resolve_exec_grant(1332, 1, n, 3600)
    assert grant["grants"] == [{"kind": "ssh", "node": "thor", "verbs": ["run"]}]


def test_copy_tag_allowed_dir_grants(tmp_path, monkeypatch):
    setup_policy(tmp_path, monkeypatch)
    n = needs_mod.parse("copy(isis:/home/isis/lumen-drop)")
    grant, sha, path = fworker.resolve_exec_grant(1332, 1, n, 3600)
    assert grant["grants"] == [{"kind": "copy", "node": "isis", "dir": "/home/isis/lumen-drop"}]


def test_copy_tag_disallowed_dir_refuses_spawn(tmp_path, monkeypatch):
    setup_policy(tmp_path, monkeypatch)
    n = needs_mod.parse("copy(isis:/etc)")
    try:
        fworker.resolve_exec_grant(1332, 1, n, 3600)
        assert False, "expected SpawnRefused"
    except fworker.SpawnRefused as e:
        assert e.code == "CAPABILITY_DENIED_BY_POLICY"
        assert "copy(isis:/etc)" in e.summary


def test_restart_tag_allowed_unit_grants(tmp_path, monkeypatch):
    setup_policy(tmp_path, monkeypatch)
    n = needs_mod.parse("restart(ollama@isis)")
    grant, sha, path = fworker.resolve_exec_grant(1332, 1, n, 3600)
    assert grant["grants"] == [{"kind": "restart", "node": "isis", "unit": "ollama"}]


def test_restart_tag_never_list_refuses_spawn(tmp_path, monkeypatch):
    setup_policy(tmp_path, monkeypatch)
    n = needs_mod.parse("restart(vaultwarden@lumen)")
    try:
        fworker.resolve_exec_grant(1332, 1, n, 3600)
        assert False, "expected SpawnRefused"
    except fworker.SpawnRefused as e:
        assert e.code == "CAPABILITY_DENIED_BY_POLICY"
        assert "vaultwarden@lumen" in e.summary


def test_restart_tag_thor_hardlock_refuses_even_if_policy_row_missing(tmp_path, monkeypatch):
    """thor isn't in the never.restart per-node map (no restart row needed — it's
    covered by never.nodes) but resolve_exec_grant must still refuse it."""
    setup_policy(tmp_path, monkeypatch)
    n = needs_mod.parse("restart(anything@thor)")
    try:
        fworker.resolve_exec_grant(1332, 1, n, 3600)
        assert False, "expected SpawnRefused"
    except fworker.SpawnRefused as e:
        assert e.code == "CAPABILITY_DENIED_BY_POLICY"


def test_restart_tag_maat_wildcard_refuses(tmp_path, monkeypatch):
    setup_policy(tmp_path, monkeypatch)
    n = needs_mod.parse("restart(anything@maat)")
    try:
        fworker.resolve_exec_grant(1332, 1, n, 3600)
        assert False, "expected SpawnRefused"
    except fworker.SpawnRefused:
        pass


def test_mixed_tags_one_forbidden_refuses_whole_spawn_not_a_partial_grant(tmp_path, monkeypatch):
    """§2.2 point 2: a forbidden tag is a dispatch REFUSAL, never a silent downgrade
    — the ticket doesn't get a grant with just the allowed half."""
    setup_policy(tmp_path, monkeypatch)
    n = needs_mod.parse("ssh(isis), restart(anything@maat)")
    try:
        fworker.resolve_exec_grant(1332, 1, n, 3600)
        assert False, "expected SpawnRefused"
    except fworker.SpawnRefused:
        pass


def test_grant_file_is_0444_and_expires_from_wall_clock(tmp_path, monkeypatch):
    setup_policy(tmp_path, monkeypatch)
    n = needs_mod.parse("ssh(isis)")
    grant, sha, path = fworker.resolve_exec_grant(1332, 1, n, 3600)
    mode = stat.S_IMODE(os.stat(path).st_mode)
    # 0400 until wq#2270: the token-engine daemon (uid tokeneng) re-reads and
    # re-hashes the file itself, so it must be world-readable. The assertion kept
    # naming 0400 and had been failing ever since (noticed under wq#2349).
    assert mode == 0o444
    # expiry is ~wall_clock_s from now, not some other arbitrary horizon
    from datetime import datetime, timezone
    expires = datetime.fromisoformat(grant["expires"].replace("Z", "+00:00"))
    delta = (expires - datetime.now(timezone.utc)).total_seconds()
    assert 3500 < delta < 3700


def test_grant_file_overwritable_on_second_attempt(tmp_path, monkeypatch):
    """A resume/retry re-resolves the grant (fresh lease) rather than being stuck
    behind the first attempt's 0400 file."""
    setup_policy(tmp_path, monkeypatch)
    n = needs_mod.parse("ssh(isis)")
    grant1, sha1, path1 = fworker.resolve_exec_grant(1332, 1, n, 3600)
    grant2, sha2, path2 = fworker.resolve_exec_grant(1332, 2, n, 1800)
    assert path1 == path2
    assert grant2["attempt"] == 2
    assert json.loads(open(path2).read())["attempt"] == 2


def test_policy_unreadable_refuses_spawn_when_exec_tags_present(tmp_path, monkeypatch):
    monkeypatch.setattr(fworker, "FLEET_EXEC_POLICY_PATH", str(tmp_path / "does-not-exist.yaml"))
    monkeypatch.setattr(fworker, "RUNS_ROOT", str(tmp_path / "_runs"))
    n = needs_mod.parse("ssh(isis)")
    try:
        fworker.resolve_exec_grant(1332, 1, n, 3600)
        assert False, "expected SpawnRefused"
    except fworker.SpawnRefused as e:
        assert e.code == "CAPABILITY_DENIED_BY_POLICY"


# ---- wq#2349: the ticket-end grant (TOKEN_ENGINE_DESIGN.md §3.4) ----
# Separate from the exec grant above by design: resolve_exec_grant mints only for
# needs:ssh/copy/restart tickets, so the SessionEnd revoke — which fell back to
# FOREMAN_GRANT_FILE — was denied grant:grant_missing_env 283 times out of 283.


def test_ticket_end_grant_is_written_for_a_ticket_with_no_exec_needs(tmp_path, monkeypatch):
    """The missing-grant path this ticket exists for: no exec needs, hence no
    FOREMAN_GRANT_FILE, and the session-end revoke still gets a grant of its own."""
    setup_policy(tmp_path, monkeypatch)
    n = needs_mod.parse("dir(/home/lumen/kb)")
    assert fworker.resolve_exec_grant(2349, 1, n, 3600) == (None, None, None)
    path, sha = fworker.write_ticket_end_grant(2349, 1, "fworker-wq2349-a1", 3600)
    grant = json.loads(open(path).read())
    assert grant["grants"] == [{"kind": "token_engine", "node": "daemon"}]
    assert grant["ticket"] == "2349"  # what guard.sh sends as FOREMAN_TICKET_ID
    assert grant["worker"] == "fworker-wq2349-a1"
    assert sha == fleet_exec_policy.sha256_bytes(open(path, "rb").read())


def test_ticket_end_grant_passes_load_grant(tmp_path, monkeypatch):
    """End to end against the broker's own check: the frame guard.sh sends
    (ticket + worker + file + sha) is what load_grant is handed, and it verifies."""
    setup_policy(tmp_path, monkeypatch)
    path, sha = fworker.write_ticket_end_grant(2349, 1, "fworker-wq2349-a1", 3600)
    grant, why = fleet_exec_policy.load_grant(path, sha, "2349", "fworker-wq2349-a1")
    assert why is None and grant is not None
    assert fleet_exec_policy.find_grants(grant, "token_engine", "daemon")


def test_ticket_end_grant_is_bound_to_its_own_ticket_and_worker(tmp_path, monkeypatch):
    """Scope check: the grant revokes this attempt's tokens and nothing else."""
    setup_policy(tmp_path, monkeypatch)
    path, sha = fworker.write_ticket_end_grant(2349, 1, "fworker-wq2349-a1", 3600)
    assert fleet_exec_policy.load_grant(path, sha, "2301", "fworker-wq2349-a1")[1] == \
        "grant_ticket_mismatch"
    assert fleet_exec_policy.load_grant(path, sha, "2349", "fworker-wq2301-a1")[1] == \
        "grant_worker_mismatch"
    assert fleet_exec_policy.load_grant(path, "0" * 64, "2349", "fworker-wq2349-a1")[1] == \
        "grant_sha_mismatch"


def test_ticket_end_grant_outlives_the_wall_clock_ceiling(tmp_path, monkeypatch):
    """SessionEnd fires AFTER the child exits — at the ceiling itself for a watchdog
    kill — so a lease of exactly wall_clock_s would expire inside its own hook."""
    from datetime import datetime, timezone
    setup_policy(tmp_path, monkeypatch)
    path, sha = fworker.write_ticket_end_grant(2349, 1, "fworker-wq2349-a1", 3600)
    grant = json.loads(open(path).read())
    expires = datetime.fromisoformat(grant["expires"].replace("Z", "+00:00"))
    delta = (expires - datetime.now(timezone.utc)).total_seconds()
    assert 3600 < delta <= 3600 + fworker.TICKET_END_GRANT_MARGIN_S


def test_ticket_end_grant_is_0444_and_per_attempt(tmp_path, monkeypatch):
    """0444: uid tokeneng re-reads and re-hashes it. Per attempt: attempt 1's hook
    can fire after attempt 2 starts, and a shared path would leave attempt 1 holding
    a sha of rewritten bytes (grant_sha_mismatch — the same denial in a new costume)."""
    setup_policy(tmp_path, monkeypatch)
    p1, sha1 = fworker.write_ticket_end_grant(2349, 1, "fworker-wq2349-a1", 3600)
    p2, sha2 = fworker.write_ticket_end_grant(2349, 2, "fworker-wq2349-a2", 3600)
    assert p1 != p2
    assert stat.S_IMODE(os.stat(p1).st_mode) == 0o444
    assert fleet_exec_policy.load_grant(p1, sha1, "2349", "fworker-wq2349-a1")[1] is None
    assert fleet_exec_policy.load_grant(p2, sha2, "2349", "fworker-wq2349-a2")[1] is None


def test_ticket_end_grant_failure_is_not_a_dispatch_failure(tmp_path, monkeypatch):
    """Best-effort like mint_gateway_key: an unwritable run dir costs a revoke (the
    daemon-start reaper still sweeps), never the attempt."""
    setup_policy(tmp_path, monkeypatch)
    monkeypatch.setattr(fworker, "run_dir", lambda t: "/proc/nonexistent-wq2349")
    assert fworker.write_ticket_end_grant(2349, 1, "fworker-wq2349-a1", 3600) == (None, None)


def test_foreman_needs_disable_is_the_rollback(tmp_path, monkeypatch):
    setup_policy(tmp_path, monkeypatch)
    monkeypatch.setenv("FOREMAN_NEEDS_DISABLE", "1")
    n = needs_mod.parse("restart(anything@maat)")  # would otherwise refuse
    assert not n  # needs_mod.parse() itself returns empty under the flag
    grant, sha, path = fworker.resolve_exec_grant(1332, 1, n, 3600)
    assert (grant, sha, path) == (None, None, None)
