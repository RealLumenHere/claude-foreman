"""wq#3971 -- tests for the read-only live operator console.

Writes fixture evidence blocks to a workspace's evidence/tool-calls.jsonl exactly
as wq#3964's EvidenceLog would (one canonical JSON object per line, append order),
then asserts the console tail emits them in order in both text and --json modes,
masks a credential in a coverage note, survives a torn line, and streams appends in
--follow mode. The console never writes to the workspace (read-only).
"""
import io
import json
import os
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import workspace_cli as wc  # noqa: E402
import workspace_console as wcon  # noqa: E402


def _block(seq, tool, ok=True, coverage=None, untrusted=False):
    """An evidence block shaped like ops/wq3808 evidence.build_evidence output."""
    return {
        "operation": tool,
        "tool": tool,
        "source": {"kind": "mcp", "server": "binwalk", "tool": tool, "git_sha": "0" * 40},
        "args_sha256": "ab" * 32,
        "started_at": f"2026-01-01T00:0{seq}:00.000000Z",
        "finished_at": f"2026-01-01T00:0{seq}:01.000000Z",
        "ok": ok,
        "exit_code": 0 if ok else 1,
        "error": None if ok else "boom",
        "coverage": coverage,
        "untrusted_content": untrusted,
        "evidence_id": f"ev_{seq:064d}",
    }


def _append(ws, block):
    """Append one block the way wq#3964's EvidenceLog does: canonical JSON + \\n."""
    path = wcon.log_path(ws)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    line = json.dumps(block, sort_keys=True, separators=(",", ":")) + "\n"
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line)


@pytest.fixture
def workspace(tmp_path):
    return str(tmp_path / "ws")


def test_text_mode_emits_in_order(workspace):
    _append(workspace, _block(1, "extract_rootfs", coverage="full"))
    _append(workspace, _block(2, "signature_scan", ok=False))
    _append(workspace, _block(3, "entropy", coverage="partial: 2 regions skipped"))

    lines = wcon.render_lines(wcon.read_blocks(workspace))

    assert len(lines) == 3
    assert lines[0].split("\t") == ["2026-01-01T00:01:01.000000Z", "extract_rootfs", "ok", "full"]
    assert lines[1].split("\t") == ["2026-01-01T00:02:01.000000Z", "signature_scan", "FAIL", "-"]
    assert lines[2].split("\t") == ["2026-01-01T00:03:01.000000Z", "entropy", "ok",
                                     "partial: 2 regions skipped"]
    # order preserved: the three tools appear in append order.
    assert [ln.split("\t")[1] for ln in lines] == ["extract_rootfs", "signature_scan", "entropy"]


def test_json_mode_emits_in_order(workspace):
    _append(workspace, _block(1, "extract_rootfs", coverage="full"))
    _append(workspace, _block(2, "signature_scan", ok=False))

    lines = wcon.render_lines(wcon.read_blocks(workspace), json_mode=True)

    recs = [json.loads(ln) for ln in lines]
    assert [r["tool"] for r in recs] == ["extract_rootfs", "signature_scan"]
    assert recs[0] == {"ts": "2026-01-01T00:01:01.000000Z", "tool": "extract_rootfs",
                       "ok": True, "coverage": "full",
                       "evidence_id": "ev_" + "0" * 63 + "1", "untrusted_content": False}
    assert recs[1]["ok"] is False and recs[1]["coverage"] is None


def test_cli_both_modes_match_render(workspace, capsys):
    _append(workspace, _block(1, "extract_rootfs", coverage="full"))
    _append(workspace, _block(2, "signature_scan", ok=False))

    assert wc.main(["console", workspace]) == 0
    text_out = capsys.readouterr().out.splitlines()
    assert text_out == wcon.render_lines(wcon.read_blocks(workspace))

    assert wc.main(["--json", "console", workspace]) == 0
    json_out = capsys.readouterr().out.splitlines()
    assert [json.loads(x) for x in json_out] == \
        [json.loads(x) for x in wcon.render_lines(wcon.read_blocks(workspace), json_mode=True)]


def test_coverage_is_credential_masked(workspace):
    secret = "gh" "p_" + "A" * 36
    _append(workspace, _block(1, "dump", coverage=f"leaked {secret} in /etc"))

    text = wcon.render_lines(wcon.read_blocks(workspace))[0]
    rec = json.loads(wcon.render_lines(wcon.read_blocks(workspace), json_mode=True)[0])
    assert secret not in text and secret not in json.dumps(rec)
    assert "MASKED" in text and "MASKED" in rec["coverage"]


def test_missing_log_is_empty_not_error(workspace, capsys):
    os.makedirs(workspace)  # a real workspace that has logged no calls yet
    assert wcon.read_blocks(workspace) == []
    assert wc.main(["console", workspace]) == 0
    assert capsys.readouterr().out == ""


def test_torn_line_is_skipped_rest_survives(workspace):
    _append(workspace, _block(1, "extract_rootfs"))
    with open(wcon.log_path(workspace), "a", encoding="utf-8") as fh:
        fh.write('{"tool": "half-written\n')  # a torn append, no closing brace
    _append(workspace, _block(2, "signature_scan"))

    tools = [ln.split("\t")[1] for ln in wcon.render_lines(wcon.read_blocks(workspace))]
    assert tools == ["extract_rootfs", "signature_scan"]


def test_bad_workspace_exits_1(tmp_path, capsys):
    rc = wc.main(["console", str(tmp_path / "nope")])
    assert rc == 1
    assert "no such workspace" in capsys.readouterr().err


def test_follow_streams_appended_blocks(workspace):
    _append(workspace, _block(1, "extract_rootfs"))
    out = io.StringIO()
    seen = {"n": 0}

    def _stop():
        # stop once we've seen the block appended after follow started.
        seen["n"] = len(out.getvalue().splitlines())
        return seen["n"] >= 2

    def _run():
        wcon.follow(workspace, out=out, poll_interval=0.01, stop=_stop)

    t = threading.Thread(target=_run)
    t.start()
    time.sleep(0.05)
    _append(workspace, _block(2, "signature_scan"))
    t.join(timeout=5)

    assert not t.is_alive()
    tools = [ln.split("\t")[1] for ln in out.getvalue().splitlines()]
    assert tools == ["extract_rootfs", "signature_scan"]


def test_console_never_writes_to_workspace(workspace):
    _append(workspace, _block(1, "extract_rootfs"))
    path = wcon.log_path(workspace)
    before = (os.path.getsize(path), os.path.getmtime(path))
    wcon.render_lines(wcon.read_blocks(workspace), json_mode=True)
    wcon.emit(workspace, out=io.StringIO())
    after = (os.path.getsize(path), os.path.getmtime(path))
    assert before == after  # read-only: size and mtime unchanged
