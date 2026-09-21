"""wq#2290: the wq#2275 mask tripwire must page for a burned credential and stay
quiet for a credential-shaped string that lives in the repo's own source.

The production trip this reproduces: wq#2278 read harness/tests/test_token_injection.py,
whose fixtures are shaped like real oauth tokens, and the tripwire filed a pri-1
rotation ticket for a value that was never live.

Every credential-shaped fixture here is assembled at runtime from halves that match
nothing on their own — a literal in this file would trip the very alarm the file
exists to test, which is how the false-alarm storm propagated in the first place.
"""
import json
import os
import subprocess
import sys
import tempfile
import textwrap

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import burn_filter
import fworker
import masking

OAT = "sk-ant-" + "oat01-not-a-real-token-value"
GHP = "ghp_" + "b7XQ2m4Kd9Rv1Tz0Yh6Lp3Ws8Nc5Fj7Ga2E"
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(REPO)


class FakeGrep:
    """Stands in for `git grep`: returns 0 for strings declared committed."""

    def __init__(self, committed=()):
        self.committed = set(committed)
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        # wq#2319: the value is the `-e` operand, not the last word — a pathspec
        # follows it so a grant is only excused by text under the granted dir.
        needle = argv[argv.index("-e") + 1] if "-e" in argv else argv[-1]
        return subprocess.CompletedProcess(
            argv, 0 if needle in self.committed else 1)


# --- live_secret_values -----------------------------------------------------

def test_live_values_taken_from_secret_shaped_names():
    live = burn_filter.live_secret_values({
        "CLAUDE_CODE_OAUTH_TOKEN": OAT,
        "ANTHROPIC_AUTH_TOKEN": "gw-" + "2f91c4ab77de6015",
        "FOREMAN_WORKER": "fworker-wq2290-a1",
    })
    assert live == {OAT, "gw-" + "2f91c4ab77de6015"}


def test_paths_and_urls_under_secret_names_are_not_secrets():
    # FOREMAN_LITELLM_KEY_FILE holds the path TO the key, not the key.
    live = burn_filter.live_secret_values({
        "FOREMAN_LITELLM_KEY_FILE": "/home/lumen/.secrets/foreman-litellm.token",
        "ANTHROPIC_BASE_URL_TOKEN": "http://localhost:4000/very/long/path",
        "SHORT_TOKEN": "abc",
    })
    assert live == set()


# --- classify ---------------------------------------------------------------

def test_live_credential_is_burned():
    detail = burn_filter.matched_values(f"CLAUDE_CODE_OAUTH_TOKEN={OAT}")
    grep = FakeGrep(committed=[])
    burned, benign = burn_filter.classify(detail, {OAT}, REPO, runner=grep)
    assert "sk-key" in burned and benign == []


def test_committed_source_literal_is_benign():
    line = f'    token_file.write_text("{OAT}")'
    masked = masking.mask_text(line)[0]
    detail = burn_filter.matched_values(line)
    grep = FakeGrep(committed=[m for ms in detail.values() for m in ms])
    burned, benign = burn_filter.classify(detail, set(), REPO, runner=grep)
    assert burned == [] and "sk-key" in benign
    assert OAT not in masked          # masking is unchanged either way


def test_unaccounted_value_is_burned():
    """Not in the child's env, not in the repo — assume the worst."""
    detail = burn_filter.matched_values(f"here is {GHP} from somewhere")
    burned, benign = burn_filter.classify(
        detail, set(), REPO, runner=FakeGrep(committed=[]))
    assert burned == ["github"] and benign == []


def test_one_live_value_burns_the_label_despite_benign_siblings():
    other = "sk-ant-" + "oat01-a-committed-fixture-value"
    detail = burn_filter.matched_values(f"{other} and {OAT}")
    grep = FakeGrep(committed=[other])
    burned, _ = burn_filter.classify(detail, {OAT}, REPO, runner=grep)
    assert burned == ["sk-key"]


def test_live_secret_is_never_passed_to_a_subprocess():
    """instinct 4: a real credential must not reach an argv the process table shows."""
    detail = burn_filter.matched_values(f"OAUTH_TOKEN={OAT}")
    grep = FakeGrep(committed=[])
    burn_filter.classify(detail, {OAT}, REPO, runner=grep)
    assert all(OAT not in " ".join(argv) for argv in grep.calls)


def test_provenance_lookups_are_cached_across_lines():
    line = f'write("{OAT}")'
    grep = FakeGrep(committed=[])
    cache = {}
    for _ in range(3):
        detail = burn_filter.matched_values(line)
        burn_filter.classify(detail, set(), REPO, runner=grep, cache=cache)
    # one lookup per distinct matched string, not per line
    assert len(grep.calls) == len(cache)


# --- is_committed_literal against the real tree -----------------------------

def test_is_committed_literal_finds_tracked_text():
    # tracked in harness/fworker.py; an untracked file would NOT count, which is
    # the intended strictness — only vetted, committed text excuses a mask hit.
    assert burn_filter.is_committed_literal("LOCAL_POOL_MODELS", REPO)


def test_is_committed_literal_rejects_absent_text():
    assert not burn_filter.is_committed_literal(
        "zz-" + "absent-from-every-tracked-file-9713", REPO)


# --- masking detail ---------------------------------------------------------

def test_every_label_sees_the_real_token_not_an_earlier_mask():
    """kv-secret runs after sk-key. Sequentially it would see `sk-ant-MASKED`, a
    string that is on no disk anywhere, so its provenance lookup could only fail and
    the line would page. Both labels must resolve to the value actually present."""
    line = f'"API_KEY": "{OAT}"'
    detail = burn_filter.matched_values(line)
    assert detail["sk-key"] == [OAT]
    assert any(OAT in m for m in detail["kv-secret"])
    assert "MASKED" not in "".join(m for ms in detail.values() for m in ms)
    # and end to end: the whole line resolves to committed text, so it stays quiet
    grep = FakeGrep(committed=[m for ms in detail.values() for m in ms])
    assert burn_filter.classify(detail, set(), REPO, runner=grep)[0] == []


# --- end to end through run_child -------------------------------------------

def make_child(tmp_path, leak):
    script = textwrap.dedent(f"""\
        import json
        def emit(f):
            print(json.dumps(f), flush=True)
        emit({{"type": "system", "subtype": "init", "session_id": "burn-int",
              "model": "fake"}})
        emit({{"type": "user", "message": {{"content": [
            {{"type": "tool_result", "tool_use_id": "t0",
              "content": [{{"type": "text", "text": {leak!r}}}]}}]}}}})
        emit({{"type": "result", "subtype": "success", "is_error": False,
              "result": "done"}})
    """)
    p = tmp_path / "child.py"
    p.write_text(script)
    return [sys.executable, str(p)]


def run(tmp_path, monkeypatch, leak, env, extra_args=()):
    alarms = []
    monkeypatch.setattr(fworker, "mask_alarm",
                        lambda tid, att, labels, records=None: alarms.append(labels))
    # extra_args ride on the child's argv the way --add-dir rides on the CLI's:
    # ignored by the script, read back by run_child to learn what it granted.
    _state, _code, events, _killed, _cpu, _pid = fworker.run_child(
        make_child(tmp_path, leak) + list(extra_args), REPO_ROOT, env,
        max_turns=50, wall_clock_s=30, ticket_id=999290, attempt=1,
        frame_gap_s=10, post_result_grace_s=1)
    return alarms, json.dumps(events)


def test_reading_a_committed_fixture_does_not_page(tmp_path, monkeypatch):
    """wq#2290 regression: wq#2278 read an auth test whose fixtures are shaped like
    oauth tokens, and the tripwire filed a pri-1 rotate-today ticket for a value
    that was never live.

    Provenance is stubbed rather than read from a real file: which files still hold
    a credential-shaped literal changes as fixtures get scrubbed, and this test is
    about what the stream reader does with the answer, not how it is obtained.
    test_reading_ordinary_source_does_not_page covers the real `git grep`.
    """
    monkeypatch.setattr(burn_filter, "is_committed_literal",
                        lambda value, root, runner=None: OAT in value)
    alarms, dump = run(tmp_path, monkeypatch,
                       f'    token_file.write_text("{OAT}")', dict(os.environ))
    assert alarms == []
    assert OAT not in dump      # still masked out of every artefact


def test_reading_ordinary_source_does_not_page():
    """Not just fixtures: kv-secret keys off an English word, so plain source that
    assigns anything to a name containing `key`/`token` reads as a credential. 35
    tracked files still page that way after wq#2292 anchored the prefix rules."""
    path = os.path.join(REPO, "read_dir_sweep.py")
    with open(path, encoding="utf-8") as f:
        text = f.read()
    hits = masking.mask_text(text)[1]
    if not hits:
        pytest.skip(f"{path} no longer matches any pattern")
    detail = burn_filter.matched_values(text, hits)
    burned, benign = burn_filter.classify(detail, set(), REPO_ROOT)
    assert burned == [] and benign


def _tracked_modules():
    out = subprocess.run(
        ["git", "-C", REPO_ROOT, "ls-files", "harness/*.py", "dispatcher/*.py"],
        capture_output=True, text=True)
    if out.returncode:
        return []
    return [os.path.join(REPO_ROOT, p) for p in out.stdout.split()]


def _sweep(runner):
    """Read every tracked module the way a worker does. Returns the ones that still
    match a pattern and the ones that would page, sharing one provenance cache."""
    matched, pages, cache = [], [], {}
    for path in _tracked_modules():
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        labels = masking.mask_text(text)[1]
        if not labels:
            continue
        matched.append(os.path.relpath(path, REPO_ROOT))
        burned, _ = burn_filter.classify(
            burn_filter.matched_values(text, labels), set(), REPO_ROOT,
            runner=runner, cache=cache)
        if burned:
            pages.append((os.path.relpath(path, REPO_ROOT), burned))
    return matched, pages


def test_no_tracked_module_pages_when_a_worker_reads_it():
    """wq#2314/wq#2253: read 8 of that run was harness/node_render_gateway.py, whose
    `key = apply_key_allowlist(reg)` read as a 19-char secret and filed a pri-1
    rotate-today ticket 34 s in. test_reading_ordinary_source_does_not_page pins one
    file and skips when that file stops matching; doctrine has every worker read the
    tree, so the invariant is tree-wide — opening any tracked module must not page.
    """
    matched = _tracked_modules()
    if not matched:
        pytest.skip("not a git checkout")
    _, pages = _sweep(subprocess.run)
    assert pages == [], "reading tracked source pages: %s" % pages
    # ...and the zero is screening's doing, not a corpus that matches nothing: blind
    # the provenance check and the same sweep must page, or this test proves nothing.
    blinded, blind_pages = _sweep(FakeGrep())
    assert blind_pages, (
        "sweep is vacuous: %d files match a pattern, none page unaccounted"
        % len(blinded))


# --- provenance across every granted dir (wq#2319) --------------------------

def _repo(path, files):
    """A git repo at `path` with `files` ({relpath: text}) committed."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    for rel, text in files.items():
        f = path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
    subprocess.run(["git", "-C", str(path), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(path), "-c", "user.email=t@t", "-c",
                    "user.name=t", "commit", "-qm", "fixture"], check=True)
    return str(path)


def test_provenance_roots_keeps_granted_repos_and_drops_the_rest(tmp_path):
    worktree = _repo(tmp_path / "wt", {"a.py": "x = 1\n"})
    granted = _repo(tmp_path / "kb", {"log.md": "y\n"})
    # Like /tmp/fworker-<id> and /home/lumen/agents/work-queue: granted, but with no
    # repo above it, so it has no committed text to excuse anything. tmp_path is not
    # usable here — pytest roots it inside this very checkout.
    with tempfile.TemporaryDirectory(dir="/tmp") as scratch:
        roots = burn_filter.provenance_roots(worktree, [granted, scratch])
    assert roots == [worktree, granted]


def test_provenance_roots_puts_the_worktree_first_and_dedups(tmp_path):
    worktree = _repo(tmp_path / "wt", {"a.py": "x = 1\n"})
    roots = burn_filter.provenance_roots(worktree, [worktree, None])
    assert roots == [worktree]


def test_value_committed_only_in_another_granted_repo_is_benign(tmp_path):
    """wq#2319: three sk-key values in wq#2288's 22:36 alarm were prose from
    /home/lumen/kb/ISSUE-LOG.md:657 — committed text in a repo that is not the
    worktree. Screening against the worktree alone left them unaccounted, so the
    doctrine log every worker is ordered to read paged on its own rows, and the
    pri-1 ticket it filed sent the next worker to read it again.
    """
    value = "sk-ant-" + "oat01-committed-in-the-doctrine-log"
    worktree = _repo(tmp_path / "wt", {"a.py": "x = 1\n"})
    doctrine = _repo(tmp_path / "kb", {"ISSUE-LOG.md": f"| 569 | quoting {value} |\n"})
    detail = burn_filter.matched_values(f"read: {value}")
    assert burn_filter.classify(detail, set(), [worktree])[0] == ["sk-key"]
    assert burn_filter.classify(detail, set(), [worktree, doctrine]) == ([], ["sk-key"])


def test_a_value_in_no_granted_repo_still_pages(tmp_path):
    """The widened screen must not become a blanket excuse."""
    value = "sk-ant-" + "oat01-tracked-by-absolutely-nothing"
    roots = [_repo(tmp_path / "wt", {"a.py": "x = 1\n"}),
             _repo(tmp_path / "kb", {"log.md": "unrelated\n"})]
    detail = burn_filter.matched_values(f"leaked {value}")
    assert burn_filter.classify(detail, set(), roots)[0] == ["sk-key"]


def test_a_grant_does_not_extend_to_the_rest_of_its_repo(tmp_path):
    """/home/lumen/projects/nest is one directory of the /home/lumen/projects repo.
    Text from a sibling project the child could not read must not excuse its hit, so
    the granted dir is searched as given rather than widened to the repo root."""
    value = "sk-ant-" + "oat01-only-in-a-sibling-project"
    repo = _repo(tmp_path / "projects", {"other/x.py": f'k = "{value}"\n',
                                         "nest/y.py": "k = 1\n"})
    granted = os.path.join(repo, "nest")
    detail = burn_filter.matched_values(f"read: {value}")
    assert burn_filter.classify(detail, set(), [granted])[0] == ["sk-key"]
    assert burn_filter.classify(detail, set(), [repo]) == ([], ["sk-key"])


def test_extra_roots_cost_nothing_once_a_value_is_accounted_for(tmp_path):
    """First hit wins: the argv exposure of an unaccounted value must not grow with
    the number of directories the child was granted (instinct 4)."""
    value = "sk-ant-" + "oat01-committed-in-the-first-root"
    grep = FakeGrep(committed=[value])
    burn_filter.is_committed_literal(value, ["/r1", "/r2", "/r3"], runner=grep)
    assert len(grep.calls) == 1


def test_granted_dirs_are_read_back_off_the_child_command_line():
    cmd = fworker.build_claude_cmd(
        "prompt", "claude-opus-5", "xhigh", "/tmp/settings.json",
        add_dirs=["/home/lumen/kb", "/home/lumen/bin"])
    assert fworker.granted_dirs(cmd) == ["/home/lumen/kb", "/home/lumen/bin"]


def test_run_child_screens_against_the_dirs_its_own_argv_granted(
        tmp_path, monkeypatch):
    """wq#2309: the grant is read off the argv inside run_child, so nothing below the
    unit tests pins that the stream path actually uses it — cwd would screen clean in
    every one of them. Drive the real thing with real `git grep`: one stream, run
    twice, paging or quiet purely on whether the repo holding the text was granted.
    """
    value = "sk-ant-" + "oat01-committed-only-in-the-granted-repo"
    granted = _repo(tmp_path / "kb", {"runbook.md": f"the runbook quotes {value}\n"})
    leak = f"read the runbook: {value}"

    ungranted, _ = run(tmp_path, monkeypatch, leak, dict(os.environ))
    assert ungranted and "sk-key" in ungranted[0]

    quiet, dump = run(tmp_path, monkeypatch, leak, dict(os.environ),
                      extra_args=["--add-dir", granted])
    assert quiet == []
    assert value not in dump    # masked either way — screening gates only the page


def test_the_same_shape_pages_when_it_is_the_childs_real_token(
        tmp_path, monkeypatch):
    """Identical string, identical provenance answer — but this time it is the
    token the harness handed the child, so the live check fires first and it pages."""
    monkeypatch.setattr(burn_filter, "is_committed_literal",
                        lambda value, root, runner=None: OAT in value)
    env = dict(os.environ)
    env["CLAUDE_CODE_OAUTH_TOKEN"] = OAT
    alarms, dump = run(tmp_path, monkeypatch, f"leaked {OAT}", env)
    assert alarms and "sk-key" in alarms[0]
    assert OAT not in dump


# --- wq#2404: a screen fix must reach the runs already in flight -------------------

# The screen as 281327c deployed it at 23:09:47Z, in one line: it accounts for text
# this process's copy cannot, because it reads roots this copy never looked in.
WIDER_SCREEN = """

def is_committed_literal(value, repo_roots, runner=subprocess.run):
    return True
"""

# the other direction: a deploy that accounts for nothing, so it calls every hit a
# burn. Spelled out rather than left to the real `git grep`, so these tests assert the
# recheck's logic and not what happens to be committed in this tree today.
STRICTER_SCREEN = """

def is_committed_literal(value, repo_roots, runner=subprocess.run):
    return False
"""

# and its opposite, a deploy that stopped screening at all. Silencing the tripwire is
# worse than a false page, so this one must be refused, not adopted.
GUTTED_SCREEN = """

def classify(detail, live, repo_roots, runner=subprocess.run, cache=None):
    return [], list(detail)
"""


def burn_filter_copy(tmp_path, extra=""):
    """A loadable copy of burn_filter.py at our own path, `extra` appended. Copying
    is what makes deployed() load it: an unchanged file comes back by identity."""
    with open(burn_filter.__file__, errors="replace") as fh:
        source = fh.read()
    path = tmp_path / "burn_filter_copy.py"
    path.write_text(source + extra)
    return str(path)


def screen(tmp_path, monkeypatch, extra, burned_here):
    """Run one alarming line through screen_mask_hits with the DEPLOYED screen being
    a copy carrying `extra`, and this process's own screen answering `burned_here`
    for provenance. Returns the labels that alarmed."""
    alarms = []
    monkeypatch.setattr(fworker, "mask_alarm",
                        lambda tid, att, labels, records=None: alarms.append(labels))
    monkeypatch.setattr(burn_filter, "is_committed_literal",
                        lambda value, roots, runner=None: not burned_here)
    real, copy = burn_filter.deployed, burn_filter_copy(tmp_path, extra)
    monkeypatch.setattr(burn_filter, "deployed", lambda path=None: real(copy))
    fworker.screen_mask_hits(["sk-key"], f"leaked {OAT}", set(), set(), set(),
                             [REPO], 2404, 1)
    return alarms


def test_deployed_is_the_imported_screen_when_the_file_has_not_changed():
    assert burn_filter.deployed() is burn_filter


def test_a_screen_fix_reaches_a_run_in_flight(tmp_path, monkeypatch):
    """The defect this closes. wq#2327 taught the rule TABLE to reload per call; the
    provenance screen still froze at spawn, so 281327c (23:09:47Z) and be9994d
    (23:44:19Z) reached no run then going. The process that filed wq#2348 at 23:01:44Z
    had imported its screen at 22:14:43Z, and every one of the ten values it filed is
    text the screen accounts for today."""
    assert screen(tmp_path, monkeypatch, WIDER_SCREEN, burned_here=True) == []


def test_the_deployed_screen_can_only_drop_an_alarm_never_raise_one(
        tmp_path, monkeypatch):
    """One-directional, like wq#2327's table recheck. A label the deployed screen
    calls burned but this process already screened clean has not been through this
    process's own provenance check, and paging on it re-opens the chain."""
    assert screen(tmp_path, monkeypatch, STRICTER_SCREEN, burned_here=False) == []


def test_a_hit_both_screens_call_burned_still_pages(tmp_path, monkeypatch):
    """The recheck must not become a way for a real burn to go quiet."""
    assert screen(tmp_path, monkeypatch, STRICTER_SCREEN, burned_here=True) == [["sk-key"]]


def test_a_deployed_screen_that_stopped_screening_is_not_trusted(
        tmp_path, monkeypatch):
    """A half-written deploy or an emptied classify would quiet every alarm in the
    fleet at once. The canary self-test refuses it and the caller keeps its own."""
    assert burn_filter.deployed(burn_filter_copy(tmp_path, GUTTED_SCREEN)) is burn_filter
    assert screen(tmp_path, monkeypatch, GUTTED_SCREEN, burned_here=True) == [["sk-key"]]


def test_the_recheck_does_not_reuse_this_processs_provenance_answers(
        tmp_path, monkeypatch):
    """The two passes must not share classify()'s cache: a fix to is_committed_literal
    is exactly what lands on this path, and a cache hit would answer with the version
    the process was spawned with — the bug, one layer down."""
    calls = []
    monkeypatch.setattr(burn_filter, "is_committed_literal",
                        lambda value, roots, runner=None: calls.append(value) or False)
    real, copy = burn_filter.deployed, burn_filter_copy(tmp_path, WIDER_SCREEN)
    monkeypatch.setattr(burn_filter, "deployed", lambda path=None: real(copy))
    monkeypatch.setattr(fworker, "mask_alarm", lambda *a, **k: None)
    fworker.screen_mask_hits(["sk-key"], f"leaked {OAT}", set(), set(), set(),
                             [REPO], 2404, 1)
    assert calls == [OAT]     # this process asked once; the deployed copy asked itself
