"""capability.preflight unit coverage (wq#1436, BROKER_WIRING.md §1 / §2.5, slice A).

Every Verdict reason has a row: ok, cited_path_unreachable (both shapes — a missing
needs:dir and a cited literal outside the reachable set), node_unknown (both halves of
the AND: no tailscale_ip fact, no ssh Host entry), capability_denied_by_policy (a
policy deny and an unreadable policy file). Plus the two invariants slice A exists to
hold: FOREMAN_NEEDS_DISABLE=1 downgrades every refusal to log-only, and there is ONE
copy of this logic — fworker imports it rather than keeping its own.
"""
import os
import sys

import pytest

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HARNESS_DIR)
import capability  # noqa: E402
import failure_record  # noqa: E402
import fworker  # noqa: E402
import needs as needs_mod  # noqa: E402

POLICY_YAML = """
never:
  nodes: [thor]
  power: [thor, lumen, maat]
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

# lumen_state.py current output shape (subject.predicate = value (since YYYY-MM-DD)).
STATE = ("isis.tailscale_ip = 100.101.1.1 (since 2026-01-01)\n"
         "thor.tailscale_ip = 100.112.151.100 (since 2026-01-01)\n"
         "maat.tailscale_ip = 100.101.1.2 (since 2026-01-01)\n"
         "orphan.tailscale_ip = 100.101.1.9 (since 2026-01-01)\n")
HOSTS = {"isis", "thor", "maat", "no-fact-node"}


def policy_path(tmp_path):
    p = tmp_path / "fleet-exec-policy.yaml"
    p.write_text(POLICY_YAML)
    return str(p)


def pf(tmp_path, detail="", needs="", add_dirs=(), worktree=None, repo="/home/lumen/foreman"):
    return capability.preflight(
        {"detail": detail}, repo, list(add_dirs), needs_mod.parse(needs),
        worktree=worktree, policy_path=policy_path(tmp_path),
        state_output=STATE, ssh_hosts=HOSTS)


# ---- ok ------------------------------------------------------------------------------

def test_reachable_ticket_passes(tmp_path):
    v = pf(tmp_path, detail="fix /home/lumen/foreman/harness/fworker.py, read "
                            "/home/lumen/kb/ISSUE-LOG.md", add_dirs=["/home/lumen/kb"])
    assert v.ok and v.reason is None and v.code is None
    assert v.unreachable_paths == [] and v.refused == []


def test_grantable_exec_tags_pass(tmp_path):
    v = pf(tmp_path, needs="ssh(isis), restart(ollama@isis), copy(isis:/home/isis/lumen-drop)")
    assert v.ok, v.summary


# ---- cited_path_unreachable ------------------------------------------------------------

def test_cited_path_outside_roots_refused(tmp_path):
    # the wq#1152 signature: the ticket cites a tree the sandbox cannot reach.
    v = pf(tmp_path, detail="flash it with /home/lumen/nle-flasher/flash.sh")
    assert not v.ok
    assert v.reason == capability.REASON_CITED_PATH_UNREACHABLE
    assert v.code == "CITED_PATH_UNREACHABLE"
    assert v.unreachable_paths == ["/home/lumen/nle-flasher/flash.sh"]


def test_missing_needs_dir_refused(tmp_path):
    v = pf(tmp_path, needs=f"dir({tmp_path}/does-not-exist)")
    assert v.reason == capability.REASON_CITED_PATH_UNREACHABLE
    assert "not an existing directory" in v.summary
    assert v.unreachable_paths == [f"{tmp_path}/does-not-exist"]


def test_needs_dir_in_add_dirs_makes_cited_path_reachable(tmp_path):
    d = tmp_path / "nle-flasher"
    d.mkdir()
    v = pf(tmp_path, detail=f"flash it with {d}/flash.sh", needs=f"dir({d})",
           add_dirs=[str(d)])
    assert v.ok, v.summary


def test_worktree_counts_as_a_root_only_when_given(tmp_path):
    detail = f"edit {tmp_path}/wt/file.py"
    assert pf(tmp_path, detail=detail, worktree=str(tmp_path / "wt")).ok
    # at dispatch time there is no worktree yet; a /home/lumen literal outside repo ∪
    # add_dirs is still unreachable, which is what the pre-skip must see.
    v = pf(tmp_path, detail="edit /home/lumen/agent/brain.py")
    assert v.reason == capability.REASON_CITED_PATH_UNREACHABLE


def test_prefix_sibling_dir_is_not_under_root():
    # /home/lumen/kb-evil must not ride on the /home/lumen/kb grant.
    assert not capability._under("/home/lumen/kb-evil/x", "/home/lumen/kb")
    assert capability._under("/home/lumen/kb/x", "/home/lumen/kb")
    assert capability._under("/home/lumen/kb", "/home/lumen/kb")


def test_cited_paths_strips_sentence_punctuation():
    assert capability.cited_paths(
        "read /home/lumen/kb/ISSUE-LOG.md, then /home/lumen/foreman/harness/guard.sh; "
        "bare /home/lumen does not count") == [
        "/home/lumen/kb/ISSUE-LOG.md", "/home/lumen/foreman/harness/guard.sh"]


# ---- node_unknown ----------------------------------------------------------------------

def test_node_with_no_ssh_alias_refused(tmp_path):
    # orphan IS a lumen_state subject with a tailscale_ip — fleet-exec's known_nodes()
    # would accept it — but it has no Host entry, so it dies at transport time.
    v = pf(tmp_path, needs="ssh(orphan)")
    assert not v.ok
    assert v.reason == capability.REASON_NODE_UNKNOWN
    assert v.code == "NODE_UNKNOWN" and v.nodes_unknown == ["orphan"]


def test_node_with_no_tailscale_fact_refused(tmp_path):
    v = pf(tmp_path, needs="ssh(no-fact-node)")
    assert v.reason == capability.REASON_NODE_UNKNOWN


def test_unknown_node_on_restart_and_copy_tags_too(tmp_path):
    assert pf(tmp_path, needs="restart(ollama@ghost)").reason == \
        capability.REASON_NODE_UNKNOWN
    assert pf(tmp_path, needs="copy(ghost:/home/ghost/lumen-drop)").reason == \
        capability.REASON_NODE_UNKNOWN


def test_empty_state_roster_fails_closed(tmp_path):
    # unreachable minerva -> empty roster -> every node refused, never skipped.
    v = capability.preflight({"detail": ""}, "/home/lumen/foreman", [],
                             needs_mod.parse("ssh(isis)"),
                             policy_path=policy_path(tmp_path),
                             state_output="", ssh_hosts=HOSTS)
    assert v.reason == capability.REASON_NODE_UNKNOWN


def test_ssh_config_wildcard_host_does_not_make_every_node_addressable(tmp_path):
    cfg = tmp_path / "config"
    cfg.write_text("Host *\n  User lumen\n\nHost isis isis.tail\n  HostName 100.101.1.1\n")
    hosts = capability.ssh_config_hosts([str(cfg)])
    assert hosts == {"isis", "isis.tail"}
    assert capability.node_reachable("isis", state_output=STATE, ssh_hosts=hosts)
    assert not capability.node_reachable("maat", state_output=STATE, ssh_hosts=hosts)


def test_missing_ssh_config_is_not_a_crash(tmp_path):
    assert capability.ssh_config_hosts([str(tmp_path / "nope")]) == set()


def test_ssh_config_include_is_followed(tmp_path):
    (tmp_path / "config.d").mkdir()
    (tmp_path / "config.d" / "fleet").write_text("Host isis\n  HostName 100.101.1.1\n")
    cfg = tmp_path / "config"
    cfg.write_text("Include config.d/*\nHost lumen\n")
    assert capability.ssh_config_hosts([str(cfg)]) == {"isis", "lumen"}


def test_ssh_config_include_cycle_terminates(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_text(f"Include {b}\nHost isis\n")
    b.write_text(f"Include {a}\nHost maat\n")
    assert capability.ssh_config_hosts([str(a)]) == {"isis", "maat"}


# ---- capability_denied_by_policy --------------------------------------------------------

def test_restart_on_never_list_node_refused(tmp_path):
    v = pf(tmp_path, needs="restart(anything@maat)")
    assert not v.ok
    assert v.reason == capability.REASON_CAPABILITY_DENIED_BY_POLICY
    assert v.code == "CAPABILITY_DENIED_BY_POLICY"
    assert v.refused and "restart(anything@maat)" in v.refused[0]


def test_thor_hardlock_refused_even_with_an_ssh_alias(tmp_path):
    # thor resolves as a node (fact + Host entry) — the refusal must come from policy,
    # and it must come regardless of what the policy file says (THOR_HARDLOCK).
    v = pf(tmp_path, needs="restart(tailscaled@thor)")
    assert v.reason == capability.REASON_CAPABILITY_DENIED_BY_POLICY


def test_copy_outside_drop_dir_refused(tmp_path):
    v = pf(tmp_path, needs="copy(isis:/etc)")
    assert v.reason == capability.REASON_CAPABILITY_DENIED_BY_POLICY


def test_partial_deny_is_a_refusal_not_a_downgrade(tmp_path):
    v = pf(tmp_path, needs="ssh(isis), restart(anything@maat)")
    assert not v.ok
    grants, refused = capability.policy_grants(needs_mod.parse("ssh(isis), restart(anything@maat)"),
                                               policy_path=policy_path(tmp_path))
    assert refused and grants == [{"kind": "ssh", "node": "isis", "verbs": ["run"]}]


def test_unreadable_policy_file_refuses(tmp_path):
    v = capability.preflight({"detail": ""}, "/home/lumen/foreman", [],
                             needs_mod.parse("ssh(isis)"),
                             policy_path=str(tmp_path / "does-not-exist.yaml"),
                             state_output=STATE, ssh_hosts=HOSTS)
    assert v.reason == capability.REASON_CAPABILITY_DENIED_BY_POLICY
    assert "unreadable" in v.summary


def test_no_exec_tags_never_reads_the_policy_file(tmp_path):
    # a dir-only ticket must not be refusable by a policy file that isn't there.
    v = capability.preflight({"detail": ""}, "/home/lumen/foreman", [],
                             needs_mod.parse("dir(/home/lumen/kb)"),
                             policy_path=str(tmp_path / "does-not-exist.yaml"),
                             state_output=STATE, ssh_hosts=HOSTS)
    assert v.ok


# ---- peer (wq#3200): fixed-argv exec path -----------------------------------------------
# policy_grants delegates to fleet_exec_policy.check_peer from the INSTALLED
# /home/lumen/bin — the grant-path test skips until the lumen-bin PR carrying
# check_peer lands; every refusal-path test must hold on both versions.

PEER_POLICY_YAML = POLICY_YAML + (
    "peer_cmds:\n"
    "  isis:\n"
    "    demo-peer:\n"
    "      argv: [/home/isis/peers/demo/run.sh]\n"
    "      sha256: " + "ab" * 32 + "\n"
)


def peer_policy_path(tmp_path):
    p = tmp_path / "fleet-exec-policy.yaml"
    p.write_text(PEER_POLICY_YAML)
    return str(p)


@pytest.mark.skipif(not hasattr(capability.fleet_exec_policy, "check_peer"),
                    reason="installed fleet_exec_policy predates the lumen-bin peer PR")
def test_peer_tag_grants_when_policy_lists_the_name(tmp_path):
    grants, refused = capability.policy_grants(needs_mod.parse("peer(demo-peer@isis)"),
                                               policy_path=peer_policy_path(tmp_path))
    assert not refused, refused
    assert grants == [{"kind": "ssh", "node": "isis", "verbs": ["peer"],
                       "peers": ["demo-peer"]}]


def test_peer_tag_unlisted_name_refused_never_granted(tmp_path):
    # holds on BOTH module versions: old fleet_exec_policy refuses for lack of
    # check_peer, new one refuses via policy — a peer tag must never fail open.
    grants, refused = capability.policy_grants(needs_mod.parse("peer(unlisted@isis)"),
                                               policy_path=peer_policy_path(tmp_path))
    assert refused and grants == []


def test_peer_tag_thor_without_reviewed_entry_refused(tmp_path):
    grants, refused = capability.policy_grants(needs_mod.parse("peer(demo-peer@thor)"),
                                               policy_path=peer_policy_path(tmp_path))
    assert refused and grants == []


# ---- rollback ---------------------------------------------------------------------------

@pytest.mark.parametrize("tags,detail", [
    ([], "flash it with /home/lumen/nle-flasher/flash.sh"),
    ([("dir", "/nope")], ""),
    ([("ssh", "orphan")], ""),
    ([("restart", "anything@maat")], ""),
])
def test_disable_env_downgrades_every_reason_to_log_only(tmp_path, monkeypatch, tags, detail):
    # Needs built directly: parse() is itself needs-blind under the flag (below), so
    # this is the path where preflight sees a refusable input and must not raise.
    monkeypatch.setenv("FOREMAN_NEEDS_DISABLE", "1")
    v = capability.preflight({"detail": detail}, "/home/lumen/foreman", [],
                             needs_mod.Needs(tags), policy_path=policy_path(tmp_path),
                             state_output=STATE, ssh_hosts=HOSTS)
    assert v.ok and v.downgraded and "FOREMAN_NEEDS_DISABLE=1" in v.summary


def test_disable_env_also_makes_the_tags_themselves_invisible(tmp_path, monkeypatch):
    monkeypatch.setenv("FOREMAN_NEEDS_DISABLE", "1")
    v = pf(tmp_path, needs="restart(anything@maat)")
    assert v.ok and not v.downgraded  # parse() returned no tags at all


# ---- one copy, not two (BROKER_WIRING.md §1) --------------------------------------------

def test_fworker_uses_this_module_and_keeps_no_second_copy():
    assert fworker.cited_paths is capability.cited_paths
    assert fworker._under is capability._under
    with open(os.path.join(HARNESS_DIR, "fworker.py")) as f:
        src = f.read()
    for gone in ("_CITED_PATH_RE", "fleet_exec_policy.check_copy",
                 "fleet_exec_policy.check_restart", "fleet_exec_policy.load_policy"):
        assert gone not in src, f"fworker.py still holds its own copy of {gone}"


def test_fworker_spawn_assert_raises_from_the_verdict(tmp_path, monkeypatch):
    monkeypatch.setattr(fworker, "FLEET_EXEC_POLICY_PATH", policy_path(tmp_path))
    monkeypatch.setenv("FOREMAN_CAPABILITY_ADVISORY", "off")   # wq#2184: else advisory
    with pytest.raises(fworker.SpawnRefused) as e:
        fworker.assert_spawn_paths(1436, "see /home/lumen/nle-flasher/flash.sh",
                                   str(tmp_path), "/home/lumen/foreman", [], [])
    assert e.value.code == "CITED_PATH_UNREACHABLE"
    assert e.value.unreachable_paths == ["/home/lumen/nle-flasher/flash.sh"]


# ---- §2.5: the refusal's lists reach failure.json ----------------------------------------

def test_failure_record_fills_unreachable_paths_and_inferred_needs():
    e = fworker.SpawnRefused("CITED_PATH_UNREACHABLE", "CITED_PATH_UNREACHABLE (...)",
                             unreachable_paths=["/home/lumen/agent"])
    rec = failure_record.build_failure_record(
        ticket=1436, attempts=0, classification="LAUNCH_FAILURE",
        block_class="HARNESS_DEFECT", events=[], node_map={}, check_worktree=False,
        spawn_refusal=e)
    assert rec["unreachable_paths"] == ["/home/lumen/agent"]
    assert rec["inferred_needs"] == ["dir(/home/lumen/agent)"]
    assert rec["refused"] == []


def test_failure_record_fills_refused_on_a_policy_denial():
    e = fworker.SpawnRefused("CAPABILITY_DENIED_BY_POLICY", "...",
                             refused=["needs:restart(x@maat) denied by never.restart: no"])
    rec = failure_record.build_failure_record(
        ticket=1436, attempts=0, classification="LAUNCH_FAILURE",
        block_class="HARNESS_DEFECT", events=[], node_map={}, check_worktree=False,
        spawn_refusal=e)
    assert rec["refused"] == ["needs:restart(x@maat) denied by never.restart: no"]
    # a forbidden capability is not a missing one — nothing to back-fill onto the ticket.
    assert rec["inferred_needs"] == [] and rec["unreachable_paths"] == []


def test_failure_record_without_a_refusal_still_mines_the_transcript():
    rec = failure_record.build_failure_record(
        ticket=1436, attempts=1, classification="CRASH", block_class="HARNESS_DEFECT",
        events=[], node_map={}, check_worktree=False)
    assert rec["refused"] == [] and rec["unreachable_paths"] == []
    assert rec["schema"] == failure_record.SCHEMA_VERSION


# ---- wq#1438 slice C: effective needs, effective add_dirs, coverage, auto-tag --------

class _Derived:
    """capability_lint.Derived's read surface, without importing the lint — these are
    capability's own contracts, and the lint has its own suite."""

    def __init__(self, tags=()):
        self.tags = list(tags)


def test_effective_needs_unions_ticket_tags_with_derived_ones():
    t = {"needs": "dir(/home/lumen/kb)", "detail": "Needs: ssh(isis)"}
    n = capability.effective_needs(t, _Derived([("dir", "/home/lumen/agent")]))
    assert n.dirs == ["/home/lumen/kb", "/home/lumen/agent"]
    assert n.ssh_nodes == ["isis"]


def test_effective_needs_dedupes_a_tag_the_ticket_already_carries():
    """A second pass over an already-auto-tagged ticket must not double it (§4 row vi)."""
    t = {"needs": "dir(/home/lumen/agent)", "detail": ""}
    n = capability.effective_needs(t, _Derived([("dir", "/home/lumen/agent")]))
    assert n.dirs == ["/home/lumen/agent"]


def test_effective_needs_drops_the_derived_half_under_the_rollback_flag(monkeypatch):
    monkeypatch.setenv("FOREMAN_NEEDS_DISABLE", "1")
    n = capability.effective_needs({"needs": "dir(/home/lumen/kb)"},
                                   _Derived([("dir", "/home/lumen/agent")]))
    assert n.dirs == []


def test_effective_add_dirs_matches_fworkers_union():
    """One answer, two callers: what the dispatcher preflights over must be exactly
    what fworker.add_dirs_for (:1049) will hand the child once the tag is written."""
    n = needs_mod.parse("dir(/home/lumen/agent), dir(/home/lumen/kb)")
    for archetype in (None, "standard-engineering"):
        assert (capability.effective_add_dirs(archetype, n)
                == fworker.add_dirs_for(archetype, n.dirs))


def test_covered_by_requires_every_path_and_treats_empty_as_unknown():
    dirs = ["/home/lumen/agent", "/home/lumen/kb"]
    assert capability.covered_by(["/home/lumen/agent/x.py"], dirs)
    assert not capability.covered_by(["/home/lumen/agent/x.py", "/home/lumen/other"], dirs)
    # a v1 failure.json wrote unreachable_paths: [] — that is "we don't know", never
    # "nothing left to cover" (§2.5).
    assert not capability.covered_by([], dirs)
    assert not capability.covered_by(["/home/lumen/agent"], [])
    assert not capability.covered_by(["/home/lumen/agent-old"], ["/home/lumen/agent"])


def test_apply_tags_unions_and_writes_once(monkeypatch):
    calls = []
    monkeypatch.setattr(capability, "_wq", lambda *a: calls.append(a) or "")
    field = capability.apply_tags(99, [("dir", "/home/lumen/agent")],
                                  current_needs="dir(/home/lumen/kb)")
    assert field == "dir(/home/lumen/kb), dir(/home/lumen/agent)"
    assert calls[0] == ("edit", "99", "--needs", field)
    assert calls[1] == ("note", "99", "capability_lint: +dir(/home/lumen/agent)")


def test_apply_tags_is_a_noop_when_the_ticket_already_carries_the_tag(monkeypatch):
    calls = []
    monkeypatch.setattr(capability, "_wq", lambda *a: calls.append(a) or "")
    assert capability.apply_tags(99, [("dir", "/home/lumen/agent")],
                                 current_needs="dir(/home/lumen/agent)") is None
    assert calls == []


def test_apply_tags_writes_nothing_in_dry_run(monkeypatch):
    calls = []
    monkeypatch.setattr(capability, "_wq", lambda *a: calls.append(a) or "")
    assert capability.apply_tags(99, [("dir", "/home/lumen/agent")],
                                 dry_run=True) == "dir(/home/lumen/agent)"
    assert calls == []


def test_apply_tags_refuses_an_unstorable_field(monkeypatch):
    """needs.validate() is re-run before the write so a lint bug fails where the caller
    logs it, not as an opaque non-zero exit from `wq edit`."""
    monkeypatch.setattr(capability, "_wq", lambda *a: pytest.fail("must not write"))
    with pytest.raises(ValueError):
        capability.apply_tags(99, [("dir", "relative/path")])
