"""wq#2184 (DISPATCH_REBUILD_SPEC §1.1, §4 check F, §5 item 2): the second wall honours
the lint's advisory bucket, and the deny-list stays hard at BOTH walls.

Two walls, one question ("would this spawn refuse?"): the dispatcher's stage 3.7
(`capability_replay.replay` runs enforce_capability's exact derive -> preflight
sequence; test_dispatcher.py drives the real stage) and fworker's spawn-time
`assert_spawn_capability`. Every fixture here is put through both and the two must agree.
"""
import datetime
import os
import sys

import pytest

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(HARNESS_DIR)
for p in (HARNESS_DIR, os.path.join(REPO_ROOT, "dispatcher")):
    sys.path.insert(0, p)
import capability  # noqa: E402
import capability_lint as cl  # noqa: E402
import capability_replay  # noqa: E402
import fworker  # noqa: E402
import needs as needs_mod  # noqa: E402

REPO = "/home/lumen/foreman"
ARCHETYPE = "standard-engineering"
ROSTER = cl.Roster(names=["lumen", "isis", "thor"], ips={})
POLICY = cl.load_policy()
SWEPT_ROOT = "/home/lumen/research_prompts"   # an allow.read_dirs root

EXISTING_DIRS = {"/home/lumen", "/home/lumen/minerva", "/home/lumen/agent", SWEPT_ROOT,
                 "/home/lumen/worktrees/wq1-a1"}
EXISTING_FILES = {"/home/lumen/HANDOFF.md", "/home/lumen/.fleet_pw",
                  "/home/lumen/.git-credentials", "/home/lumen/agent/.env",
                  "/home/lumen/.secrets/x.token", "/home/lumen/.netrc",
                  "/home/lumen/.ssh/id_ed25519",
                  "/home/lumen/worktrees/wq1-a1/.env", SWEPT_ROOT + "/notes.md"}

# prose-mention class: what the lint calls advisory, what §4 check F wants dispatched
ADVISORY = {
    "dir_outside_allow": "look at /home/lumen/minerva for the layout",
    "path_missing": "the config lives in /home/lumen/no-such-dir-2184/conf",
    "worktree_mention": "the diff is in /home/lumen/worktrees/wq1-a1/harness/x.py",
    "home_root_file": "read /home/lumen/HANDOFF.md first",
}
# refused by the lint's deny-list / sweep, never advisory
HARD = {
    "secrets_prefix": "read /home/lumen/.secrets/x.token",
    "env_basename": "read /home/lumen/agent/.env",
    "home_fleet_pw": "read /home/lumen/.fleet_pw",
    "home_git_credentials": "read /home/lumen/.git-credentials",
    "home_netrc": "read /home/lumen/.netrc",
    "ssh_prefix": "read /home/lumen/.ssh/id_ed25519",
    "worktree_secret": "cat /home/lumen/worktrees/wq1-a1/.env",
}

SWEEP = {"v": None}


def sweep_doc(hits=None):
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    roots = {r: {"files": 1, "hits": []} for r in POLICY["allow"]["read_dirs"]}
    if hits:
        roots[SWEPT_ROOT]["hits"] = hits
    return {"ts": ts, "roots": roots}


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("FOREMAN_NEEDS_LINT_MODE", "apply-dir")
    monkeypatch.delenv("FOREMAN_CAPABILITY_ADVISORY", raising=False)
    monkeypatch.delenv("FOREMAN_NEEDS_DISABLE", raising=False)
    monkeypatch.setattr(cl, "_isdir", lambda p: p in EXISTING_DIRS)
    monkeypatch.setattr(cl, "_exists", lambda p: p in EXISTING_DIRS or p in EXISTING_FILES)
    monkeypatch.setattr(cl, "load_sweep", lambda *a, **k: SWEEP["v"])
    monkeypatch.setattr(cl, "load_roster", lambda *a, **k: ROSTER)
    SWEEP["v"] = sweep_doc()
    return tmp_path


def ticket(detail, tid=2184):
    return {"id": tid, "title": "", "detail": detail, "needs": None,
            "archetype": ARCHETYPE, "status": "open"}


def dispatcher_wall(t):
    """The column stage 3.7 lands the ticket in."""
    return capability_replay.replay(t, repo=REPO, roster=ROSTER, policy=POLICY)


def fworker_wall(t, tmp_path):
    """None when the spawn-time check passes, else the SpawnRefused code. The ticket
    carries what the dispatcher would have written back onto it: the derived dir tags."""
    derived = cl.derive(t, repo=REPO, add_dirs=cl.base_add_dirs(ARCHETYPE),
                        roster=ROSTER, policy=POLICY)
    needs = capability.effective_needs(t, derived)
    add_dirs = capability.effective_add_dirs(ARCHETYPE, needs)
    try:
        fworker.assert_spawn_capability(t["id"], t, str(tmp_path), REPO, add_dirs, needs)
    except fworker.SpawnRefused as e:
        return e.code
    return None


def both(detail, tmp_path):
    t = ticket(detail)
    return dispatcher_wall(t), fworker_wall(t, tmp_path)


# ---- deny-list: hard at both walls, lever on and off ----------------------------------

@pytest.mark.parametrize("lever", ["on", "off"])
@pytest.mark.parametrize("name", sorted(HARD))
def test_deny_listed_path_is_refused_at_both_walls(name, lever, env, monkeypatch):
    monkeypatch.setenv("FOREMAN_CAPABILITY_ADVISORY", lever)
    col, spawn = both(HARD[name], env)
    assert col["column"] == capability.REASON_CAPABILITY_DENIED_BY_POLICY, col
    assert spawn == "CITED_PATH_UNREACHABLE"


@pytest.mark.parametrize("lever", ["on", "off"])
def test_fresh_sweep_hit_is_refused_at_both_walls(lever, env, monkeypatch):
    monkeypatch.setenv("FOREMAN_CAPABILITY_ADVISORY", lever)
    SWEEP["v"] = sweep_doc(hits=[{"path": SWEPT_ROOT + "/notes.md", "line": 3}])
    col, spawn = both(f"read {SWEPT_ROOT}/notes.md", env)
    assert col["column"] == capability.REASON_CAPABILITY_DENIED_BY_POLICY
    assert "secret-sweep hit" in col["detail"]
    assert spawn == "CITED_PATH_UNREACHABLE"


@pytest.mark.parametrize("lever", ["on", "off"])
@pytest.mark.parametrize("stale", [None, {"ts": "2020-01-01T00:00:00Z", "roots": {}}],
                         ids=["missing", "48h-stale"])
def test_stale_sweep_fails_closed_at_both_walls(stale, lever, env, monkeypatch):
    monkeypatch.setenv("FOREMAN_CAPABILITY_ADVISORY", lever)
    SWEEP["v"] = stale
    col, spawn = both(f"read {SWEPT_ROOT}/notes.md", env)
    assert col["column"] == capability.REASON_NEEDS_UNRESOLVED
    assert spawn == "CITED_PATH_UNREACHABLE"


# ---- advisory: dispatches at both walls, held with the lever off ----------------------

@pytest.mark.parametrize("name", sorted(ADVISORY))
def test_advisory_path_passes_both_walls_with_no_grant(name, env):
    t = ticket(ADVISORY[name])
    col = dispatcher_wall(t)
    assert col["column"] == "dispatchable" and col["advisory"], col
    assert col["tags"] == [] and not col["apply"]      # no add-dir, no tag for the mention
    assert fworker_wall(t, env) is None


@pytest.mark.parametrize("name", sorted(ADVISORY))
def test_advisory_path_is_held_at_both_walls_with_the_lever_off(name, env, monkeypatch):
    monkeypatch.setenv("FOREMAN_CAPABILITY_ADVISORY", "off")
    col, spawn = both(ADVISORY[name], env)
    assert col["column"] in (capability.REASON_CAPABILITY_DENIED_BY_POLICY,
                             capability.REASON_NEEDS_UNRESOLVED), col
    assert spawn == "CITED_PATH_UNREACHABLE"


def test_advisory_beside_a_secret_is_still_refused_whole(env):
    col, spawn = both(ADVISORY["dir_outside_allow"] + " and " + HARD["secrets_prefix"], env)
    assert col["column"] == capability.REASON_CAPABILITY_DENIED_BY_POLICY
    assert spawn == "CITED_PATH_UNREACHABLE"


def test_allowed_dir_is_tagged_not_advisory_and_both_walls_pass(env):
    t = ticket(f"read {SWEPT_ROOT}/notes.md")
    col = dispatcher_wall(t)
    assert col["column"] == "dispatchable" and col["apply"] == [f"dir({SWEPT_ROOT})"]
    assert fworker_wall(t, env) is None


# ---- an explicit request is a real block ----------------------------------------------

@pytest.mark.parametrize("lever", ["on", "off"])
def test_explicit_needs_dir_on_an_unreachable_path_is_refused(lever, env, monkeypatch):
    monkeypatch.setenv("FOREMAN_CAPABILITY_ADVISORY", lever)
    missing = "/home/lumen/no-such-dir-2184"
    t = ticket(f"Needs: dir({missing})\nthe config lives in {missing}/conf")
    col = dispatcher_wall(t)
    assert col["column"] == "cited_path_unreachable" or col["column"] in (
        capability.REASON_NEEDS_UNRESOLVED, capability.REASON_CAPABILITY_DENIED_BY_POLICY)
    if lever == "on":
        assert col["column"] == "cited_path_unreachable"
        assert f"needs:dir({missing}) is not an existing directory" in col["detail"]
    assert fworker_wall(t, env) == "CITED_PATH_UNREACHABLE"


def test_explicit_dir_is_refused_even_when_the_same_path_is_in_the_advisory_set():
    missing = "/home/lumen/no-such-dir-2184"
    v = capability.preflight({"detail": f"see {missing}/conf"}, REPO, [],
                             needs_mod.Needs([("dir", missing)]),
                             advisory_paths=[missing + "/conf", missing])
    assert not v.ok and v.reason == capability.REASON_CITED_PATH_UNREACHABLE
    assert v.unreachable_paths == [missing]


# ---- preflight unit: the lever, node_unknown, and the skip set ------------------------

def test_preflight_skips_only_the_paths_it_is_given():
    detail = "see /home/lumen/a-2184/x and /home/lumen/b-2184/y"
    v = capability.preflight({"detail": detail}, REPO, [], None,
                             advisory_paths=["/home/lumen/a-2184/x"])
    assert not v.ok and v.unreachable_paths == ["/home/lumen/b-2184/y"]


def test_preflight_ignores_a_stale_advisory_set_when_the_lever_is_off(monkeypatch):
    monkeypatch.setenv("FOREMAN_CAPABILITY_ADVISORY", "off")
    v = capability.preflight({"detail": "see /home/lumen/a-2184/x"}, REPO, [], None,
                             advisory_paths=["/home/lumen/a-2184/x"])
    assert not v.ok and v.reason == capability.REASON_CITED_PATH_UNREACHABLE


def test_node_unknown_stays_a_hard_refusal_beside_an_advisory_path():
    v = capability.preflight({"detail": "see /home/lumen/a-2184/x"}, REPO, [],
                             needs_mod.Needs([("ssh", "nonesuch")]),
                             advisory_paths=["/home/lumen/a-2184/x"],
                             state_output="", ssh_hosts=set())
    assert not v.ok and v.reason == capability.REASON_NODE_UNKNOWN


# ---- the shared classifier -------------------------------------------------------------

def test_both_walls_read_one_classification(env):
    """derive() and the spawn-time helper report the same advisory paths."""
    detail = " ; ".join(ADVISORY.values())
    t = ticket(detail)
    derived = cl.derive(t, repo=REPO, add_dirs=cl.base_add_dirs(ARCHETYPE),
                        roster=ROSTER, policy=POLICY)
    assert derived.advisory_paths == cl.advisory_paths_for(
        t, REPO, cl.base_add_dirs(ARCHETYPE), policy=POLICY)
    assert "/home/lumen/HANDOFF.md" in derived.advisory_paths
    assert "/home/lumen/minerva" in derived.advisory_paths


def test_advisory_paths_for_is_empty_when_the_lever_or_the_lint_is_off(monkeypatch):
    t = ticket(ADVISORY["path_missing"])
    assert cl.advisory_paths_for(t, REPO, [], policy=POLICY)
    monkeypatch.setenv("FOREMAN_CAPABILITY_ADVISORY", "off")
    assert cl.advisory_paths_for(t, REPO, [], policy=POLICY) == []
    monkeypatch.delenv("FOREMAN_CAPABILITY_ADVISORY")
    monkeypatch.setenv("FOREMAN_NEEDS_LINT_MODE", "warn")
    assert cl.advisory_paths_for(t, REPO, [], policy=POLICY) == []


def test_advisory_paths_for_fails_closed_on_any_error(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("sweep exploded")
    monkeypatch.setattr(cl, "_derive_dirs", boom)
    assert cl.advisory_paths_for(ticket(ADVISORY["path_missing"]), REPO, []) == []


def test_needs_disable_still_downgrades_a_refusal_to_log_only(monkeypatch):
    monkeypatch.setenv("FOREMAN_NEEDS_DISABLE", "1")
    v = capability.preflight({"detail": "see /home/lumen/a-2184/x"}, REPO, [], None)
    assert v.ok and v.downgraded
