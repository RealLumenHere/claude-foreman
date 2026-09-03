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


def test_grant_file_is_0400_and_expires_from_wall_clock(tmp_path, monkeypatch):
    setup_policy(tmp_path, monkeypatch)
    n = needs_mod.parse("ssh(isis)")
    grant, sha, path = fworker.resolve_exec_grant(1332, 1, n, 3600)
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o400
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


def test_foreman_needs_disable_is_the_rollback(tmp_path, monkeypatch):
    setup_policy(tmp_path, monkeypatch)
    monkeypatch.setenv("FOREMAN_NEEDS_DISABLE", "1")
    n = needs_mod.parse("restart(anything@maat)")  # would otherwise refuse
    assert not n  # needs_mod.parse() itself returns empty under the flag
    grant, sha, path = fworker.resolve_exec_grant(1332, 1, n, 3600)
    assert (grant, sha, path) == (None, None, None)
