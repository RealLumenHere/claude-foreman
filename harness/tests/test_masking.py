"""wq#2275 (token-engine T6): credentials in the child's stream-json must be masked
before anything derived from the stream can reach disk, and a hit must alarm
(ntfy + rotation ticket) because the token already reached the model's context.

The integration test drives fworker.run_child() with a real subprocess emitting
NDJSON frames — the same plumbing test_loop_break_integration.py rides — and
asserts the synthetic ghp_ value is absent from every returned event while the
alarm fired exactly once.
"""
import json
import os
import sys
import textwrap

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import fworker
import masking

# Synthetic, never-live values shaped like the real thing.
GHP = "ghp_" + "A1b2C3d4E5f6G7h8J9k0A1b2C3d4E5f6G7h8"
GHPAT = "github_pat_" + "11ABCDE0Y" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8s9T0u1V2w3X4y5Z6"
SK = "sk-ant-" + "x" * 24
AKIA = "AKIA" + "ABCDEFGHIJKLMNOP"

# wq#2292: strings that LOOK like a prefix rule but are not credentials. Each one
# is a real shape that reached a stream and cost a pri-1 rotation ticket or a
# failed broker verb. Both in-repo copies of the house set must stay silent on
# every one of them.
FALSE_POSITIVES = [
    # the exact string that fired on wq#2282: `ghs_solver` inside a repo name,
    # from the ISSUE-LOG row documenting this same bug in remote_credential_sweep
    "https://github.com/ERGO-Code/highs_solver.git flagged UNSTRIPPABLE",
    "clone git@github.com:o/ghp_notes.git",          # right prefix, body too short
    "the risk-assessment-framework doc needs review",  # `sk-` inside `risk-`
    "ghp_MASKED and github_pat_MASKED",  # our OWN output must not re-arm the alarm
]


def test_patterns_mask_and_label():
    text = f"remote https://x:{GHP}@github.com and {SK} and {AKIA}"
    masked, hits = masking.mask_text(text)
    assert GHP not in masked and SK not in masked and AKIA not in masked
    assert "ghp_MASKED" in masked
    assert set(hits) == {"github", "sk-key", "aws"}


def test_github_pat_still_masked():
    # not a `TOKEN=` context: kv-secret would otherwise eat the prefix too
    masked, hits = masking.mask_text(f"remote https://x:{GHPAT}@github.com")
    assert GHPAT not in masked and "github_pat_MASKED" in masked
    assert hits == ["github"]   # one label even though two github rules exist


def test_false_positives_never_alarm():
    """wq#2292: the pri-1 rotation alarm must not fire on a repo name."""
    for text in FALSE_POSITIVES:
        masked, hits = masking.mask_text(text)
        assert hits == [], f"{text!r} falsely matched {hits}"
        assert masked == text, f"{text!r} was rewritten to {masked!r}"


def test_token_engine_mirror_agrees():
    """The daemon keeps its own copy of the house set (it runs as `tokeneng`, off
    this import path). Drift between them is silent, so pin them to each other:
    same verdict on every false positive AND on every real token."""
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..",
                                    "token-engine"))
    import token_engine_daemon as ted

    for text in FALSE_POSITIVES:
        assert ted.mask_hits(text) == [], f"{text!r} trips the daemon tripwire"
    for real in (GHP, GHPAT, SK, AKIA):
        assert ted.mask_hits(f'v = "{real}"'), f"daemon stopped catching {real[:8]}"
        assert masking.mask_text(f'v = "{real}"')[1], f"masking stopped catching {real[:8]}"


def test_clean_text_untouched():
    text = json.dumps({"type": "assistant", "message": {"content": [
        {"type": "text", "text": "cache_read_input_tokens: 1234"}]}})
    masked, hits = masking.mask_text(text)
    assert masked == text and hits == []


def test_masked_ndjson_line_still_parses():
    frame = {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "t0",
         "content": [{"type": "text", "text": f"origin https://x:{GHP}@github.com"}]}]}}
    masked, hits = masking.mask_text(json.dumps(frame))
    reparsed = json.loads(masked)   # masking must never corrupt the JSON
    assert hits == ["github"]
    assert GHP not in json.dumps(reparsed)


def make_leaky_child(tmp_path):
    """A child that emits a tool_result containing a synthetic ghp_ token, twice
    (alarm must still fire once), then a clean result frame."""
    script = textwrap.dedent(f"""\
        import json
        def emit(f):
            print(json.dumps(f), flush=True)
        emit({{"type": "system", "subtype": "init", "session_id": "mask-int",
              "model": "fake"}})
        for i in range(2):
            emit({{"type": "user", "message": {{"content": [
                {{"type": "tool_result", "tool_use_id": f"t{{i}}",
                  "content": [{{"type": "text",
                    "text": "remote: https://x:{GHP}@github.com/o/r.git"}}]}}]}}}})
        emit({{"type": "result", "subtype": "success", "is_error": False,
              "result": "done, remote was {GHP}"}})
    """)
    p = tmp_path / "leaky_child.py"
    p.write_text(script)
    return [sys.executable, str(p)]


def test_run_child_masks_stream_and_alarms_once(tmp_path, monkeypatch):
    alarms = []
    monkeypatch.setattr(fworker, "mask_alarm",
                        lambda tid, att, labels: alarms.append((tid, att, labels)))
    state, exit_code, events, killed_reason, cpu_s, pid = fworker.run_child(
        make_leaky_child(tmp_path), str(tmp_path), dict(os.environ),
        max_turns=50, wall_clock_s=30, ticket_id=999998, attempt=1,
        frame_gap_s=10, post_result_grace_s=1)
    # the synthetic token never appears in anything fworker retained from the stream
    dump = json.dumps(events) + json.dumps(state.result_text or "")
    assert GHP not in dump
    assert "ghp_MASKED" in dump
    # alarm fired exactly once for the github label despite two leaky frames
    assert alarms == [(999998, 1, ["github"])]


def test_mask_alarm_pages_and_files_rotation_ticket(monkeypatch):
    pages, adds = [], []

    class P:
        returncode = 0
        stdout = "wq#424242 added"
        stderr = ""

    monkeypatch.setattr(fworker, "ntfy",
                        lambda title, msg, priority="default":
                        pages.append((title, priority)))
    monkeypatch.setattr(fworker, "wq", lambda *a, **k: adds.append(a) or P())
    fworker.mask_alarm(1234, 2, ["github"])
    assert pages and pages[0][0] == "foreman MASK HIT" and pages[0][1] == "high"
    assert adds and adds[0][0] == "add"
    assert any("rotation" in str(x) for x in adds[0])
