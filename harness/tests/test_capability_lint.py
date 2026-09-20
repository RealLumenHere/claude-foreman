"""capability_lint (wq#1437, BROKER_WIRING.md §2.1 slice B) — table-driven over the
REAL corpus.

The ticket rows below are verbatim excerpts from the live queue.db (open+blocked scan,
2026-09-06), the same 111-ticket corpus the design doc tuned against: wq#1133 and
wq#1008 cite secrets, wq#1358 cites a dead worktree, wq#1441 asks for a restart on
Thor — all four must land in `refused`, never in `tags`. wq#1401/#1402/#1403/#1407 are
the doc's §4 row-iv replay set and must land in `tags` as plain read dirs.

Zero live services: the node roster and the broker policy are fixtures, and the
filesystem questions go through capability_lint._isdir/_exists so a row's expected
bucket is pinned to this file, not to the host's disk.
"""
import os
import sys

import pytest

HARNESS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HARNESS)
import capability_lint as cl  # noqa: E402
import needs as needs_mod  # noqa: E402

# --- fixtures -------------------------------------------------------------------

ROSTER = cl.Roster(
    names=["lumen", "isis", "thor", "thoth", "bes", "anubis", "maat", "sobek"],
    # fixture ip -> subject map, not fleet facts: the shapes are what is under test
    ips={"100.64.0.5": "isis", "192.168.1.42": "bes"},
)

POLICY = {
    "never": {
        "nodes": ["thor"],
        "restart": {"lumen": ["vaultwarden*", "cloudflared", "tailscaled", "sshd"],
                    "maat": ["*"]},
    },
    "copy_dirs": {"isis": ["/home/isis/lumen-drop"], "thor": ["/home/thor/lumen-drop"]},
    # wq#1441: deny is the default; these are the fixture's explicit grants. Rows
    # below that expect a tag are under one of them; rows that expect `unresolved`
    # with the allow hint are deliberately not.
    "allow": {
        "read_dirs": ["/home/lumen/instructions", "/home/lumen/agent",
                      "/home/lumen/foreman", "/home/lumen/bin",
                      "/home/lumen/backups/wq-proposals", "/home/lumen/.npm-global"],
        "restart": {"isis": ["ollama", "ollama.service"], "lumen": ["lumen-messenger"]},
        "ssh": ["isis", "bes", "lumen", "anubis"],
    },
}

# Paths the corpus rows cite. Everything else "does not exist" for these tests.
EXISTING_DIRS = {
    "/home/lumen/instructions/playbooks", "/home/lumen/agent",
    "/home/lumen/backups/wq-proposals", "/home/lumen/.npm-global/bin",
    "/home/lumen/bin", "/home/lumen/mailbot", "/home/lumen/foreman",
    "/home/lumen/foreman/harness", "/home/lumen/secrets", "/home/lumen/.secrets",
    "/home/lumen/worktrees", "/home/lumen/worktrees/wq1357-a1", "/home/lumen",
    "/home/lumen/instructions/approvals",   # wq#1535 closure-artifact parent
}
EXISTING_FILES = {
    "/home/lumen/.secrets/github-frank.token", "/home/lumen/secrets/reddit.env",
    "/home/lumen/instructions/playbooks/maintenance-window.md",
    "/home/lumen/bin/fleet-exec", "/home/lumen/bin/gitea-rotate-finish",
    "/home/lumen/foreman/harness/fworker.py",
}


@pytest.fixture(autouse=True)
def fs(monkeypatch):
    monkeypatch.setattr(cl, "_isdir", lambda p: p in EXISTING_DIRS)
    monkeypatch.setattr(cl, "_exists",
                        lambda p: p in EXISTING_DIRS or p in EXISTING_FILES)
    monkeypatch.delenv("FOREMAN_NEEDS_LINT_MODE", raising=False)


def fresh_sweep():
    """wq#1482: a fresh, hitless read_dir_sweep.json for every allow.read_dirs root
    — this file's corpus is about the allow-list gate, not the sweep gate, so every
    row here should behave as if the sweep just ran clean."""
    import datetime
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    roots = {r: {"files": 0, "hits": []} for r in POLICY["allow"]["read_dirs"]}
    return {"ts": ts, "roots": roots}


def derive(detail, title="", repo=None, add_dirs=()):
    return cl.derive({"title": title, "detail": detail}, repo=repo, add_dirs=add_dirs,
                     roster=ROSTER, policy=POLICY, sweep=fresh_sweep())


# --- the real corpus ------------------------------------------------------------

# (ticket, detail excerpt verbatim from queue.db, expected tags, expected refused
#  substrings, expected unresolved substrings)
CORPUS = [
    (
        "wq#1133",
        "REMAINING (this ticket): make the frankstupak PAT rotation procedure write "
        "the CANONICAL /home/lumen/.secrets/github-frank.token (mode 600, no trailing "
        "newline) and keep agent/.env GITHUB_TOKEN in lockstep. Deliver a "
        "github-rotate-finish helper modeled on /home/lumen/bin/gitea-rotate-finish.",
        ["dir(/home/lumen/bin)"],
        ["dir(/home/lumen/.secrets/github-frank.token) deny:prefix (/home/lumen/.secrets)"],
        [],
    ),
    (
        "wq#1008",
        "If it succeeds, drop client_id/secret into /home/lumen/secrets/reddit.env "
        "(0600) and ping — a fleet-search reddit-api path can then land.",
        [],
        ["dir(/home/lumen/secrets/reddit.env) deny:prefix (/home/lumen/secrets)"],
        [],
    ),
    (
        "wq#1358",
        "Re-run 'lumen-pr land /home/lumen/worktrees/wq1357-a1 5 --merge' (or from any "
        "mailbot worktree) once wq#1338 clears.",
        [],
        [],   # wq#2131: another ticket's worktree is a mention -> advisory, see below
        [],
    ),
    (
        "wq#1401",
        "POLICY (Frank 2026-09-03; doc /home/lumen/instructions/playbooks/"
        "maintenance-window.md; state fleet.maintenance_window): dedupe disruptive ops.",
        ["dir(/home/lumen/instructions/playbooks)"],
        [],
        [],
    ),
    (
        "wq#1407",
        "preflight.py:247 spawns bare 'claude'; lumen-foreman-dispatch.service PATH "
        "lacks /home/lumen/.npm-global/bin so every wq#1372 auto-reprove fails "
        "FileNotFoundError.",
        ["dir(/home/lumen/.npm-global/bin)"],
        [],
        [],
    ),
    (
        "wq#1441",
        "FOREMAN_NEEDS_LINT_MODE=apply for ssh/restart/copy tags. Run row iii (three "
        "never-list tickets: restart(tailscaled@thor), Lumen MAC spoof text, rm -rf on "
        "isis; each pre-skipped).",
        [],
        ["restart(tailscaled@thor) policy:never.nodes (thor)"],
        [],
    ),
    (
        "wq#1350",
        "[ops] sync /home/lumen/foreman primary tree, confirm systemctl restart "
        "lumen-messenger actually works (wq#1347 follow-up)",
        ["dir(/home/lumen/foreman)", "restart(lumen-messenger@lumen)"],
        [],
        [],
    ),
    (
        "wq#665",
        "reversible via reinstall) and meanwhile silence it reversibly (adb shell cmd "
        "notification / appops on its foreground channel).",
        [],
        [],
        [],   # wq#2131: 'adb' mid-sentence is a prose mention -> advisory, see below
    ),
]


@pytest.mark.parametrize("tid,detail,tags,refused,unresolved",
                         CORPUS, ids=[c[0] for c in CORPUS])
def test_real_corpus_rows(tid, detail, tags, refused, unresolved):
    d = derive(detail)
    assert d.tag_strings == tags
    assert d.refused == refused
    for want in unresolved:
        assert any(want in u for u in d.unresolved), d.unresolved
    if not unresolved:
        assert d.unresolved == []


def test_secret_citation_never_becomes_a_tag():
    """§0: 'any lint that derives needs:dir from cited paths without a deny-list hands
    a worker Frank's tokens on its first pass'. The negative, asserted directly."""
    for detail, _ in [(c[1], c[0]) for c in CORPUS[:2]]:
        d = derive(detail)
        assert not any("secret" in t or "worktrees" in t for t in d.tag_strings)
        assert d.refused


# --- deny-list ------------------------------------------------------------------

DENY_ROWS = [
    "/home/lumen/.secrets/github-frank.token",
    "/home/lumen/secrets/reddit.env",
    "/home/lumen/.ssh/config",
    "/home/lumen/.claude/settings.json",
    "/home/lumen/.config/gh/hosts.yml",
    "/home/lumen/worktrees/wq1357-a1",
    "/home/lumen/scratch/worktrees/x",
    "/home/lumen/agents/work-queue/queue.db",
    "/home/lumen/agents/work-queue/queue.db-wal",
    "/home/lumen/projects/nest/app.env",
    "/home/lumen/kb/api.token",
    "/home/lumen/certs/site.key",
    "/home/lumen/certs/site.pem",
    "/home/lumen/.ssh/id_ed25519",
    "/home/lumen",
    "/",
]


@pytest.mark.parametrize("path", DENY_ROWS)
def test_dir_deny_list(path):
    assert cl.dir_denied(path), path


@pytest.mark.parametrize("path", ["/home/lumen/bin", "/home/lumen/mailbot",
                                  "/home/lumen/foreman/harness",
                                  "/home/lumen/.npm-global/bin"])
def test_dir_deny_list_lets_ordinary_dirs_through(path):
    assert cl.dir_denied(path) is None


def test_denied_before_existence_check():
    """A cited secret that does NOT exist is still refused, never `unresolved` — the
    deny-list runs on the path as cited (doc §4 row iv: /home/lumen/.secrets/x.token)."""
    d = derive("read /home/lumen/.secrets/x.token for the probe")
    assert d.refused and not d.tags and not d.unresolved


def test_file_in_denied_parent_dir_is_refused():
    d = derive("the token lives at /home/lumen/creds.token")
    assert d.refused == ["dir(/home/lumen/creds.token) deny:basename (*.token)"]


# --- dir normalisation ----------------------------------------------------------

def test_cited_file_becomes_its_parent_dir():
    d = derive("see /home/lumen/instructions/playbooks/maintenance-window.md")
    assert d.tag_strings == ["dir(/home/lumen/instructions/playbooks)"]


def test_missing_path_is_advisory_never_guessed():
    """§2.1: 'a non-existent path is not guessed' — no walking up to an existing
    ancestor, which would widen the grant to a directory nobody cited. wq#2131: it is
    advisory now, not a hold."""
    d = derive("write /home/lumen/mailbot/does/not/exist/yet.md")
    assert d.tags == [] and d.unresolved == [] and d.refused == []
    assert d.advisory == [
        "cited path /home/lumen/mailbot/does/not/exist/yet.md does not exist (not guessed)"]


# --- wq#1535: closure artifacts -------------------------------------------------

def test_closure_artifact_resolves_to_its_existing_parent():
    """The factory's yellow-approval touch file does not exist until the work touches
    it; the reach that work needs is the approvals dir, which does exist and is under
    an allow root -> a plain dir tag, no `does not exist` shrug."""
    d = derive("touch /home/lumen/instructions/approvals/F55b260980679.approved then apply")
    assert d.tag_strings == ["dir(/home/lumen/instructions/approvals)"]
    assert d.unresolved == [] and d.refused == []


def test_closure_artifact_with_missing_parent_is_still_not_guessed(monkeypatch):
    monkeypatch.setattr(cl, "CLOSURE_ARTIFACT_GLOBS",
                        ("/home/lumen/instructions/approvals/*.approved",
                         "/home/lumen/instructions/nowhere/*.approved"))
    d = derive("touch /home/lumen/instructions/nowhere/F1.approved")
    assert d.tags == [] and d.unresolved == []
    assert d.advisory == ["closure artifact /home/lumen/instructions/nowhere/F1.approved: "
                          "parent directory does not exist (not guessed)"]


def test_closure_artifact_glob_is_narrow():
    """Only the exact shape is a closure artifact; a sibling file that does not exist
    is still the ordinary not-guessed case (the doctrine is unchanged for everything
    else)."""
    d = derive("read /home/lumen/instructions/approvals/F55b260980679.md")
    assert d.tags == [] and d.unresolved == []
    assert d.advisory == ["cited path /home/lumen/instructions/approvals/F55b260980679.md "
                          "does not exist (not guessed)"]


def test_closure_artifact_under_deny_prefix_is_refused_first(monkeypatch):
    """Deny still wins before the closure rule sees the path."""
    monkeypatch.setattr(cl, "CLOSURE_ARTIFACT_GLOBS", ("/home/lumen/.secrets/*.approved",))
    d = derive("touch /home/lumen/.secrets/F1.approved")
    assert d.tags == [] and d.unresolved == []
    assert d.refused == ["dir(/home/lumen/.secrets/F1.approved) deny:prefix (/home/lumen/.secrets)"]


def test_closure_artifact_shipped_glob_matches_the_factory_convention():
    """minerva_factory.approval_path(plan_id) = APPROVALS_DIR/F<plan_id>.approved — the
    shipped glob must match that exact shape or the promoter's tickets stall again."""
    assert cl.closure_artifact("/home/lumen/instructions/approvals/F0cab64387782.approved")
    assert not cl.closure_artifact("/home/lumen/instructions/approvals/F0cab64387782.md")
    assert not cl.closure_artifact("/home/lumen/instructions/approvals/sub/F1.approved")


# --- wq#1697: a ticket's own worktree citation ----------------------------------

def test_own_worktree_citation_is_neither_tagged_nor_refused():
    """wq#1697 fix 1: the retry-notes mechanism (fworker.write_prior_attempt_notes)
    writes this ticket's own prior worktree path into its own detail. That path is
    not a request for new reach — the next attempt gets exactly it as its worktree
    regardless of any tag — so it must land in none of the three buckets."""
    d = cl.derive(
        {"id": 1672, "title": "t",
         "detail": "## git diff --stat (prior worktree)\n"
                   "see /home/lumen/worktrees/wq1672-a3/foo.py for what attempt 3 left"},
        roster=ROSTER, policy=POLICY, sweep=fresh_sweep())
    assert d.tags == [] and d.refused == [] and d.unresolved == []


def test_other_tickets_worktree_citation_is_never_tagged():
    """The exemption is keyed to THIS ticket's id — citing a DIFFERENT ticket's
    worktree (wq#1358's real corpus row above) is never granted; own-worktree reach is
    not a door into anyone else's tree. wq#2131: it is a mention, so advisory rather
    than a hold."""
    d = cl.derive(
        {"id": 1358, "title": "t",
         "detail": "Re-run 'lumen-pr land /home/lumen/worktrees/wq1357-a1 5 --merge'"},
        roster=ROSTER, policy=POLICY, sweep=fresh_sweep())
    assert d.tags == [] and d.refused == []
    assert d.advisory == ["dir(/home/lumen/worktrees/wq1357-a1) deny:prefix (/home/lumen/worktrees)"]


def test_own_worktree_citation_with_no_ticket_id_is_advisory_not_exempt():
    """A caller that derives without an id (e.g. an ad-hoc string, not a real queue
    row) gets no own-worktree exemption — the exemption never fires on guesswork."""
    d = cl.derive({"title": "t", "detail": "see /home/lumen/worktrees/wq1672-a3/foo.py"},
                  roster=ROSTER, policy=POLICY, sweep=fresh_sweep())
    assert d.refused == []
    assert d.advisory == ["dir(/home/lumen/worktrees/wq1672-a3/foo.py) deny:prefix (/home/lumen/worktrees)"]


def test_paths_already_reachable_need_no_tag():
    d = derive("edit /home/lumen/foreman/harness/fworker.py and /home/lumen/bin/fleet-exec",
               repo="/home/lumen/foreman", add_dirs=["/home/lumen/bin"])
    assert d.tag_strings == []


def test_deepest_existing_dir_not_the_repo_root():
    d = derive("touch /home/lumen/foreman/harness/fworker.py")
    assert d.tag_strings == ["dir(/home/lumen/foreman/harness)"]


# --- ssh ------------------------------------------------------------------------

SSH_ROWS = [
    ("ssh isis and read the journal", ["ssh(isis)"], []),
    ("run `ssh -o BatchMode=yes isis` first", ["ssh(isis)"], []),
    ("ssh lumen@100.64.0.5 to check", ["ssh(isis)"], []),
    ("probe the unit on isis before touching it", ["ssh(isis)"], []),
    ("the host at 192.168.1.42 is the one", ["ssh(bes)"], []),
    # prose, not a node mention (wq#879/#325 tuning): no tag AND no unresolved
    ("bare ssh itself also isn't in this allowlist", [], []),
    # "ssh to bes" is prose about a past run, not an `ssh <node>` invocation: the
    # target token is "to", so nothing is derived (wq#879's real text)
    ("that worker ran ssh to bes repeatedly with no grant", [], []),
    ("ssh outcome recorded as the UID-split baseline", [], []),
    # host-shaped but unknown: reported, never guessed
    ("ssh isis-2 for the second box", [], ["node 'isis-2' is not a lumen_state subject"]),
    ("ssh 100.99.99.99 for the probe", [], ["no single lumen_state subject for that ip"]),
]


@pytest.mark.parametrize("detail,tags,unresolved", SSH_ROWS)
def test_ssh_shapes(detail, tags, unresolved):
    d = derive(detail)
    assert d.tag_strings == tags
    assert len(d.unresolved) == len(unresolved)
    for want in unresolved:
        assert any(want in u for u in d.unresolved), d.unresolved


# --- restart --------------------------------------------------------------------

RESTART_ROWS = [
    ("Needs: restart(ollama@isis)", ["restart(ollama@isis)"], [], []),
    ("systemctl restart lumen-messenger", ["restart(lumen-messenger@lumen)"], [], []),
    ("restart ollama.service on isis", ["restart(ollama@isis)"], [], []),
    ("systemctl restart ollama on isis", ["restart(ollama@isis)"], [], []),
    # never-list rows (doc §4 row iii): refused, not tagged
    ("restart(tailscaled@thor)", [], ["restart(tailscaled@thor) policy:never.nodes (thor)"], []),
    ("systemctl restart vaultwarden-lumen", [],
     ["restart(vaultwarden-lumen@lumen) policy:never.restart[lumen] (vaultwarden*)"], []),
    ("restart postgres on maat", [], ["restart(postgres@maat) policy:never.restart[maat] (*)"], []),
    # unknown node: unresolved, never defaulted to lumen
    ("restart ollama on nowhere", [], [], ["node 'nowhere' is not a lumen_state subject"]),
    # prose without one of the three explicit shapes: nothing at all
    ("we should probably restart the messenger at some point", [], [], []),
]


@pytest.mark.parametrize("detail,tags,refused,unresolved", RESTART_ROWS)
def test_restart_shapes(detail, tags, refused, unresolved):
    d = derive(detail)
    assert d.tag_strings == tags
    assert d.refused == refused
    for want in unresolved:
        assert any(want in u for u in d.unresolved), d.unresolved


def test_restart_refusal_uses_the_shipped_policy_file():
    """The fixture policy above must not drift from harness/fleet-exec-policy.yaml —
    the two never-rows the doc names are checked against the real file."""
    policy = cl.load_policy()
    assert policy, "fleet-exec-policy.yaml must be readable"
    assert cl.restart_refusal("thor", "tailscaled", policy)
    assert cl.restart_refusal("lumen", "vaultwarden-lumen", policy)
    assert cl.restart_refusal("isis", "ollama", policy) is None


# --- copy -----------------------------------------------------------------------

COPY_ROWS = [
    ("scp report.md isis:/home/isis/lumen-drop", ["copy(isis:/home/isis/lumen-drop)"], []),
    ("rsync -a out/ isis:/home/isis/lumen-drop", ["copy(isis:/home/isis/lumen-drop)"], []),
    ("scp report.md isis:/etc", [], ["not a copy_dirs entry for isis"]),
    ("scp report.md nowhere:/home/nowhere/lumen-drop", [],
     ["node is not a lumen_state subject"]),
]


@pytest.mark.parametrize("detail,tags,unresolved", COPY_ROWS)
def test_copy_shapes(detail, tags, unresolved):
    d = derive(detail)
    assert d.tag_strings == tags
    for want in unresolved:
        assert any(want in u for u in d.unresolved), d.unresolved


# --- v2 verbs -------------------------------------------------------------------

@pytest.mark.parametrize("verb,detail", [
    ("sudo", "run sudo systemctl daemon-reload there"),
    ("adb", "adb shell cmd notification"),
    ("fastboot", "fastboot flashing unlock the bench device"),
    ("reboot", "reboot the node after the change"),
    ("poweroff", "poweroff the spare box"),
    ("docker restart", "docker restart the container"),
])
def test_v2_verbs_are_unresolved_never_tagged(verb, detail):
    d = derive(detail)
    assert d.tags == []
    assert any(verb in u and "v2 verb" in u for u in d.unresolved), d.unresolved


@pytest.mark.parametrize("detail", [
    "convert /home/lumen/bin/fleet-roll-reboot.sh to read fleet-inv ssh-list",
    "the power-cycle_log.txt shows the plug bounced",
    "install lands via /usr/bin/sudo only when the orchestrator runs it",
    "the adb-tools dir is on thor",
])
def test_v2_verb_inside_a_name_is_not_unresolved(detail):
    d = cl.derive({"title": "t", "detail": detail}, repo="/home/lumen/foreman",
                    add_dirs=("/home/lumen/bin",), roster=ROSTER, policy={})
    assert not any("v2 verb" in u for u in d.unresolved), d.unresolved


# --- contract -------------------------------------------------------------------

def test_derive_is_deterministic():
    detail = CORPUS[0][1] + " " + CORPUS[6][1] + " ssh isis"
    first, second = derive(detail), derive(detail)
    assert (first.tag_strings, first.unresolved, first.refused) == \
           (second.tag_strings, second.unresolved, second.refused)


def test_tags_are_valid_needs_column_values():
    """What slice C will hand `wq edit --needs` must pass needs.validate() and parse
    back to the same tags — one vocabulary, no second definition (needs.py:98)."""
    d = derive("edit /home/lumen/mailbot/x.py; ssh isis; systemctl restart ollama on "
               "isis; scp out isis:/home/isis/lumen-drop")
    field = d.needs_field()
    ok, err = needs_mod.validate(field)
    assert ok, err
    assert needs_mod.parse(needs_field=field).tags == d.tags


def test_buckets_are_deduped_and_never_merged():
    d = derive("ssh isis; ssh isis again; /home/lumen/bin and /home/lumen/bin again; "
               "restart(tailscaled@thor); restart(tailscaled@thor)")
    assert d.tag_strings == ["dir(/home/lumen/bin)", "ssh(isis)"]
    assert d.refused == ["restart(tailscaled@thor) policy:never.nodes (thor)"]
    assert not set(d.tag_strings) & set(d.refused)


def test_empty_ticket_derives_nothing():
    d = cl.derive({"title": "", "detail": None}, roster=ROSTER, policy=POLICY)
    assert not d and d.as_event() == {"tags": [], "unresolved": [], "refused": [],
                                      "advisory": []}


def test_mode_defaults_to_apply_and_off_disables(monkeypatch):
    """wq#1438 flipped the default from slice B's `warn` to `apply-dir`: the pre-skip
    and the dir auto-tag are the slice, so shipping them inert would ship nothing."""
    monkeypatch.delenv("FOREMAN_NEEDS_LINT_MODE", raising=False)
    assert cl.mode() == "apply" and cl.enabled() and cl.enforcing()
    monkeypatch.setenv("FOREMAN_NEEDS_LINT_MODE", "off")
    assert not cl.enabled() and not cl.enforcing()
    monkeypatch.setenv("FOREMAN_NEEDS_LINT_MODE", "apply")
    assert cl.mode() == "apply" and cl.enabled() and cl.enforcing()


def test_warn_is_the_documented_rollback(monkeypatch):
    """The slice's rollback (`FOREMAN_NEEDS_LINT_MODE=warn`) must restore slice B
    exactly: still derives, still logs, decides and applies nothing."""
    monkeypatch.setenv("FOREMAN_NEEDS_LINT_MODE", "warn")
    assert cl.mode() == "warn" and cl.enabled() and not cl.enforcing()
    d = cl.derive({"title": "read /home/lumen/agent"}, roster=ROSTER, policy=POLICY,
                  sweep=fresh_sweep())
    assert d.tags and cl.appliable_tags(d) == []


def test_unknown_mode_degrades_to_warn_never_to_enforcing(monkeypatch):
    """A typo in a systemd drop-in must not be able to make the whole queue
    undispatchable — fail toward measurement, not toward the gate."""
    monkeypatch.setenv("FOREMAN_NEEDS_LINT_MODE", "apply_dir")  # underscore, not dash
    assert cl.mode() == "warn" and not cl.enforcing()


def test_apply_dir_applies_only_dir_tags(monkeypatch):
    """Slice C's scope: `dir` is read-only and deny-listed, so auto-applying it can
    only widen READ context. ssh/restart/copy wait for slice F."""
    monkeypatch.setenv("FOREMAN_NEEDS_LINT_MODE", "apply-dir")
    d = cl.derive({"title": "ops", "detail": "ssh isis and read /home/lumen/agent"},
                  roster=ROSTER, policy=POLICY, sweep=fresh_sweep())
    assert ("ssh", "isis") in d.tags and ("dir", "/home/lumen/agent") in d.tags
    assert cl.appliable_tags(d) == [("dir", "/home/lumen/agent")]
    monkeypatch.setenv("FOREMAN_NEEDS_LINT_MODE", "apply")
    assert cl.appliable_tags(d) == d.tags


def test_unreachable_state_store_fails_closed(monkeypatch):
    """An empty roster (minerva down) must make every node mention unresolved rather
    than resolve to some default node — fleet-exec:60 fails the same way."""
    monkeypatch.setenv("FOREMAN_LUMEN_STATE_CLI", "/nonexistent/lumen_state.py")
    monkeypatch.setattr(cl, "LUMEN_STATE_CLI", "/nonexistent/lumen_state.py")
    monkeypatch.setattr(cl, "_roster_cache", None)
    roster = cl.load_roster(refresh=True)
    assert roster.names == set()
    d = cl.derive({"title": "", "detail": "ssh isis-2 then restart ollama on isis"},
                  roster=roster, policy=POLICY)
    assert d.tags == [] and len(d.unresolved) == 2


def test_unreadable_policy_makes_no_refusal_and_no_copy_tag(monkeypatch):
    d = cl.derive({"title": "", "detail": "scp x isis:/home/isis/lumen-drop"},
                  roster=ROSTER, policy={})
    assert d.tags == [] and "not a copy_dirs entry" in d.unresolved[0]


# --- wq#2131: secrets stay hard, everything else is advisory ---------------------

HOME_ROOT_FILES = {"/home/lumen/.fleet_pw", "/home/lumen/.netrc", "/home/lumen/HANDOFF.md",
                   "/home/lumen/.git-credentials"}


@pytest.fixture
def home_root_files(monkeypatch):
    monkeypatch.setattr(cl, "_exists",
                        lambda p: p in EXISTING_DIRS or p in EXISTING_FILES
                        or p in HOME_ROOT_FILES)


@pytest.mark.parametrize("path", [
    "/home/lumen/.fleet_pw", "/home/lumen/.netrc", "/home/lumen/.git-credentials",
    "/home/lumen/.git-credentials-old", "/home/lumen/backup/db.pw",
    "/home/lumen/foreman/.netrc",
])
def test_new_secret_basenames_are_denied_as_cited(path):
    assert cl.dir_denied(path), path
    assert "deny:basename" in cl.dir_denied(path)


@pytest.mark.parametrize("path", ["/home/lumen/.fleet_pw", "/home/lumen/.netrc",
                                  "/home/lumen/.git-credentials"])
def test_home_root_secret_is_refused_by_basename_not_the_parent_dir(path, home_root_files):
    d = derive(f"the password is in {path}")
    assert d.advisory == [] and d.tags == []
    assert d.refused == [f"dir({path}) {cl.dir_denied(path)}"]
    assert "basename" in d.refused[0] and "deny:path (/home/lumen)" not in d.refused[0]


def test_missing_home_root_secret_is_refused_not_advisory():
    d = derive("cat /home/lumen/.fleet_pw please")
    assert d.refused and d.advisory == [] and d.unresolved == []


def test_original_deny_entries_are_untouched():
    assert cl.DIR_DENY_BASENAME_GLOBS == ("*.env", "*.token", "*.key", "*.pem", "id_*")
    assert cl.DIR_DENY_EXACT == ("/home/lumen", "/")
    assert "/home/lumen/worktrees" in cl.DIR_DENY_PREFIXES
    assert "/home/lumen/.secrets" in cl.DIR_DENY_PREFIXES


def test_home_root_non_secret_file_is_advisory(home_root_files):
    d = derive("see /home/lumen/HANDOFF.md for context")
    assert d.refused == [] and d.unresolved == [] and d.tags == []
    assert len(d.advisory) == 1 and "/home/lumen/HANDOFF.md" in d.advisory[0]


def test_dir_outside_allow_is_advisory():
    d = derive("compare with how /home/lumen/mailbot does it")   # exists, not allow-listed
    assert d.tags == [] and d.unresolved == [] and d.refused == []
    assert len(d.advisory) == 1 and "not under allow.read_dirs" in d.advisory[0]


@pytest.mark.parametrize("path", [
    "/home/lumen/.secrets/x.txt", "/home/lumen/secrets/x.txt", "/home/lumen/.ssh/config",
    "/home/lumen/.claude/settings.json", "/home/lumen/.config/gh/hosts.yml",
    "/home/lumen/agents/work-queue/queue.db",
])
def test_deny_list_hits_are_never_advisory(path):
    d = derive(f"read {path}")
    assert d.refused and d.advisory == [], (path, d)


@pytest.mark.parametrize("path", ["/home/lumen/worktrees/wq1-a1/.env",
                                  "/home/lumen/scratch/worktrees/x/id_rsa",
                                  "/home/lumen/worktrees/wq1-a1/.netrc"])
def test_secret_inside_a_worktree_is_still_refused(path):
    d = derive(f"look at {path}")
    assert d.refused and d.advisory == [], (path, d)


def test_fresh_sweep_hit_stays_refused():
    sweep = fresh_sweep()
    sweep["roots"]["/home/lumen/agent"]["hits"] = [{"path": "/home/lumen/agent/x.py",
                                                    "line": 3}]
    d = cl.derive({"title": "", "detail": "read /home/lumen/agent"},
                  roster=ROSTER, policy=POLICY, sweep=sweep)
    assert d.refused == ["dir(/home/lumen/agent) fresh secret-sweep hit at "
                         "/home/lumen/agent/x.py:3"]
    assert d.advisory == [] and d.tags == []


def test_stale_sweep_stays_unresolved_fail_closed():
    d = cl.derive({"title": "", "detail": "read /home/lumen/agent"},
                  roster=ROSTER, policy=POLICY, sweep={"ts": "2020-01-01T00:00:00Z",
                                                      "roots": {}})
    assert d.tags == [] and d.advisory == [] and len(d.unresolved) == 1
    assert "read_dir_sweep is missing" in d.unresolved[0]


def test_lever_off_restores_the_pre_advisory_buckets(monkeypatch, home_root_files):
    monkeypatch.setenv("FOREMAN_CAPABILITY_ADVISORY", "off")
    assert not cl.advisory_enabled()
    d = derive("see /home/lumen/HANDOFF.md and /home/lumen/mailbot and "
               "/home/lumen/mailbot/nope.py and /home/lumen/worktrees/wq1357-a1\n"
               "the box may need a reboot someday")
    assert d.advisory == []
    assert any("deny:path (/home/lumen)" in r for r in d.refused)
    assert any("deny:prefix (/home/lumen/worktrees)" in r for r in d.refused)
    assert any("not under allow.read_dirs" in u for u in d.unresolved)
    assert any("does not exist" in u for u in d.unresolved)
    assert any("'reboot' is a v2 verb" in u for u in d.unresolved)
    monkeypatch.delenv("FOREMAN_CAPABILITY_ADVISORY")
    assert cl.advisory_enabled()


def test_new_secret_denies_survive_the_lever(monkeypatch):
    monkeypatch.setenv("FOREMAN_CAPABILITY_ADVISORY", "off")
    assert cl.dir_denied("/home/lumen/.fleet_pw")


# v2 verb: hard only in fenced code / a list item / an imperative line -------------

@pytest.mark.parametrize("detail", [
    "Steps:\n```\nsudo systemctl restart foo\n```\n",
    "Do this:\n- reboot the node when idle\n",
    "1. shut it down with sudo shutdown -h\n",
    "* verify first, then power-cycle the plug",
    "reboot isis after the change",
    "then sudo cat /etc/x",
    "$ sudo whoami",
    "- do NOT reboot thor",       # a list line is a step line, negation or not
    "```sh\nfoo && reboot\n```",
])
def test_v2_verb_in_an_instruction_position_is_hard(detail):
    d = derive(detail)
    assert d.advisory == [] and any("v2 verb" in u for u in d.unresolved), d


@pytest.mark.parametrize("detail", [
    "The old flow needed a reboot after each change and we want to drop that.",
    "Do not reboot the box; the watchdog handles it.",
    "Never sudo in the worker sandbox.",
    "wq#1571 HANGed under fworker; the worker likely hangs on sudo cat /etc/x",
    "background: adb shell cmd notification is how the alert gets silenced",
    "Context paragraph.\n\nWe considered a `docker restart` but rejected it.",
])
def test_v2_verb_prose_mention_is_advisory(detail):
    d = derive(detail)
    assert d.unresolved == [] and d.tags == [], d
    assert d.advisory and all("v2 verb" in a and "prose mention" in a for a in d.advisory)


def test_v2_verb_after_a_closed_fence_is_prose_again():
    d = derive("```\nls\n```\nit used to reboot on its own")
    assert d.unresolved == [] and len(d.advisory) == 1


def test_v2_verb_in_prose_and_in_a_step_is_hard_for_the_step_only():
    d = derive("We never want a reboot here.\n- run sudo systemctl daemon-reload\n")
    assert len(d.unresolved) == 1 and "'sudo'" in d.unresolved[0]
    assert len(d.advisory) == 1 and "'reboot'" in d.advisory[0]


# advisory note --------------------------------------------------------------------

def test_advisory_note_is_recognised_in_the_tickets_own_notes():
    d = derive("compare with /home/lumen/mailbot")
    fp, text = cl.advisory_note(d)
    assert text.startswith(f"needs_advisory[{fp}]:") and "1 advisory finding(s)" in text
    assert cl.advisory_noted(f"2026-09-20 10:00 {text}", fp)
    assert not cl.advisory_noted("", fp) and not cl.advisory_noted(None, fp)


def test_advisory_note_fingerprint_follows_the_findings():
    a = cl.advisory_note(derive("compare with /home/lumen/mailbot"))[0]
    b = cl.advisory_note(derive("compare with /home/lumen/mailbot and /home/lumen/pokemon"))[0]
    assert a != b
    assert a == cl.advisory_note(derive("compare with /home/lumen/mailbot"))[0]


def test_advisory_note_is_bounded():
    d = cl.Derived(advisory=[f"finding {i} " + "x" * 300 for i in range(12)])
    _, text = cl.advisory_note(d)
    assert len(text) < 900 and "(+7 more)" in text


def test_advisory_alone_makes_the_derivation_truthy():
    assert cl.Derived(advisory=["x"]) and not cl.Derived()
