"""failure_record.py — wq#1330 unit coverage (WORKER_EXEC_CAPABILITY_GAP.md §4).

Pure-function tests: denied-call extraction from stream-json frames, the §4.1
inferred_needs map (node resolution injected — never a live lumen_state call),
mechanical triage in the doc's order, the failure.json write + sha contract, and
dispatcher/self_heal.classify_block()'s triage-led reason parsing.
"""
import hashlib
import json
import os
import sys

import pytest

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HARNESS_DIR)
sys.path.insert(0, os.path.join(os.path.dirname(HARNESS_DIR), "dispatcher"))
import failure_record as fr  # noqa: E402
import self_heal  # noqa: E402

NODE_MAP = {"100.89.147.95": "apis", "apis": "apis",
            "100.112.151.100": "thor", "thor": "thor"}

APPROVAL = "This command requires approval"
COMPOUND = ("Claude requested permissions to run a compound command, but the "
            "following parts require approval: ssh thor -- uptime")
PATH_BLOCK = ("was blocked. For security, Claude Code may only read files in "
              "the allowed working directories")


def use(tool, inp, uid):
    return {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": tool, "input": inp, "id": uid}]}}


def err(uid, text):
    return {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": uid, "is_error": True,
         "content": text}]}}


def denial_events(cmd, text=APPROVAL, times=1, tool="Bash", inp=None):
    ev = []
    for i in range(times):
        uid = f"t{i}"
        ev.append(use(tool, inp if inp is not None else {"command": cmd}, uid))
        ev.append(err(uid, text))
    return ev


# ---- extraction -------------------------------------------------------------------------

def test_argv_strips_env_prefix_and_wrappers():
    assert fr._argv("FOO=1 env timeout 5 ssh apis true")[0] == "ssh"
    assert fr._argv("sudo systemctl restart nginx")[0] == "sudo"


def test_denied_ssh_call_aggregates_with_count():
    # ISSUE-LOG #71 (wq#916): the exact refused shape, three retries.
    ev = denial_events("ssh -o ConnectTimeout=5 100.89.147.95 true", times=3)
    denied, unreachable, sig = fr.extract_denied_calls(ev, node_map=NODE_MAP)
    assert len(denied) == 1
    d = denied[0]
    assert (d["argv0"], d["node"], d["count"]) == ("ssh", "apis", 3)
    assert d["refusal"] == "requires approval"
    assert unreachable == []
    assert sig  # last_error_sig captured


def test_compound_denial_uses_the_refused_segment():
    ev = denial_events("uptime && ssh thor -- uptime", text=COMPOUND)
    denied, _, _ = fr.extract_denied_calls(ev, node_map=NODE_MAP)
    assert denied[0]["argv0"] == "ssh"
    assert denied[0]["node"] == "thor"


# wq#2691: direct invocation of the lumen_state.py shim (a bash-shebang script at
# /home/lumen/bin/lumen_state.py, not a *.py Claude Code can pattern-match against
# "Bash(python3 *)") isn't matched by any worker-settings.json allow entry, so the
# CLI's own classifier denies it outright — plain "requires approval" for the direct
# form, and a compound "multiple operations" refusal for the `bash lumen_state.py`
# form. Neither is a wq#2579-style structural refusal (no for/while/cd/heredoc
# marker in the reason text); both are ordinary allowlist misses, fixed by adding
# "Bash(/home/lumen/bin/lumen_state.py *)" to worker-settings.json.
LUMEN_STATE_DIRECT_REFUSAL = "This command requires approval"
LUMEN_STATE_BASH_WRAPPED_REFUSAL = (
    "This Bash command contains multiple operations. The following part "
    "requires approval: bash /home/lumen/bin/lumen_state.py current hathor")


def test_lumen_state_direct_invocation_denial_is_plain_not_structural():
    ev = denial_events("/home/lumen/bin/lumen_state.py current hathor",
                       text=LUMEN_STATE_DIRECT_REFUSAL)
    denied, _, _ = fr.extract_denied_calls(ev, node_map=NODE_MAP)
    assert len(denied) == 1
    d = denied[0]
    assert d["refusal"] == "requires approval"
    assert fr._structural_kind(d["reason_text"]) is None


def test_lumen_state_bash_wrapped_denial_uses_the_refused_segment():
    ev = denial_events("bash /home/lumen/bin/lumen_state.py current hathor",
                       text=LUMEN_STATE_BASH_WRAPPED_REFUSAL)
    denied, _, _ = fr.extract_denied_calls(ev, node_map=NODE_MAP)
    assert len(denied) == 1
    d = denied[0]
    assert d["argv0"] == "bash"
    assert fr._structural_kind(d["reason_text"]) is None


def test_blocked_path_lands_in_unreachable_paths():
    # wq#1152 signature: a Read outside worktree ∪ add-dirs.
    ev = denial_events(None, text=PATH_BLOCK, tool="Read",
                       inp={"file_path": "/home/lumen/projects/nest/nle-flasher/f.py"})
    denied, unreachable, _ = fr.extract_denied_calls(
        ev, node_map=NODE_MAP, reachable_roots=["/tmp/wt"])
    assert unreachable == ["/home/lumen/projects/nest/nle-flasher/f.py"]
    assert denied[0]["refusal"] == "blocked_path"


def test_reachable_path_is_not_flagged():
    ev = denial_events(None, text=PATH_BLOCK, tool="Read",
                       inp={"file_path": "/tmp/wt/inside.py"})
    _, unreachable, _ = fr.extract_denied_calls(
        ev, node_map=NODE_MAP, reachable_roots=["/tmp/wt"])
    assert unreachable == []


def test_plain_tool_error_is_not_a_denial():
    ev = denial_events("pytest -x", text="E   AssertionError: boom")
    denied, unreachable, sig = fr.extract_denied_calls(ev, node_map=NODE_MAP)
    assert denied == [] and unreachable == []
    assert "assertionerror" in sig


# ---- structural refusals (wq#2579: for/while loop, cd+relative-path, heredoc) ------------

FOR_LOOP_REFUSAL = (
    "This command requires approval: Contains for_statement, which is not allowed")
CD_RELATIVE_PATH_REFUSAL = (
    "This command requires approval: grep reads a file by a relative path after a "
    "cd in a compound command; which file that is cannot be resolved statically "
    "while a Read() deny rule is configured, so this needs approval.")


def test_structural_for_loop_is_not_misattributed_to_an_allowed_command():
    # wq#2564: this exact shape was misattributed to argv0=grep/cut, both already on
    # worker-settings.json, so triage never named the real (unfixable-by-allowlist)
    # cause.
    ev = denial_events(
        "for i in 1 2 3; do python3 router.py assign $i 2>&1 | grep -o ticket; done",
        text=FOR_LOOP_REFUSAL)
    denied, _, _ = fr.extract_denied_calls(ev, node_map=NODE_MAP)
    assert len(denied) == 1
    d = denied[0]
    assert d["argv0"] == "for_loop"
    assert d["refusal"] == "structural:for_loop"
    assert "for_statement" in d["reason_text"]


def test_structural_cd_relative_path_is_not_misattributed_to_an_allowed_command():
    ev = denial_events("cd /home/lumen/kb && grep -n pat ISSUE-LOG.md",
                       text=CD_RELATIVE_PATH_REFUSAL)
    denied, _, _ = fr.extract_denied_calls(ev, node_map=NODE_MAP)
    assert len(denied) == 1
    d = denied[0]
    assert d["argv0"] == "cd_relative_path"
    assert d["refusal"] == "structural:cd_relative_path"
    assert "relative path after a cd" in d["reason_text"]


# ---- inferred needs (§4.1 map, node resolution never guessed) ---------------------------

def test_infer_needs_ssh_resolved_node():
    ev = denial_events("ssh 100.89.147.95 true")
    denied, unreachable, _ = fr.extract_denied_calls(ev, node_map=NODE_MAP)
    assert fr.infer_needs(denied, unreachable) == ["ssh(apis)"]


def test_infer_needs_unresolvable_node_omits_tag():
    ev = denial_events("ssh 10.9.9.9 true")
    denied, unreachable, _ = fr.extract_denied_calls(ev, node_map=NODE_MAP)
    assert fr.infer_needs(denied, unreachable) == []


def test_infer_needs_sudo_restart_bench_dir():
    ev = (denial_events("sudo apt-get install -y foo")
          + denial_events("systemctl restart qbittorrent")
          + denial_events("adb devices"))
    denied, _, _ = fr.extract_denied_calls(ev, node_map=NODE_MAP)
    needs = fr.infer_needs(denied, ["/home/lumen/projects/nest/nle-flasher"])
    assert "root(lumen)" in needs
    assert "restart(qbittorrent@lumen)" in needs
    assert "bench(thor,adb)" in needs
    assert "dir(/home/lumen/projects/nest/nle-flasher)" in needs


def test_load_node_map_parses_state_lines():
    text = ("thor.tailscale_ip = 100.112.151.100 (since 2026-01-01)\n"
            "thor.role = bench host (since 2026-01-01)\n"
            "garbage line\n")
    m = fr.load_node_map(state_output=text)
    assert m == {"100.112.151.100": "thor", "thor": "thor"}


# ---- triage (doc §4.1 order) ------------------------------------------------------------

def _triage(classification="KILLED", trip_kind=None, events=(),
            wt=None, worker_reason=None):
    denied, unreachable, _ = fr.extract_denied_calls(list(events), node_map=NODE_MAP)
    return fr.triage(classification, trip_kind, denied, unreachable,
                     wt or {"match": None}, list(events),
                     worker_block_reason=worker_reason)


def test_triage_permission_trip_is_capability_denied_even_unmapped():
    t, head = _triage(classification="PERMISSION_LOOP", trip_kind="PERMISSION")
    assert t == "CAPABILITY_DENIED"


def test_triage_ssh_denial_is_capability_denied_with_needs_head():
    ev = denial_events("ssh -o ConnectTimeout=5 100.89.147.95 true", times=3)
    t, head = _triage(events=ev)
    assert t == "CAPABILITY_DENIED"
    assert "ssh(apis)" in head and "3x" in head


# wq#4716: a research ticket that needs live web data and has no route must say so,
# not fall through to UNKNOWN (wq#4183, CFP dates).
WEB_REFUSAL = ("Claude requested permissions to use WebSearch, but you haven't "
               "granted it yet.")


def say(text):
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}


def test_triage_refused_websearch_is_needs_web():
    ev = denial_events(None, text=WEB_REFUSAL, tool="WebSearch",
                       inp={"query": "DEF CON 35 CFP"}, times=2)
    t, head = _triage(events=ev)
    assert t == "CAPABILITY_DENIED"
    assert head.startswith("needs-web: WebSearch refused 2x")


@pytest.mark.parametrize("text", [
    "I cannot access the internet from this sandbox, so the 2027 CFP dates are unknown.",
    "I don't have web search available here.",
    "There is no web access route for this worker.",
    "Unable to reach the web to verify these deadlines.",
])
def test_triage_worker_prose_no_web_is_needs_web(text):
    t, head = _triage(events=[say(text)])
    assert t == "CAPABILITY_DENIED"
    assert head.startswith("needs-web: worker said")


@pytest.mark.parametrize("text", [
    "Wired the web dashboard and the tests pass.",
    "Cannot reproduce the crash; the fix is in the internet-facing proxy config.",
])
def test_triage_unrelated_web_words_stay_unknown(text):
    t, _ = _triage(events=[say(text)])
    assert t == "UNKNOWN"


def test_triage_worker_block_reason_outranks_needs_web():
    t, head = _triage(events=[say("I cannot access the internet.")],
                      worker_reason="needs Frank's decision on X")
    assert t == "REAL_BLOCK"


def test_triage_worktree_mismatch_is_harness_defect():
    t, head = _triage(wt={"match": False, "expected_repo": "/home/lumen/foreman"})
    assert t == "HARNESS_DEFECT"
    assert "WORKTREE_REPO_MISMATCH" in head


def test_triage_crash_with_traceback_is_genuine_bug():
    ev = denial_events("python3 x.py",
                       text="Traceback (most recent call last):\n  boom")
    t, _ = _triage(classification="CRASH", events=ev)
    assert t == "GENUINE_BUG"


def test_triage_hang_in_test_command_is_genuine_bug():
    ev = [use("Bash", {"command": "python3 -m pytest tests/ -x"}, "t0")]
    t, _ = _triage(classification="HANG", events=ev)
    assert t == "GENUINE_BUG"


def test_triage_worker_block_is_real_block_verbatim_first_line():
    t, head = _triage(worker_reason="needs Frank: CF dashboard credential\nmore")
    assert t == "REAL_BLOCK"
    assert head == "needs Frank: CF dashboard credential"


def test_triage_turns_budget_is_limit():
    assert _triage(classification="TURNS")[0] == "LIMIT"
    assert _triage(classification="BUDGET")[0] == "LIMIT"


def test_triage_default_is_unknown_never_silent():
    t, head = _triage(classification="CRASH")
    assert t == "UNKNOWN"
    assert "CRASH" in head


def test_triage_structural_for_loop_names_the_mechanical_signature():
    # wq#2564/wq#2579: previously fell to TRIAGE_UNKNOWN "no mechanical signature
    # matched" — the head must now name the structural cause, not an allowed argv0.
    ev = denial_events("for i in 1 2 3; do echo $i; done", text=FOR_LOOP_REFUSAL)
    t, head = _triage(events=ev)
    assert t == "CAPABILITY_DENIED"
    assert "for_loop" in head
    assert "no mechanical signature matched" not in head


def test_triage_structural_cd_relative_path_names_the_mechanical_signature():
    ev = denial_events("cd /home/lumen/kb && grep -n pat ISSUE-LOG.md",
                       text=CD_RELATIVE_PATH_REFUSAL)
    t, head = _triage(events=ev)
    assert t == "CAPABILITY_DENIED"
    assert "cd_relative_path" in head
    assert "no mechanical signature matched" not in head


# ---- record + write + reason line -------------------------------------------------------

def test_build_write_and_reason_line(tmp_path):
    ev = denial_events("ssh 100.112.151.100 true", times=3)
    rec = fr.build_failure_record(
        ticket=349, attempts=2, classification="PERMISSION_LOOP",
        block_class="HARNESS_DEFECT", events=ev, worktree=str(tmp_path),
        expected_repo=None, session_id="sid-123", trip_kind="PERMISSION",
        attempt_files=["attempt-1.json", "attempt-2.json"], node_map=NODE_MAP,
        check_worktree=False)
    assert rec["schema"] == fr.SCHEMA_VERSION
    assert rec["triage"] == "CAPABILITY_DENIED"
    assert rec["inferred_needs"] == ["ssh(thor)"]
    assert rec["denied_calls"][0]["count"] == 3
    assert rec["transcript"] == "~/.claude/projects/*/sid-123.jsonl"

    path = fr.write_failure_record(str(tmp_path), rec)
    assert os.path.basename(path) == "failure.json"
    line = fr.block_reason_line(rec, path)
    assert line.startswith("CAPABILITY_DENIED: ")
    assert line.endswith(f"; see {path}")
    assert "transcript=" not in line
    on_disk = json.load(open(path))
    assert on_disk == json.loads(json.dumps(rec))
    assert fr.sha256_file(path) == hashlib.sha256(
        open(path, "rb").read()).hexdigest()


def test_masked_origin_never_stores_credential():
    assert fr._mask_url("https://user:tok3n@git.example/x.git") == \
        "https://***@git.example/x.git"


# ---- self_heal.classify_block on the new reason format ----------------------------------

def test_classify_block_reads_block_class_from_failure_json(tmp_path):
    fpath = tmp_path / "failure.json"
    fpath.write_text(json.dumps({"schema": 1, "block_class": "INFRA_CRASH"}))
    reason = f"UNKNOWN: LAUNCH_FAILURE after 2 attempts; see {fpath}"
    klass, detail = self_heal.classify_block({"block_reason": reason})
    assert klass == "INFRA_CRASH"


def test_classify_block_triage_fallback_when_file_missing(tmp_path):
    reason = f"CAPABILITY_DENIED: ssh(thor) refused 3x; see {tmp_path}/gone/failure.json"
    klass, _ = self_heal.classify_block({"block_reason": reason})
    assert klass == "HARNESS_DEFECT"  # wq#1107 bucket: never breaker-counted
    klass, _ = self_heal.classify_block({"block_reason": "LIMIT: TURNS ceiling hit; see /nope/failure.json"})
    assert klass == "LIMIT_HIT"
    klass, _ = self_heal.classify_block({"block_reason": "UNKNOWN: CRASH; see /nope/failure.json"})
    assert klass is None  # conservative: treated like unmarked, never auto-released


def test_classify_block_legacy_prefix_still_wins():
    klass, detail = self_heal.classify_block(
        {"block_reason": "INFRA_CRASH: LAUNCH_FAILURE after 2 attempts; transcript=x"})
    assert klass == "INFRA_CRASH"


# ---- wq#5118: Claude Code permission decisions (auto-mode classifier / hard denial) -----
# Texts as the CLI 2.1.296 bundle builds them; the tail is the clause wq#5002's
# last_error_sig recorded, shared by all three.
_DENIAL_TAIL = ("IMPORTANT: You *may* attempt to accomplish this action using other tools "
                "that might naturally be used to accomplish this goal, e.g. using head "
                "instead of cat. But you *should not* attempt to work around this denial "
                "in malicious ways, e.g. do not use your ability to run tests to execute "
                "non-test actions. You should only try to work around this restriction in "
                "reasonable ways that do not attempt to bypass the intent behind this "
                "denial. If you believe this capability is essential to complete the "
                "user's request, STOP and explain to the user what you were trying to do "
                "and why you need this permission. Let the user decide how to proceed.")
AUTO_MODE_DENIAL = ("Permission for this action was denied by the Claude Code auto mode "
                    "classifier. Reason: Unauthorized Access. If you have other tasks that "
                    "don't depend on this action, continue working on those. "
                    + _DENIAL_TAIL)
HARD_DENIAL = "Permission to use Bash has been denied. " + _DENIAL_TAIL
REASONED_DENIAL = "Permission for this action has been denied. Reason: blocked by policy"
GREP_CMD = "grep -n litellm /home/lumen/foreman/harness/hallucination_guard.py"


@pytest.mark.parametrize("text,kind", [
    (AUTO_MODE_DENIAL, fr.CLASSIFIER_REFUSAL),
    (HARD_DENIAL, fr.PERMISSION_REFUSAL),
    (REASONED_DENIAL, fr.PERMISSION_REFUSAL),
    (APPROVAL, None), (COMPOUND, None), (PATH_BLOCK, None), (WEB_REFUSAL, None),
    ("grep: /nope: No such file or directory", None), ("", None), (None, None),
])
def test_decision_denial_kind(text, kind):
    assert fr.decision_denial_kind(text) == kind


def test_auto_mode_denial_is_not_an_allowlist_denial():
    """The reason it fell to UNKNOWN: neither existing matcher sees it."""
    assert not fr.is_permission_denial(AUTO_MODE_DENIAL)
    assert not fr.is_permission_denial(HARD_DENIAL)


def test_auto_mode_denial_row_names_action_and_reason():
    denied, unreachable, sig = fr.extract_denied_calls(
        denial_events(GREP_CMD, text=AUTO_MODE_DENIAL, times=5), node_map=NODE_MAP)
    assert unreachable == []
    assert len(denied) == 1
    d = denied[0]
    assert d["refusal"] == fr.CLASSIFIER_REFUSAL and d["count"] == 5
    assert d["argv0"] == "grep" and d["action"] == GREP_CMD
    assert d["reason_text"] == "Unauthorized Access"
    assert sig.endswith("let the user decide how to proceed.")
    assert fr.infer_needs(denied, unreachable) == []


def test_classifier_denied_ssh_infers_no_capability_need():
    denied, _, _ = fr.extract_denied_calls(
        denial_events("ssh thor uptime", text=AUTO_MODE_DENIAL), node_map=NODE_MAP)
    assert denied[0]["node"] == "thor"
    assert fr.infer_needs(denied, []) == []


def test_triage_wq5002_shape_is_classifier_denied():
    """wq#5002: KILLED by loop_break after the same refusal 5x -> was UNKNOWN."""
    t, head = _triage(classification="KILLED",
                      events=denial_events(GREP_CMD, text=AUTO_MODE_DENIAL, times=5))
    assert t == "CLASSIFIER_DENIED"
    assert head.startswith("auto-mode classifier denied 5x: " + GREP_CMD)
    assert "reason: Unauthorized Access" in head and "Frank" in head
    assert fr.route_for(t) == fr.ROUTE_FRANK_GATED


def test_triage_hard_denial_is_classifier_denied_and_says_which_layer():
    t, head = _triage(events=denial_events(GREP_CMD, text=HARD_DENIAL, times=5))
    assert t == "CLASSIFIER_DENIED"
    assert head.startswith("Claude Code permission layer denied 5x: ")


def test_triage_classifier_denial_outranks_capability_and_limit():
    ev = denial_events("ssh thor uptime", text=AUTO_MODE_DENIAL, times=2)
    assert _triage(classification="TURNS", events=ev)[0] == "CLASSIFIER_DENIED"


def test_triage_survived_classifier_denial_is_not_the_cause():
    """Denied once, worked around it, then died on something else."""
    ev = denial_events(GREP_CMD, text=AUTO_MODE_DENIAL)
    ev += [use("Bash", {"command": "pytest -q"}, "z"), err("z", "1 failed")]
    assert fr.classifier_denied_signal(ev) is None
    t, _ = _triage(classification="TURNS", events=ev)
    assert t == "LIMIT"


def test_triage_worker_self_block_outranks_classifier_denial():
    t, head = _triage(events=denial_events(GREP_CMD, text=AUTO_MODE_DENIAL),
                      worker_reason="needs Frank: classifier refused the audit grep")
    assert t == "REAL_BLOCK" and head.startswith("needs Frank")


def test_hard_denied_web_tool_stays_needs_web():
    ev = denial_events(None, text="Permission to use WebFetch has been denied. "
                       + _DENIAL_TAIL, tool="WebFetch", inp={"url": "https://x.test"})
    assert fr.classifier_denied_signal(ev) is None
    t, head = _triage(events=ev)
    assert t == "CAPABILITY_DENIED" and head.startswith("needs-web")


def test_auto_mode_denied_web_tool_is_classifier_denied():
    ev = denial_events(None, text=AUTO_MODE_DENIAL, tool="WebFetch",
                       inp={"url": "https://x.test"})
    sig = fr.classifier_denied_signal(ev)
    assert sig["kind"] == fr.CLASSIFIER_REFUSAL and sig["action"] == "WebFetch https://x.test"


def test_classifier_denied_record_routes_to_frank_and_is_a_work_block(tmp_path):
    rec = fr.build_failure_record(
        ticket=5002, attempts=2, classification="KILLED", block_class="INFRA_CRASH",
        events=denial_events(GREP_CMD, text=AUTO_MODE_DENIAL, times=5),
        node_map=NODE_MAP, check_worktree=False)
    assert rec["triage"] == "CLASSIFIER_DENIED"
    assert rec["route"] == "frank_gated"
    assert rec["block_class"] == "WORK_BLOCKED"
    assert rec["inferred_needs"] == []
    assert rec["denied_calls"][0]["action"] == GREP_CMD
    path = fr.write_failure_record(str(tmp_path), rec)
    assert fr.block_reason_line(rec, path).startswith("CLASSIFIER_DENIED: auto-mode")


def test_other_triage_has_no_route():
    rec = fr.build_failure_record(
        ticket=1, attempts=2, classification="CRASH", block_class="INFRA_CRASH",
        events=[], node_map=NODE_MAP, check_worktree=False)
    assert rec["triage"] == "UNKNOWN" and rec["route"] is None
    assert rec["block_class"] == "INFRA_CRASH"


def test_classifier_denied_is_a_non_competence_triage_value():
    assert "CLASSIFIER_DENIED" in fr.TRIAGE_VALUES
    assert "CLASSIFIER_DENIED" in fr.TRIAGE_NON_COMPETENCE


def test_classify_block_never_auto_releases_classifier_denied(tmp_path):
    fpath = tmp_path / "failure.json"
    fpath.write_text(json.dumps({"schema": 2, "block_class": "INFRA_CRASH"}))
    klass, detail = self_heal.classify_block(
        {"block_reason": f"CLASSIFIER_DENIED: auto-mode classifier denied 5x; see {fpath}"})
    assert klass == "WORK_BLOCKED" and detail.startswith("auto-mode classifier")
