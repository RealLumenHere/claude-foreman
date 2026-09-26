"""2026-09-26: mask_at_rest single-flight + the test-process guard on its trigger."""
import os
import subprocess
import sys

import fworker
import mask_at_rest


def test_second_run_skips_while_lock_is_held(tmp_path, monkeypatch):
    lock = str(tmp_path / "mask_at_rest.lock")
    monkeypatch.setattr(mask_at_rest, "LOCK_PATH", lock)
    held = mask_at_rest.acquire_lock()
    assert held is not None
    assert mask_at_rest.acquire_lock() is None          # busy -> None
    # main() on a busy lock exits 0 without scanning anything
    monkeypatch.setattr(mask_at_rest, "transcript_paths", lambda session=None: (_ for _ in ()).throw(AssertionError("scanned")))
    assert mask_at_rest.main(["--store", "transcripts"]) == 0
    held.close()
    assert mask_at_rest.acquire_lock() is not None      # released -> free again


def test_lock_dir_unwritable_fails_open(tmp_path, monkeypatch):
    monkeypatch.setattr(mask_at_rest, "LOCK_PATH", str(tmp_path / "nope.txt" / "lock"))
    (tmp_path / "nope.txt").write_text("a file, not a dir")
    assert mask_at_rest.acquire_lock() is not None


def test_trigger_is_a_noop_under_the_guard(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: calls.append(a))
    monkeypatch.setenv("FOREMAN_NO_MASK_AT_REST", "1")
    fworker.trigger_mask_at_rest("sess-1")
    assert calls == []
    monkeypatch.delenv("FOREMAN_NO_MASK_AT_REST")
    fworker.trigger_mask_at_rest("sess-1")
    assert len(calls) == 1
    argv = calls[0][0]
    assert argv[:3] == ["nice", "-n", "10"] and argv[3] == sys.executable
    assert argv[-2:] == ["--session", "sess-1"]


def test_suite_conftest_sets_the_guard():
    assert os.environ.get("FOREMAN_NO_MASK_AT_REST") == "1"
