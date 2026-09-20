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
        needle = argv[-1]
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


def run(tmp_path, monkeypatch, leak, env):
    alarms = []
    monkeypatch.setattr(fworker, "mask_alarm",
                        lambda tid, att, labels, records=None: alarms.append(labels))
    _state, _code, events, _killed, _cpu, _pid = fworker.run_child(
        make_child(tmp_path, leak), REPO_ROOT, env,
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
