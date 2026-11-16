"""wq#5007 — dual-condition exit gate, circuit breaker, hourly limit, session expiry,
per-loop git backup (harness/exit_gate.py) and their wiring into the Stop hook and
LoopTracker. Thresholds are the ticket's: indicators>=2 AND EXIT_SIGNAL:true;
no-progress 3, same-error 5, output-decline 70%, half-open after 30 min, 24 h sessions."""
import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "hooks"))
import action_gate
import exit_gate
import stop_gate


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


# ------------------------------------------------------------------ exit signal

@pytest.mark.parametrize("text,want", [
    ("done.\nEXIT_SIGNAL: true\nTICKET_CLOSED", True),
    ("EXIT_SIGNAL: false\nBLOCKED", False),
    ("exit_signal = TRUE\nTICKET_CLOSED", True),
    ("- `EXIT_SIGNAL: true`\nTICKET_CLOSED", True),
    ("EXIT_SIGNAL: false\n...later...\nEXIT_SIGNAL: true\nTICKET_CLOSED", True),   # last wins
    ("EXIT_SIGNAL: true\nactually no\nEXIT_SIGNAL: false", False),
    ("all landed, PR merged.\nTICKET_CLOSED", None),                    # missing
    ("I will set EXIT_SIGNAL: true once the tests pass.", None),          # prose, not a line
    ("EXIT_SIGNAL: maybe", None),
    ("", None), (None, None), (42, None),
])
def test_parse_exit_signal(text, want):
    assert exit_gate.parse_exit_signal(text) is want


# ------------------------------------------------------------------ the dual gate

@pytest.mark.parametrize("n,signal,exits,reason", [
    (2, True, True, "exit"),
    (3, True, True, "exit"),
    (2, False, False, "signal_false"),               # EXIT_SIGNAL governs
    (4, False, False, "signal_false"),
    (2, None, False, "signal_missing"),              # missing defaults to false
    (4, None, False, "signal_missing"),
    (1, True, False, "indicators_below_threshold"),  # threshold not met
    (0, True, False, "indicators_below_threshold"),
    (0, None, False, "signal_missing"),
])
def test_decide_table(n, signal, exits, reason):
    ind = {f"i{k}": k < n for k in range(4)}
    d = exit_gate.decide(ind, signal)
    assert (d.exit, d.reason, len(d.indicators)) == (exits, reason, n)


def test_decide_never_exits_on_one_condition_exhaustive():
    """Test to failure: every combination of 4 indicators x 3 signal values — an exit
    happens on exactly the (>=2, True) cells and nowhere else."""
    for mask in range(16):
        ind = {f"i{k}": bool(mask >> k & 1) for k in range(4)}
        for signal in (True, False, None):
            want = signal is True and bin(mask).count("1") >= 2
            assert exit_gate.decide(ind, signal).exit is want, (mask, signal)


def test_nudge_names_what_is_missing():
    miss = exit_gate.nudge_text(exit_gate.decide({"ticket_done": True, "x": True}, None), 7)
    assert "no EXIT_SIGNAL line" in miss and "wq#7" in miss
    low = exit_gate.nudge_text(exit_gate.decide({"ticket_done": True}, True))
    assert "fewer than 2" in low
    assert "not finished" in exit_gate.nudge_text(exit_gate.decide({}, False))


# ------------------------------------------------------------------ circuit breaker

def cb(tmp_path, clock=None):
    return exit_gate.CircuitBreaker(str(tmp_path / "cb.json"), now=clock or Clock())


def test_no_progress_opens_at_three_with_half_open_warning(tmp_path):
    b = cb(tmp_path)
    assert b.record_loop(False, output_chars=1000) == exit_gate.CLOSED
    assert b.record_loop(False, output_chars=1000) == exit_gate.HALF_OPEN
    assert b.record_loop(False, output_chars=1000) == exit_gate.OPEN
    assert "no progress 3" in b.s["reason"]


def test_progress_resets_and_closes(tmp_path):
    b = cb(tmp_path)
    b.record_loop(False, output_chars=900)
    b.record_loop(False, output_chars=900)
    assert b.state == exit_gate.HALF_OPEN
    assert b.record_loop(True, output_chars=900) == exit_gate.CLOSED
    assert b.s["no_progress"] == 0
    b.record_loop(False, output_chars=900)
    assert b.record_loop(False, output_chars=900) == exit_gate.HALF_OPEN  # counts from 0 again


def test_same_error_opens_at_five_even_with_progress(tmp_path):
    b = cb(tmp_path)
    for i in range(4):
        assert b.record_loop(True, error_sig="adb: no devices") == exit_gate.CLOSED, i
    assert b.record_loop(True, error_sig="adb: no devices") == exit_gate.OPEN
    assert "same error 5" in b.s["reason"]


def test_different_error_restarts_the_streak(tmp_path):
    b = cb(tmp_path)
    for _ in range(4):
        b.record_loop(True, error_sig="A")
    b.record_loop(True, error_sig="B")
    assert b.state == exit_gate.CLOSED and b.s["same_error"] == 1
    b.record_loop(True, error_sig=None)
    assert b.s["same_error"] == 0


def test_output_decline_over_70_percent_opens_only_without_progress(tmp_path):
    b = cb(tmp_path)
    b.record_loop(True, output_chars=10_000)
    assert b.record_loop(True, output_chars=100) == exit_gate.CLOSED      # progress: fine
    b2 = cb(tmp_path / "..", Clock()) if False else exit_gate.CircuitBreaker(
        str(tmp_path / "cb2.json"), now=Clock())
    b2.record_loop(True, output_chars=10_000)
    assert b2.record_loop(False, output_chars=2_999) == exit_gate.OPEN    # 70.01% drop
    assert "declined" in b2.s["reason"]
    b3 = exit_gate.CircuitBreaker(str(tmp_path / "cb3.json"), now=Clock())
    b3.record_loop(True, output_chars=10_000)
    assert b3.record_loop(False, output_chars=3_000) == exit_gate.CLOSED  # exactly 70%: no


def test_decline_ignores_a_tiny_baseline(tmp_path):
    b = cb(tmp_path)
    b.record_loop(True, output_chars=50)
    assert b.record_loop(False, output_chars=1) == exit_gate.CLOSED


def test_question_loop_holds_counters_steady(tmp_path):
    b = cb(tmp_path)
    b.record_loop(False, output_chars=1000)
    for _ in range(10):
        b.record_loop(False, output_chars=10, asking_questions=True)
    assert b.s["no_progress"] == 1 and b.state == exit_gate.CLOSED


def test_unmeasurable_progress_is_never_counted(tmp_path):
    b = cb(tmp_path)
    for _ in range(10):
        assert b.record_loop(None, output_chars=1000) == exit_gate.CLOSED


def test_open_refuses_then_half_opens_after_30_min(tmp_path):
    clock = Clock()
    b = cb(tmp_path, clock)
    for _ in range(3):
        b.record_loop(False, output_chars=1000)
    assert b.state == exit_gate.OPEN
    allow, why = b.preflight()
    assert not allow and "OPEN" in why
    clock.t += 29 * 60 + 59
    assert b.preflight()[0] is False
    clock.t += 2
    allow, why = b.preflight()
    assert allow and why == "half-open probe" and b.state == exit_gate.HALF_OPEN


def test_half_open_probe_decides(tmp_path):
    clock = Clock()
    for progress, want in ((True, exit_gate.CLOSED), (False, exit_gate.OPEN)):
        p = tmp_path / f"cb-{progress}.json"
        b = exit_gate.CircuitBreaker(str(p), now=clock)
        for _ in range(3):
            b.record_loop(False, output_chars=1000)
        clock.t += 31 * 60
        assert b.preflight()[0]
        assert b.record_loop(progress, output_chars=1000) == want
    # a failed probe starts a fresh cooldown
    assert b.preflight()[0] is False


def test_state_survives_the_process(tmp_path):
    clock = Clock()
    b = cb(tmp_path, clock)
    for _ in range(3):
        b.record_loop(False, output_chars=1000)
    again = cb(tmp_path, clock)
    assert again.state == exit_gate.OPEN and again.preflight()[0] is False
    assert [h["to"] for h in again.s["history"]] == [exit_gate.HALF_OPEN, exit_gate.OPEN]


def test_reset_is_the_exit_path(tmp_path):
    b = cb(tmp_path)
    for _ in range(3):
        b.record_loop(False, error_sig="E", output_chars=1000)
    b.reset("exit")
    assert b.state == exit_gate.CLOSED and b.preflight() == (True, None)
    assert b.s["no_progress"] == 0 and b.s["same_error"] == 0


def test_corrupt_state_file_starts_closed(tmp_path):
    (tmp_path / "cb.json").write_text("{not json")
    assert cb(tmp_path).state == exit_gate.CLOSED


# ------------------------------------------------------------------ loop tracker wrap

def fail_frames(i, err, cmd="pytest -q"):
    return [{"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": f"t{i}", "name": "Bash",
                 "input": {"command": cmd}}]}},
            {"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": f"t{i}", "is_error": True,
                 "content": [{"type": "text", "text": err}]}]}}]


def ok_edit(i):
    return [{"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": f"e{i}", "name": "Edit",
                 "input": {"file_path": "/w/a.py"}}]}},
            {"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": f"e{i}", "is_error": False,
                 "content": "ok"}]}}]


def feed(tracker, frames):
    for f in frames:
        r = tracker.observe(f)
        if r:
            return r
    return None


def test_same_error_with_edits_between_needs_five_not_three():
    """The false-kill class: a TDD loop whose test keeps failing with the same tail
    while the agent edits between runs. Bare LoopTracker kills at 3; wrapped needs 5."""
    bare = action_gate.LoopTracker(threshold=3)
    wrapped = exit_gate.CircuitLoopTracker(action_gate.LoopTracker(threshold=3))
    bare_trip = wrapped_trip = None
    for i in range(1, 7):
        frames = fail_frames(i, "AssertionError: expected 3 got 4") + ok_edit(i)
        if bare_trip is None and feed(bare, frames):
            bare_trip = i
        if wrapped_trip is None and feed(wrapped, frames):
            wrapped_trip = i
    assert (bare_trip, wrapped_trip) == (3, 5)
    assert wrapped.tripped.startswith("same error 5x")


def test_no_progress_trigger_stays_at_three():
    """Same command class failing with NO successful change between: still 3."""
    wrapped = exit_gate.CircuitLoopTracker(action_gate.LoopTracker(threshold=3))
    trip = None
    for i in range(1, 7):
        if feed(wrapped, fail_frames(i, f"boom variant {i} {'x' * i}")):
            trip = i
            break
    assert trip == 3 and "same command class" in wrapped.tripped


def test_identical_failure_without_any_change_still_trips_at_three():
    """Same command, same error, nothing changed between: the no-progress trigger must
    not be delayed to 5 just because the error text also repeats."""
    wrapped = exit_gate.CircuitLoopTracker(action_gate.LoopTracker(threshold=3))
    trip = None
    for i in range(1, 7):
        if feed(wrapped, fail_frames(i, "adb: no devices found", "adb wait-for-device")):
            trip = i
            break
    assert trip == 3 and "same command class" in wrapped.tripped


def test_permission_refusal_keeps_the_tracker_threshold():
    wrapped = exit_gate.CircuitLoopTracker(action_gate.LoopTracker(threshold=3, steered=True))
    denial = "This command requires approval"
    assert action_gate.is_permission_denial(denial)
    trip = None
    for i in range(1, 7):
        if feed(wrapped, fail_frames(i, denial) + ok_edit(i)):
            trip = i
            break
    assert trip == 3 and wrapped.trip_kind == "PERMISSION"


def test_wrapper_is_transparent_for_harness_attributes():
    inner = action_gate.LoopTracker(threshold=3, ceiling_spent=True)
    w = exit_gate.CircuitLoopTracker(inner)
    assert w.ceiling_spent is True and w.steer is None and w.tripped is None
    w.ceiling_spent = False
    assert inner.ceiling_spent is False
    assert "trigger:" in w.report()


# ------------------------------------------------------------------ hourly limit

def test_hourly_limit_global_and_ticket_and_rollover(tmp_path):
    clock = Clock()
    root = str(tmp_path)
    for _ in range(3):
        assert exit_gate.take_call(root, 1, clock, global_limit=5, ticket_limit=3)[0]
    ok, why = exit_gate.take_call(root, 1, clock, global_limit=5, ticket_limit=3)
    assert not ok and "ticket call limit 3/3" in why
    assert exit_gate.take_call(root, 2, clock, global_limit=5, ticket_limit=3)[0]
    assert exit_gate.take_call(root, 3, clock, global_limit=5, ticket_limit=3)[0]
    ok, why = exit_gate.take_call(root, 4, clock, global_limit=5, ticket_limit=3)
    assert not ok and "fleet call limit 5/5" in why
    # refused calls were not counted
    assert json.load(open(tmp_path / exit_gate.CALLS_FILENAME))["global"] == 5
    clock.t += 3600
    assert exit_gate.take_call(root, 1, clock, global_limit=5, ticket_limit=3)[0]
    assert json.load(open(tmp_path / exit_gate.CALLS_FILENAME))["global"] == 1


def test_hourly_limit_zero_disables(tmp_path):
    for _ in range(50):
        assert exit_gate.take_call(str(tmp_path), 9, Clock(), global_limit=0, ticket_limit=0)[0]


# ------------------------------------------------------------------ session expiry

def test_session_expires_after_24h_and_unknown_is_not_expired(tmp_path):
    clock = Clock()
    rd = str(tmp_path)
    exit_gate.note_session(rd, "sid-a", clock)
    clock.t += 24 * 3600 - 1
    assert not exit_gate.session_expired(rd, "sid-a", clock)
    exit_gate.note_session(rd, "sid-a", clock)        # re-noting never rejuvenates
    clock.t += 2
    assert exit_gate.session_expired(rd, "sid-a", clock)
    assert not exit_gate.session_expired(rd, "never-seen", clock)
    assert not exit_gate.session_expired(rd, "sid-a", clock, expiry_s=0)


# ------------------------------------------------------------------ git backup

def git(cwd, *a):
    return subprocess.run(["git", "-C", str(cwd), *a], capture_output=True, text=True,
                          check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.email", "t@t")
    git(r, "config", "user.name", "t")
    (r / "tracked.txt").write_text("v1\n")
    (r / ".gitignore").write_text("ignored.bin\n")
    git(r, "add", "-A")
    git(r, "commit", "-qm", "init")
    return r


def test_backup_captures_dirty_and_untracked_without_touching_the_tree(repo):
    (repo / "tracked.txt").write_text("v2 uncommitted\n")
    (repo / "new.py").write_text("print(1)\n")
    (repo / "ignored.bin").write_text("secret-ish\n")
    status_before = git(repo, "status", "--porcelain")
    head_before = git(repo, "rev-parse", "HEAD")
    ref = exit_gate.git_backup(str(repo), 5007, "a1")
    assert ref and ref.startswith("refs/foreman-backup/wq5007/")
    assert git(repo, "status", "--porcelain") == status_before
    assert git(repo, "rev-parse", "HEAD") == head_before
    assert git(repo, "show", f"{ref}:tracked.txt") == "v2 uncommitted"
    assert git(repo, "show", f"{ref}:new.py") == "print(1)"
    files = git(repo, "ls-tree", "-r", "--name-only", ref).split()
    assert "ignored.bin" not in files
    assert not [f for f in os.listdir(git(repo, "rev-parse", "--absolute-git-dir"))
                if f.startswith("foreman-backup-index")]


def test_backup_leaves_large_untracked_files_out_of_the_object_store(repo, monkeypatch):
    """An RE worktree's multi-GB dump must not be hashed into .git on every loop."""
    monkeypatch.setattr(exit_gate, "BACKUP_MAX_UNTRACKED_FILE_BYTES", 100)
    (repo / "dump.bin").write_bytes(b"x" * 5000)
    (repo / "notes.txt").write_text("small\n")
    (repo / "tracked.txt").write_text("y" * 5000)          # tracked: always included
    (repo / "sp ace é.txt").write_text("odd name\n")
    ref = exit_gate.git_backup(str(repo), 3, "a1")
    files = git(repo, "-c", "core.quotepath=off", "ls-tree", "-r", "--name-only",
                ref).splitlines()
    assert "dump.bin" not in files
    assert {"notes.txt", "tracked.txt", "sp ace é.txt"} <= set(files)
    assert len(git(repo, "show", f"{ref}:tracked.txt")) == 5000
    monkeypatch.setattr(exit_gate, "BACKUP_MAX_UNTRACKED_FILE_BYTES", 10 ** 6)
    monkeypatch.setattr(exit_gate, "BACKUP_MAX_UNTRACKED_TOTAL_BYTES", 20)
    ref2 = exit_gate.git_backup(str(repo), 3, "a2")
    assert "dump.bin" not in git(repo, "ls-tree", "-r", "--name-only", ref2).split()


def test_rollback_restores_the_snapshot(repo):
    (repo / "tracked.txt").write_text("good state\n")
    ref = exit_gate.git_backup(str(repo), 1, "a1")
    (repo / "tracked.txt").write_text("the loop wrecked this\n")
    ok, err = exit_gate.rollback(str(repo), ref)
    assert ok, err
    assert (repo / "tracked.txt").read_text() == "good state\n"


def test_backups_are_pruned_to_keep(repo, monkeypatch):
    monkeypatch.setattr(exit_gate, "BACKUP_KEEP", 3)
    t = [1_800_000_000]
    monkeypatch.setattr(exit_gate.time, "time", lambda: t.__setitem__(0, t[0] + 1) or t[0])
    for i in range(6):
        (repo / "tracked.txt").write_text(f"v{i}\n")
        assert exit_gate.git_backup(str(repo), 2, f"a{i}")
    assert len(exit_gate.list_backups(str(repo), 2)) == 3
    assert exit_gate.list_backups(str(repo), 999) == []


def test_backup_outside_a_repo_is_none_not_an_error(tmp_path):
    assert exit_gate.git_backup(str(tmp_path), 1, "a1") is None
    assert exit_gate.worktree_fingerprint(str(tmp_path)) is None


def test_fingerprint_moves_on_edit_commit_and_new_file(repo):
    seen = {exit_gate.worktree_fingerprint(str(repo))}
    (repo / "tracked.txt").write_text("edit\n")
    seen.add(exit_gate.worktree_fingerprint(str(repo)))
    (repo / "u.txt").write_text("x")
    seen.add(exit_gate.worktree_fingerprint(str(repo)))
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "c2")
    seen.add(exit_gate.worktree_fingerprint(str(repo)))
    assert len(seen) == 4 and None not in seen
    assert exit_gate.worktree_fingerprint(str(repo)) == exit_gate.worktree_fingerprint(str(repo))


# ------------------------------------------------------------------ loop stats

def test_loop_stats_reads_mutations_errors_and_questions():
    events = fail_frames(1, "E: device 0x7f3a busy at 12:01:02") + ok_edit(1) + [
        {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "Which option should I take?"}]}},
        {"type": "weird"}, "not-a-dict", {"type": "user", "message": "str"}]
    s = exit_gate.loop_stats(events)
    assert s["mutations"] == 1 and s["asking_questions"] is True
    assert "0x7f3a" not in s["error_sig"] and "12:01:02" not in s["error_sig"]
    assert s["output_chars"] > 0
    assert exit_gate.loop_stats([])["asking_questions"] is False
    assert exit_gate.loop_stats(None)["error_sig"] is None


# ------------------------------------------------------------------ Stop hook half

def transcript(tmp_path, final_text):
    p = tmp_path / "t.jsonl"
    p.write_text(json.dumps({"type": "assistant", "message": {"content": [
        {"type": "text", "text": final_text}]}}) + "\n")
    return str(p)


@pytest.fixture
def hook_env(tmp_path, monkeypatch):
    monkeypatch.setenv("FOREMAN_EXIT_GATE_PROTOCOL", "1")
    monkeypatch.setenv("FOREMAN_TICKET_ID", "5007")
    monkeypatch.setenv("FOREMAN_STOP_GATE_STATE", str(tmp_path / "stop-gate.json"))
    monkeypatch.setenv("FOREMAN_WORKTREE_ROOT", str(tmp_path))
    monkeypatch.delenv("FOREMAN_EXIT_GATE", raising=False)
    monkeypatch.setattr(stop_gate.acceptance, "read_acceptance", lambda *a, **k: None)
    monkeypatch.setattr(stop_gate, "_mechanical_trap", lambda *a, **k: None)
    monkeypatch.setattr(stop_gate, "_branch_has_commits", lambda wt: True)
    state = {"done": True, "status": "done"}
    monkeypatch.setattr(stop_gate, "_ticket_is_done", lambda t: state["done"])
    monkeypatch.setattr(stop_gate, "_ticket_status", lambda t: state["status"])
    return state


def hook(tmp_path, text, sid="s1"):
    return stop_gate.decide({"session_id": sid, "cwd": str(tmp_path),
                             "transcript_path": transcript(tmp_path, text)})


def test_hook_allows_the_full_exit(tmp_path, hook_env):
    assert hook(tmp_path, "landed PR 1.\nEXIT_SIGNAL: true\nTICKET_CLOSED") == (stop_gate.ALLOW, "")


def test_hook_blocks_missing_signal_then_caps(tmp_path, hook_env):
    for n in (1, 2, 3):
        code, msg = hook(tmp_path, "landed PR 1.\nTICKET_CLOSED")
        assert code == stop_gate.BLOCK and f"({n}/3)" in msg and "no EXIT_SIGNAL line" in msg
    assert hook(tmp_path, "landed PR 1.\nTICKET_CLOSED")[0] == stop_gate.ALLOW


def test_hook_blocks_signal_true_without_two_indicators(tmp_path, hook_env, monkeypatch):
    hook_env.update(done=False, status="claimed")
    monkeypatch.setattr(stop_gate, "_branch_has_commits", lambda wt: False)
    code, msg = hook(tmp_path, "I think it is fine.\nEXIT_SIGNAL: true\nTICKET_CLOSED")
    assert code == stop_gate.BLOCK and "fewer than 2" in msg


def test_hook_blocks_explicit_false_while_still_claimed(tmp_path, hook_env):
    hook_env.update(done=False, status="claimed")
    code, msg = hook(tmp_path, "pausing here.\nEXIT_SIGNAL: false")
    assert code == stop_gate.BLOCK and "not finished" in msg


@pytest.mark.parametrize("text,status", [
    ("cannot reach the device.\nEXIT_SIGNAL: false\nBLOCKED", "blocked"),
    ("lost the claim.\nEXIT_SIGNAL: false\nCLAIM_ERROR", "claimed"),
    ("released it, not mine to finish.\nEXIT_SIGNAL: false", "open"),
    ("parked on a grant", "awaiting_grant"),
])
def test_hook_steps_aside_for_legitimate_non_completion_stops(tmp_path, hook_env, text, status):
    hook_env.update(done=False, status=status)
    assert hook(tmp_path, text)[0] == stop_gate.ALLOW


def test_hook_is_inert_without_the_protocol_env_or_in_shadow(tmp_path, hook_env, monkeypatch):
    monkeypatch.setenv("FOREMAN_EXIT_GATE", "shadow")
    assert hook(tmp_path, "no signal at all")[0] == stop_gate.ALLOW
    monkeypatch.delenv("FOREMAN_EXIT_GATE")
    monkeypatch.delenv("FOREMAN_EXIT_GATE_PROTOCOL")
    assert hook(tmp_path, "no signal at all")[0] == stop_gate.ALLOW


def test_hook_fails_open_on_unreadable_transcript(tmp_path, hook_env):
    assert stop_gate.decide({"session_id": "s", "cwd": str(tmp_path),
                             "transcript_path": str(tmp_path / "absent.jsonl")})[0] == stop_gate.ALLOW


# ------------------------------------------------------------------ worker prompt

def test_worker_prompt_carries_the_contract_and_keeps_the_sentinel_last():
    p = os.path.join(os.path.dirname(__file__), "..", "prompts", "worker.md")
    text = open(p).read()
    assert exit_gate.PROTOCOL_MARKER in text
    assert text.index("EXIT_SIGNAL: true|false") < text.index("TICKET_CLOSED | BLOCKED | CLAIM_ERROR")
    import classifier
    assert classifier.parse_worker_sentinel("ok\nEXIT_SIGNAL: true\nTICKET_CLOSED") == "TICKET_CLOSED"


def test_mode_parsing(monkeypatch):
    for raw, want in (("", "enforce"), ("SHADOW", "shadow"), ("off", "off"), ("junk", "enforce")):
        monkeypatch.setenv("FOREMAN_EXIT_GATE", raw)
        assert exit_gate.mode() == want
