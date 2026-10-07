"""wq#3576 — `install.sh --verify` per ops/VERIFY_CONTRACT.md.

ssh is replaced by a fake on PATH that answers the per-feed Miniflux SELECT with
whatever FAKE_ROW says ("<parsing_error_count> <newest_entry_age_s>", empty for
not subscribed); nothing touches Sia.
"""
import os
import stat
import subprocess
from pathlib import Path

BUNDLE = Path(__file__).resolve().parent

FAKE_SSH = """#!/bin/sh
# last arg is the remote command: `true` for vremote_up, the psql SELECT otherwise
for a; do last="$a"; done
echo "$last" >> "$FAKE_LOG"
[ "$FAKE_SIA_UP" = 1 ] || exit 255
case "$last" in
  true) exit 0 ;;
  *psql*)
    [ "$FAKE_DB_UP" = 1 ] || exit 1
    case "$last" in *"$FAKE_ROW_URL"*) [ -n "$FAKE_ROW" ] && echo "$FAKE_ROW" ;;
                    *) echo "0 60" ;; esac
    exit 0 ;;
esac
exit 99
"""

ALL = "daily/all.xml"
PY = "daily/python.xml"


def run(tmp_path, *args, up=True, db_up=True, row="0 60", row_url=ALL, extra_env=None):
    d = tmp_path / "fakebin"
    d.mkdir(exist_ok=True)
    ssh = d / "ssh"
    ssh.write_text(FAKE_SSH)
    ssh.chmod(ssh.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "ssh.log"
    env = {"PATH": f"{d}:/usr/bin:/bin", "HOME": str(tmp_path), "FAKE_LOG": str(log),
           "FAKE_SIA_UP": "1" if up else "0", "FAKE_DB_UP": "1" if db_up else "0",
           "FAKE_ROW": row, "FAKE_ROW_URL": row_url, **(extra_env or {})}
    proc = subprocess.run(["bash", str(BUNDLE / "install.sh"), *args],
                          capture_output=True, text=True, env=env)
    proc.ssh_log = log.read_text() if log.exists() else ""
    return proc


def test_handler_precedes_the_install_step():
    text = (BUNDLE / "install.sh").read_text()
    assert text.index('if [ "${1:-}" = --verify ]; then') < text.index("miniflux_subscribe.py\"")


def test_verify_ok_when_every_feed_is_subscribed_and_fresh(tmp_path):
    proc = run(tmp_path, "--verify")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "VERIFY OK" in proc.stdout
    # one query per feeds.txt entry, each naming its own url, read-only
    assert proc.ssh_log.count("psql") == 2
    assert ALL in proc.ssh_log and PY in proc.ssh_log
    for verb in ("INSERT", "UPDATE", "DELETE"):
        assert verb not in proc.ssh_log


def test_verify_misses_an_unsubscribed_feed(tmp_path):
    proc = run(tmp_path, "--verify", row="", row_url=PY)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "not subscribed in Miniflux" in proc.stdout and PY in proc.stdout
    assert proc.stdout.count("MISS") == 1


def test_verify_misses_a_feed_that_stopped_capturing(tmp_path):
    proc = run(tmp_path, "--verify", row=f"0 {49 * 3600}")
    assert proc.returncode == 1
    assert "no entry captured in 48h" in proc.stdout


def test_verify_misses_a_subscribed_feed_with_no_entries_yet(tmp_path):
    proc = run(tmp_path, "--verify", row="0 -1")
    assert proc.returncode == 1
    assert "no entry captured" in proc.stdout


def test_verify_misses_parsing_errors(tmp_path):
    proc = run(tmp_path, "--verify", row="4 60")
    assert proc.returncode == 1
    assert "parsing errors (4)" in proc.stdout


def test_unreachable_sia_is_err_not_miss(tmp_path):
    proc = run(tmp_path, "--verify", up=False)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "ERR unreachable" in proc.stdout
    assert "MISS" not in proc.stdout


def test_failed_db_query_is_err_not_miss(tmp_path):
    proc = run(tmp_path, "--verify", db_up=False)
    assert proc.returncode == 2
    assert "miniflux query failed" in proc.stdout
    assert "MISS" not in proc.stdout


def test_install_refuses_without_miniflux_env(tmp_path):
    proc = run(tmp_path)
    assert proc.returncode == 2
    assert "MINIFLUX_BASE_URL" in proc.stderr
