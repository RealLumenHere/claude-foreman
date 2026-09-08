"""wq#1441 (research/BROKER_GRANT_COVERAGE.md §2): explicit ALLOW in capability_lint.

Deny is the default. A `dir`/`ssh`/`restart` tag is emitted only for a target the
policy's `allow:` section names; `never` is checked first and always wins (refused,
never "not allowed"); a target that is neither lands in `unresolved` with the
propose-don't-deploy hint. Fixtures only — no roster, no disk, no policy file.
"""
import datetime
import os
import sys

import pytest

HARNESS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HARNESS)
import capability_lint as cl  # noqa: E402


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def fresh_sweep(roots=None):
    return {"ts": _iso(datetime.datetime.now(datetime.timezone.utc)), "roots": roots or {}}


def stale_sweep(roots=None, age_h=49):
    old = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=age_h)
    return {"ts": _iso(old), "roots": roots or {}}

ROSTER = cl.Roster(names=["lumen", "isis", "thor", "anubis", "maat"],
                   ips={"100.64.0.5": "isis"})
POLICY = {
    "never": {"nodes": ["thor"],
              "restart": {"lumen": ["vaultwarden*", "cloudflared", "tailscaled"],
                          "maat": ["*"]}},
    "copy_dirs": {},
    "allow": {"read_dirs": ["/home/lumen/research_prompts", "/home/lumen/instructions"],
              "restart": {"isis": ["ollama"], "lumen": ["lumen-messenger"]},
              "ssh": ["isis"]},
}
DIRS = {"/home/lumen/research_prompts", "/home/lumen/instructions",
        "/home/lumen/instructions/playbooks", "/home/lumen/agent",
        "/home/lumen/sentinel/config", "/home/lumen/minerva", "/home/lumen/.secrets"}
FILES = {"/home/lumen/agent/AGENTS.md", "/home/lumen/research_prompts/x.md",
         "/home/lumen/.secrets/x.token"}


@pytest.fixture(autouse=True)
def fs(monkeypatch):
    monkeypatch.setattr(cl, "_isdir", lambda p: p in DIRS)
    monkeypatch.setattr(cl, "_exists", lambda p: p in DIRS or p in FILES)
    monkeypatch.setattr(cl, "load_sweep", fresh_sweep)


def derive(detail, policy=POLICY, sweep=None):
    return cl.derive({"title": "", "detail": detail}, roster=ROSTER, policy=policy,
                     sweep=sweep)


# --- dir --------------------------------------------------------------------------

def test_dir_under_allow_root_is_tagged():
    d = derive("read /home/lumen/instructions/playbooks then report")
    assert d.tag_strings == ["dir(/home/lumen/instructions/playbooks)"]
    assert d.unresolved == [] and d.refused == []


def test_dir_outside_allow_is_unresolved_with_the_hint():
    """wq#1451's live drill dir. Swept 2026-09-08: agent/.env + 20 .env.bak carry live
    tokens, so it is NOT on the allow list and must not auto-grant any more."""
    d = derive("read /home/lumen/agent/AGENTS.md line 1")
    assert d.tag_strings == []
    assert d.unresolved == ["dir(/home/lumen/agent) is not under allow.read_dirs — "
                            + cl.ALLOW_HINT]


# --- read_dir_sweep gate (wq#1441 §2.3, wq#1482 slice 5) --------------------------
# A dir(X) tag proves X clean at PR time only; a secret written later under an allowed
# root (the wq#1466 class) must be caught on the next sweep, not silently re-granted.

def test_dir_fresh_hit_is_refused_with_file_line():
    sweep = fresh_sweep({"/home/lumen/instructions":
                         {"files": 1, "hits": [{"path": "/home/lumen/instructions/playbooks/x.md",
                                                "line": 12, "kind": "gh_pat", "excerpt": "ghp_…"}]}})
    d = derive("read /home/lumen/instructions/playbooks then report", sweep=sweep)
    assert d.tag_strings == []
    assert d.refused == [
        "dir(/home/lumen/instructions/playbooks) fresh sweep hit at "
        "/home/lumen/instructions/playbooks/x.md:12 [gh_pat] — " + cl.ALLOW_HINT]


def test_dir_hit_outside_the_cited_subdir_still_tags():
    """A hit elsewhere under the allow root, but not under the cited dir itself, does
    not block this narrower dir(X)."""
    sweep = fresh_sweep({"/home/lumen/instructions":
                         {"files": 1, "hits": [{"path": "/home/lumen/instructions/other/x.md",
                                                "line": 1, "kind": "gh_pat", "excerpt": "ghp_…"}]}})
    d = derive("read /home/lumen/instructions/playbooks then report", sweep=sweep)
    assert d.tag_strings == ["dir(/home/lumen/instructions/playbooks)"]
    assert d.refused == []


def test_dir_stale_sweep_is_unresolved_fail_closed():
    sweep = stale_sweep()
    d = derive("read /home/lumen/instructions/playbooks then report", sweep=sweep)
    assert d.tag_strings == [] and d.refused == []
    assert d.unresolved == [
        "dir(/home/lumen/instructions/playbooks) read_dir_sweep is missing or >48h "
        "stale — fail closed (BROKER_GRANT_COVERAGE.md slice 5); run "
        "read_dir_sweep.py --policy --record"]


def test_dir_missing_sweep_is_unresolved_fail_closed():
    d = derive("read /home/lumen/instructions/playbooks then report", sweep={})
    assert d.tag_strings == [] and d.refused == []
    assert "read_dir_sweep is missing or >48h stale" in d.unresolved[0]


def test_sweep_stale_boundary():
    assert cl.sweep_stale(None) is True
    assert cl.sweep_stale({}) is True
    assert cl.sweep_stale({"ts": "not-a-timestamp"}) is True
    assert cl.sweep_stale(fresh_sweep()) is False
    assert cl.sweep_stale(stale_sweep(age_h=48.5)) is True
    assert cl.sweep_stale(stale_sweep(age_h=1)) is False


def test_sweep_hit_under_scopes_to_the_named_root():
    sweep = fresh_sweep({
        "/home/lumen/instructions": {"files": 1, "hits": [
            {"path": "/home/lumen/instructions/x.md", "line": 3, "kind": "aws", "excerpt": "AKIA…"}]},
        "/home/lumen/research_prompts": {"files": 0, "hits": []},
    })
    assert cl.sweep_hit_under("/home/lumen/instructions", sweep,
                             "/home/lumen/instructions")["line"] == 3
    assert cl.sweep_hit_under("/home/lumen/instructions", sweep,
                             "/home/lumen/research_prompts") is None


def test_deny_list_still_beats_allow():
    """A deny-listed path under an allow root is refused, not granted."""
    pol = dict(POLICY, allow={"read_dirs": ["/home/lumen"]})
    d = derive("cat /home/lumen/.secrets/x.token", policy=pol)
    assert d.refused and d.tag_strings == []


def test_missing_allow_section_allows_nothing():
    """An edited-away `allow:` must not restore the pre-wq#1441 open default."""
    pol = {k: v for k, v in POLICY.items() if k != "allow"}
    d = derive("read /home/lumen/research_prompts/x.md", policy=pol)
    assert d.tag_strings == [] and "allow.read_dirs" in d.unresolved[0]


# --- restart ----------------------------------------------------------------------

@pytest.mark.parametrize("detail,tags,refused,unresolved", [
    ("Needs: restart(ollama@isis)", ["restart(ollama@isis)"], [], []),
    ("restart ollama on isis", ["restart(ollama@isis)"], [], []),
    ("systemctl restart lumen-messenger", ["restart(lumen-messenger@lumen)"], [], []),
    # never beats allow, and beats the unit-shape rule: still a deny row (test iii)
    ("restart tailscaled on thor", [], ["restart(tailscaled@thor) policy:never.nodes (thor)"], []),
    ("Needs: restart(postgresql@maat)", [], ["restart(postgresql@maat) policy:never.restart[maat] (*)"], []),
    ("systemctl restart cloudflared", [], ["restart(cloudflared@lumen) policy:never.restart[lumen] (cloudflared)"], []),
    # explicit tag form, not allowed -> honest skip with the hint
    ("Needs: restart(gitea@anubis)", [], [], ["restart(gitea@anubis) is not on allow.restart[anubis]"]),
    # prose shape, unit-shaped token, not allowed -> unresolved
    ("restart lumen-agent.service on lumen", [], [], ["restart(lumen-agent@lumen) is not on allow.restart[lumen]"]),
    # prose shape, English word (wq#1440's real detail) -> nothing at all
    ("live fact: isis systemctl restart without sudo is a polkit refusal", [], [], []),
    ("after that, restart it on isis and verify", [], [], []),
])
def test_restart_allow_matrix(detail, tags, refused, unresolved):
    d = derive(detail)
    assert d.tag_strings == tags
    assert d.refused == refused
    got = [u.split(" — ")[0] for u in d.unresolved if u.startswith("restart(")]
    assert got == unresolved, d.unresolved


def test_unit_shaped():
    assert cl.unit_shaped("lumen-messenger") and cl.unit_shaped("ollama.service")
    assert cl.unit_shaped("getty@tty1") and cl.unit_shaped("a.b-c@d")
    for word in ("without", "on", "it", "ollama", ""):
        assert not cl.unit_shaped(word), word


# --- ssh --------------------------------------------------------------------------

def test_explicit_ssh_not_allowed_is_unresolved():
    d = derive("ssh anubis and read the borg log")
    assert d.tag_strings == []
    assert d.unresolved == ["ssh(anubis) is not on allow.ssh — " + cl.ALLOW_HINT]


def test_prose_ssh_not_allowed_emits_nothing():
    d = derive("check the repo on anubis; then read the log on maat")
    assert d.tag_strings == [] and d.unresolved == []


def test_ssh_allowed_tags():
    d = derive("ssh isis; probe the unit on isis; 100.64.0.5")
    assert d.tag_strings == ["ssh(isis)"]


# --- helpers ----------------------------------------------------------------------

def test_allowed_read_dir_returns_the_root():
    assert cl.allowed_read_dir("/home/lumen/instructions/playbooks", POLICY) == "/home/lumen/instructions"
    assert cl.allowed_read_dir("/home/lumen/instructionsX", POLICY) is None
    assert cl.allowed_read_dir("/home/lumen/agent", {}) is None


# --- the live policy file's shape (wq#1441 §2) --------------------------------------

def test_live_policy_allow_section_is_consistent():
    import yaml
    pol = yaml.safe_load(open(os.path.join(HARNESS, "fleet-exec-policy.yaml")))
    allow = pol["allow"]
    assert allow["read_dirs"] and allow["restart"] and allow["ssh"]
    for root in allow["read_dirs"]:
        assert cl.dir_denied(root) is None, root
        assert root.startswith("/home/lumen/") and root != "/home/lumen"
    for node, globs in allow["restart"].items():
        assert node not in pol["never"]["nodes"], node
        for g in globs:
            assert cl.restart_refusal(node, g, pol) is None, (node, g)
    assert "thor" not in allow["ssh"]
