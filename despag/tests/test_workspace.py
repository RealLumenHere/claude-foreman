"""wq#1810: despag P1-5 — workspace = git worktree per session, checkpoint tar,
mtime TTL, refs/lumen/collected cursor, D.1 workspace_snapshot frame.

Acceptance (per the ticket): TTL reaps only stale+unleased dirs; checkpoint excludes
credentials; cursor advances only on ack; a crash between capture and ack
re-produces the same delta; C-01 continuation carries previous snapshot."""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time

from despag.backing import FileBlobStore
from despag.backing.control_sqlite import ControlStore
from despag import workspace as ws

HARNESS = {"repo": "scratch"}


def run(coro):
    return asyncio.run(coro)


def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("DESPAG_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("DESPAG_WORKTREES_ROOT", str(tmp_path / "worktrees"))


def _control(tmp_path) -> ControlStore:
    return ControlStore(str(tmp_path / "control.db"))


def _blob(tmp_path) -> FileBlobStore:
    return FileBlobStore(str(tmp_path / "blobs"))


# ── identity ─────────────────────────────────────────────────────────────────
def test_sanitize_sid_substitutes_unsafe_chars():
    assert ws.sanitize_sid("a/b c*d") == "a_b_c_d"
    assert ws.sanitize_sid("") == "_default"
    assert ws.sanitize_sid("plain-id.1") == "plain-id.1"


def test_ensure_workspace_is_a_worktree_of_the_declared_repo(tmp_path, monkeypatch):
    _env(monkeypatch, tmp_path)
    control = _control(tmp_path)
    path = run(ws.ensure_workspace("sid-repo", HARNESS, control))
    assert path.is_dir()
    assert (path / ".git").exists()  # worktree pointer file
    head = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    assert head


# ── TTL reap ─────────────────────────────────────────────────────────────────
def test_reap_stale_reaps_only_stale_and_unleased(tmp_path, monkeypatch):
    _env(monkeypatch, tmp_path)
    control = _control(tmp_path)
    fresh = run(ws.ensure_workspace("fresh", HARNESS, control))
    stale_unleased = run(ws.ensure_workspace("stale-unleased", HARNESS, control))
    stale_leased = run(ws.ensure_workspace("stale-leased", HARNESS, control))

    old = time.time() - 1000 * 3600
    os.utime(stale_unleased, (old, old))
    os.utime(stale_leased, (old, old))
    run(control.lease_admit("stale-leased", "turn-owner", ttl_s=3600))

    reaped = run(ws.reap_stale(control, ttl_hours=72))

    assert reaped == ["stale-unleased"]
    assert fresh.exists()
    assert stale_leased.exists()
    assert not stale_unleased.exists()


def test_ensure_workspace_raises_session_expired_after_reap(tmp_path, monkeypatch):
    _env(monkeypatch, tmp_path)
    control = _control(tmp_path)
    run(ws.ensure_workspace("gone", HARNESS, control))
    ws.remove_workspace("gone")
    try:
        run(ws.ensure_workspace("gone", HARNESS, control))
        assert False, "expected SessionExpired"
    except ws.SessionExpired:
        pass


# ── checkpoint / rehydrate ───────────────────────────────────────────────────
def test_checkpoint_excludes_credentials(tmp_path, monkeypatch):
    _env(monkeypatch, tmp_path)
    control = _control(tmp_path)
    blob = _blob(tmp_path)
    path = run(ws.ensure_workspace("sid-ckpt", HARNESS, control))
    (path / ".credentials.json").write_text("super-secret")
    (path / ".claude").mkdir()
    (path / ".claude" / "config.json").write_text("also secret")
    (path / "keep.txt").write_text("real work product")

    result = run(ws.checkpoint("sid-ckpt", blob))
    assert result["sha256"]

    data = run(blob.get("sessions", "sid-ckpt/workspace.tgz"))
    assert data is not None
    tar_path = tmp_path / "inspect.tgz"
    tar_path.write_bytes(data)
    names = subprocess.run(["tar", "tzf", str(tar_path)], capture_output=True, text=True).stdout

    assert ".credentials.json" not in names
    assert ".claude" not in names
    assert "keep.txt" in names


def test_rehydrate_probe_short_circuits_without_refetch(tmp_path, monkeypatch):
    _env(monkeypatch, tmp_path)
    control = _control(tmp_path)
    blob = _blob(tmp_path)
    run(ws.ensure_workspace("sid-probe", HARNESS, control))
    result = run(ws.checkpoint("sid-probe", blob))

    outcome = run(ws.rehydrate("sid-probe", HARNESS, control, blob, probe=result["sha256"]))
    assert outcome["skipped"] is True
    assert outcome["restored"] is False


# ── collection cursor (E.4) ──────────────────────────────────────────────────
def test_cursor_advances_only_on_ack(tmp_path, monkeypatch):
    _env(monkeypatch, tmp_path)
    control = _control(tmp_path)
    blob = _blob(tmp_path)
    path = run(ws.ensure_workspace("sid-cursor", HARNESS, control))

    def cursor_sha():
        p = subprocess.run(["git", "-C", str(path), "rev-parse", "-q", "--verify", "refs/lumen/collected"],
                           capture_output=True, text=True)
        return p.stdout.strip() or None

    (path / "turn1.txt").write_text("turn 1 work")
    ws.build_snapshot("sid-cursor", "resp_1")   # reads only; must not move the cursor
    init_head = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"],
                               capture_output=True, text=True).stdout.strip()
    assert cursor_sha() == init_head            # collected_init pinned it at the FIRST HEAD

    run(ws.checkpoint("sid-cursor", blob))       # checkpoint's own commit moves HEAD...
    assert cursor_sha() == init_head             # ...but must NOT move the cursor (E.4)

    new_head = ws.advance_cursor("sid-cursor")   # only the ack moves it
    assert cursor_sha() == new_head
    assert new_head != init_head


# ── D.1 workspace_snapshot ───────────────────────────────────────────────────
def test_crash_between_capture_and_ack_reproduces_same_delta(tmp_path, monkeypatch):
    _env(monkeypatch, tmp_path)
    control = _control(tmp_path)
    blob = _blob(tmp_path)
    path = run(ws.ensure_workspace("sid-crash", HARNESS, control))
    (path / "output.txt").write_text("turn output")

    frame1 = run(ws.capture_and_store_snapshot("sid-crash", "resp_1", blob))
    # simulate a crash: advance_cursor is never called for resp_1's turn.
    frame2 = run(ws.capture_and_store_snapshot("sid-crash", "resp_1_retry", blob))

    assert frame1["diff"] == frame2["diff"]
    assert frame1["status_porcelain"] == frame2["status_porcelain"]
    assert frame1["file_tree_delta"] == frame2["file_tree_delta"]


def test_previous_snapshot_carries_forward_exactly_one(tmp_path, monkeypatch):
    _env(monkeypatch, tmp_path)
    control = _control(tmp_path)
    blob = _blob(tmp_path)
    path = run(ws.ensure_workspace("sid-c01", HARNESS, control))

    assert run(ws.previous_snapshot("sid-c01", blob)) is None

    (path / "a.txt").write_text("a")
    run(ws.capture_and_store_snapshot("sid-c01", "resp_1", blob, assistant_texts=["did a"]))
    ws.advance_cursor("sid-c01")
    prev = run(ws.previous_snapshot("sid-c01", blob))
    assert prev["response_id"] == "resp_1"

    (path / "b.txt").write_text("b")
    run(ws.capture_and_store_snapshot("sid-c01", "resp_2", blob, assistant_texts=["did b"]))
    prev2 = run(ws.previous_snapshot("sid-c01", blob))
    assert prev2["response_id"] == "resp_2"   # never resends the older resp_1 snapshot


def test_build_snapshot_caps_total_size_and_marks_truncated(tmp_path, monkeypatch):
    _env(monkeypatch, tmp_path)
    control = _control(tmp_path)
    path = run(ws.ensure_workspace("sid-big", HARNESS, control))
    (path / "big.txt").write_text("seed\n")
    subprocess.run(["git", "-C", str(path), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", "seed"], check=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})
    (path / "big.txt").write_text("x" * (200 * 1024) + "\n")

    frame = ws.build_snapshot("sid-big", "resp_1")

    assert frame["truncated"] is True
    assert len(json.dumps(frame).encode()) <= ws._SNAPSHOT_MAX_BYTES
