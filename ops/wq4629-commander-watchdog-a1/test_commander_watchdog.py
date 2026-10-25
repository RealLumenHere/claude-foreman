"""wq#4629 — tests for lumen-job-notify.sh + drain_done_dir.sh.

Stubs lumen-run/ntfy-send/claude so this needs no real systemd unit, no
network, and no fleet credentials. Run: pytest ops/wq4629-commander-watchdog-a1/test_commander_watchdog.py
"""
import json
import os
import stat
import subprocess
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _write_stub(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def test_lumen_job_notify_repolls_past_timeout_and_writes_marker(tmp_path):
    lumen_run = tmp_path / "lumen-run"
    count_file = tmp_path / "wait_count"
    _write_stub(
        lumen_run,
        f"""#!/usr/bin/env bash
count=0
[ -f "{count_file}" ] && count=$(cat "{count_file}")
count=$((count + 1))
echo "$count" > "{count_file}"
if [ "$count" -lt 2 ]; then
  echo "TIMEOUT after 1s (still RUNNING)"
else
  echo "DONE:0"
fi
""",
    )

    done_dir = tmp_path / "done"
    env = dict(os.environ, LUMEN_RUN_BIN=str(lumen_run), COMMANDER_WATCHDOG_DONE_DIR=str(done_dir))
    result = subprocess.run(
        ["bash", str(HERE / "lumen-job-notify.sh"), "lumen-job-build-limpet-123", "--poll-cap", "1"],
        env=env, capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr

    marker = done_dir / "lumen-job-build-limpet-123.json"
    assert marker.exists(), "marker not written"
    data = json.loads(marker.read_text())
    assert data["result"] == "DONE:0"
    assert data["unit"] == "lumen-job-build-limpet-123"
    assert count_file.read_text().strip() == "2", "expected 1 timeout + 1 terminal lumen-run call"


def test_drain_notifies_injects_claude_and_clears_marker(tmp_path):
    done_dir = tmp_path / "done"
    done_dir.mkdir()
    marker = done_dir / "lumen-job-build-limpet-123.json"
    marker.write_text(json.dumps({
        "unit": "lumen-job-build-limpet-123",
        "result": "DONE:0",
        "finished_at": "2026-10-25T00:00:00Z",
    }))

    ntfy_log = tmp_path / "ntfy.log"
    claude_log = tmp_path / "claude.log"
    ntfy_send = tmp_path / "ntfy-send"
    claude_bin = tmp_path / "claude"
    _write_stub(ntfy_send, f'#!/usr/bin/env bash\necho "$*" >> "{ntfy_log}"\n')
    _write_stub(claude_bin, f'#!/usr/bin/env bash\necho "$*" >> "{claude_log}"\n')

    env = dict(
        os.environ,
        COMMANDER_WATCHDOG_DONE_DIR=str(done_dir),
        NTFY_SEND_BIN=str(ntfy_send),
        CLAUDE_BIN=str(claude_bin),
        COMMANDER_WATCHDOG_NOTIFY_CLAUDE="1",
    )
    result = subprocess.run(
        ["bash", str(HERE / "drain_done_dir.sh")],
        env=env, capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr

    time.sleep(0.3)  # let the backgrounded claude -p stub flush its log line
    assert not marker.exists(), "drain left the marker behind — the .path unit would re-fire forever"
    assert "lumen-job-build-limpet-123: DONE:0" in ntfy_log.read_text()
    assert "lumen-job-build-limpet-123: DONE:0" in claude_log.read_text()
