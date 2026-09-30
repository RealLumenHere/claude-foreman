"""wq#2546: mask_at_rest.py masks CC transcripts + the supergateway gateway log in
place, backing the pre-mask bytes off-box first. Tests stub offsite_backup() and
the rotated ledger rather than hitting the network or a real restic repo — the
off-box call itself is exercised for its CONTRACT (called before any write,
blocks the write on failure), not its transport.

Fixtures are split at the credential prefix, same convention as test_masking.py:
this file must not itself carry a live-shaped value uninterrupted, or reading it
arms the wq#2275 tripwire on whatever process reads it next.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import mask_at_rest  # noqa: E402
import masking  # noqa: E402

GHP = "ghp_" + "A1b2C3d4E5f6G7h8J9k0A1b2C3d4E5f6G7h8"
AKIA = "AKIA" + "ABCDEFGHIJKLMNOP"


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Every path this module touches lives under tmp_path — never the real
    projects root, gateway log dir, run-log dir, state file, or (wq#3164) the
    production single-flight lock: without this last one every test here shares
    /home/lumen/foreman/runtime/mask_at_rest.lock with whatever real
    lumen-mask-at-rest.timer/fworker run is live on the box, which is fine for a
    single main() call per test (the lock is released when it returns) but
    collides the instant a test calls main() twice to exercise the incremental
    scan across two passes."""
    monkeypatch.setattr(mask_at_rest, "PROJECTS_ROOT", str(tmp_path / "projects"))
    monkeypatch.setattr(mask_at_rest, "GATEWAY_LOG_DIR", str(tmp_path / "gw"))
    monkeypatch.setattr(mask_at_rest, "RUN_LOG_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(mask_at_rest, "STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.setattr(mask_at_rest, "LOCK_PATH", str(tmp_path / "mask_at_rest.lock"))
    return tmp_path


def _write(path, text, mode=0o600):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(text)
    os.chmod(path, mode)
    return path


def test_transcript_with_hit_is_masked_and_backed_up(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(mask_at_rest, "offsite_backup",
                        lambda paths, tag: calls.append((tuple(paths), tag)) or True)
    p = _write(str(tmp_path / "projects" / "proj-a" / "sess1.jsonl"),
              json.dumps({"text": f"token is {GHP}"}) + "\n")
    rc = mask_at_rest.main(["--store", "transcripts"])
    assert rc == 1   # unaccounted (not in the empty rotated ledger)
    assert calls and calls[0][0] == (p,)
    with open(p) as fh:
        out = fh.read()
    assert GHP not in out
    assert "ghp_MASKED" in out
    assert json.loads(out)   # still valid JSON — line-wise masking preserved it


def test_clean_file_is_untouched_and_not_backed_up(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(mask_at_rest, "offsite_backup",
                        lambda paths, tag: calls.append(paths) or True)
    p = _write(str(tmp_path / "projects" / "proj-a" / "sess1.jsonl"),
              json.dumps({"text": "nothing secret here"}) + "\n")
    before = os.stat(p).st_mtime
    rc = mask_at_rest.main(["--store", "transcripts"])
    assert rc == 0
    assert calls == []
    assert os.stat(p).st_mtime == before


def test_failed_offsite_backup_blocks_the_mask_and_pages_aq(tmp_path, monkeypatch):
    """wq#3164: a failed offsite backup used to make process_store return an empty
    (0, [], []) -- indistinguishable from "nothing to do" -- so main() exited 0 and
    283 held-unmasked transcripts sat with no signal until an audit found them.
    Confirms both halves: the file is still untouched (no off-box copy exists yet,
    so masking it would be an unrecoverable loss) AND the pass now files an aq row
    and exits non-zero instead of silently succeeding."""
    monkeypatch.setattr(mask_at_rest, "offsite_backup", lambda paths, tag: False)
    aq_stub = tmp_path / "fake-aq"
    aq_stub.write_text("")
    monkeypatch.setattr(mask_at_rest, "AQ_BIN", str(aq_stub))
    aq_calls = []
    monkeypatch.setattr(mask_at_rest.subprocess, "run",
                        lambda cmd, **kw: aq_calls.append(cmd))
    p = _write(str(tmp_path / "projects" / "proj-a" / "sess1.jsonl"),
              json.dumps({"text": f"token is {GHP}"}) + "\n")
    rc = mask_at_rest.main(["--store", "transcripts"])
    assert rc == 1   # held-unmasked > 0 must not exit 0
    with open(p) as fh:
        assert GHP in fh.read()   # untouched: masking a value with no off-box copy
                                  # of the original would be a silent, unrecoverable loss
    assert aq_calls, "held-unmasked file(s) must file an aq row, not exit silently"
    cmd = aq_calls[0]
    assert cmd[:3] == [str(aq_stub), "add", mask_at_rest.AQ_SOURCE]
    assert p in cmd[5]   # the held path is named in the aq row body


def test_dry_run_masks_nothing_and_never_calls_backup(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(mask_at_rest, "offsite_backup",
                        lambda paths, tag: calls.append(paths) or True)
    p = _write(str(tmp_path / "projects" / "proj-a" / "sess1.jsonl"),
              json.dumps({"text": f"key is {AKIA}"}) + "\n")
    rc = mask_at_rest.main(["--store", "transcripts", "--dry-run"])
    assert rc == 1
    assert calls == []
    with open(p) as fh:
        assert AKIA in fh.read()


def test_session_filter_scopes_to_one_transcript(tmp_path, monkeypatch):
    monkeypatch.setattr(mask_at_rest, "offsite_backup", lambda paths, tag: True)
    hit = _write(str(tmp_path / "projects" / "proj-a" / "target.jsonl"),
                json.dumps({"text": f"token is {GHP}"}) + "\n")
    other = _write(str(tmp_path / "projects" / "proj-a" / "other.jsonl"),
                  json.dumps({"text": f"token is {GHP}"}) + "\n")
    rc = mask_at_rest.main(["--store", "transcripts", "--session", "target"])
    assert rc == 1
    with open(hit) as fh:
        assert GHP not in fh.read()
    with open(other) as fh:
        assert GHP in fh.read()   # untouched: outside the requested session scope


def test_already_rotated_value_masks_but_does_not_page(tmp_path, monkeypatch):
    monkeypatch.setattr(mask_at_rest, "offsite_backup", lambda paths, tag: True)
    paged = []
    monkeypatch.setattr(mask_at_rest, "page_alarm",
                        lambda unaccounted, run_id: paged.append(unaccounted))
    sha12 = masking.value_sha12(GHP[len("ghp_"):])   # scan() hashes the value AFTER
                                                       # the kept prefix, not the whole match
    monkeypatch.setattr(mask_at_rest.mask_replay, "load_rotated_ledger",
                        lambda: {sha12: {"ticket": "wq#0000"}})
    p = _write(str(tmp_path / "projects" / "proj-a" / "sess1.jsonl"),
              json.dumps({"text": f"token is {GHP}"}) + "\n")
    rc = mask_at_rest.main(["--store", "transcripts"])
    assert rc == 0   # rotated -> not unaccounted
    with open(p) as fh:
        assert GHP not in fh.read()   # masked regardless of rotation state
    assert paged == [[]]


def test_gzip_rotated_gateway_log_round_trips(tmp_path, monkeypatch):
    import gzip
    monkeypatch.setattr(mask_at_rest, "offsite_backup", lambda paths, tag: True)
    p = str(tmp_path / "gw" / "desktop-commander-mcp.log.2.gz")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with gzip.open(p, "wt") as fh:
        fh.write(f"child stdout: key={AKIA}\n")
    rc = mask_at_rest.main(["--store", "supergateway"])
    assert rc == 1
    with gzip.open(p, "rt") as fh:
        out = fh.read()
    assert AKIA not in out
    assert "AKIAMASKED" in out


def test_no_files_found_exits_2(tmp_path):
    rc = mask_at_rest.main(["--store", "supergateway"])
    assert rc == 2


def test_run_log_written_only_when_hits_found(tmp_path, monkeypatch):
    monkeypatch.setattr(mask_at_rest, "offsite_backup", lambda paths, tag: True)
    _write(str(tmp_path / "projects" / "proj-a" / "sess1.jsonl"),
          json.dumps({"text": f"token is {GHP}"}) + "\n")
    mask_at_rest.main(["--store", "transcripts"])
    assert os.path.isdir(str(tmp_path / "runs"))
    files = os.listdir(str(tmp_path / "runs"))
    assert len(files) == 1
    with open(os.path.join(str(tmp_path / "runs"), files[0])) as fh:
        rec = json.loads(fh.readline())
    assert rec["label"] == "github"
    assert "value" not in rec and GHP not in json.dumps(rec)   # hash-only, never the value


def test_offsite_backup_resolves_restic_by_absolute_path_under_minimal_path(
        tmp_path, monkeypatch):
    """wq#3164: harness/systemd/lumen-mask-at-rest.service sets no Environment=PATH=,
    so the unit runs under systemd's own default $PATH (systemd.exec(5):
    /usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin), which does not
    include ~/.local/bin where restic is actually installed on this box. Reproduces
    that PATH directly (not this pytest process's own, which may well have
    ~/.local/bin on it) and asserts the invoked argv[0] is still the absolute
    RESTIC_BIN, not a bare "restic" that would need a $PATH lookup to resolve."""
    systemd_default_path = (
        "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")
    assert os.path.dirname(mask_at_rest.RESTIC_BIN) not in systemd_default_path.split(":")
    monkeypatch.setenv("PATH", systemd_default_path)
    calls = []

    class _FakeProc:
        returncode = 0
        stderr = ""

    def fake_run(cmd, env=None, **kwargs):
        calls.append((cmd, env.get("PATH") if env else None))
        return _FakeProc()

    monkeypatch.setattr(mask_at_rest.subprocess, "run", fake_run)
    ok = mask_at_rest.offsite_backup([str(tmp_path / "f.jsonl")], tag="t")
    assert ok
    cmd, path_used = calls[0]
    assert cmd[0] == mask_at_rest.RESTIC_BIN
    assert os.path.isabs(cmd[0])
    assert path_used == systemd_default_path   # env is inherited verbatim, not widened


def test_unchanged_clean_file_is_not_reread_on_second_pass(tmp_path, monkeypatch):
    """wq#3164: the nightly sweep used to re-read and re-scan every transcript every
    run (1h08m CPU, 5.3G peak on the 2026-09-29 run) even though a file with zero
    hits is never written. A file recorded clean in STATE_PATH must cost one stat,
    not a full read+scan, on the next pass its mtime hasn't moved."""
    reads = []
    real_read = mask_at_rest._read_text
    monkeypatch.setattr(mask_at_rest, "_read_text",
                        lambda p: reads.append(p) or real_read(p))
    p = _write(str(tmp_path / "projects" / "proj-a" / "sess1.jsonl"),
              "nothing secret here\n")
    rc1 = mask_at_rest.main(["--store", "transcripts"])
    assert rc1 == 0
    assert reads == [p]
    reads.clear()
    rc2 = mask_at_rest.main(["--store", "transcripts"])
    assert rc2 == 0
    assert reads == []   # unchanged since the first pass -> skipped, not reread


def test_touched_file_is_rescanned_after_being_recorded_clean(tmp_path, monkeypatch):
    monkeypatch.setattr(mask_at_rest, "offsite_backup", lambda paths, tag: True)
    p = _write(str(tmp_path / "projects" / "proj-a" / "sess1.jsonl"),
              "nothing secret here\n")
    assert mask_at_rest.main(["--store", "transcripts"]) == 0
    future = os.stat(p).st_mtime + 5
    _write(p, json.dumps({"text": f"token is {GHP}"}) + "\n")
    os.utime(p, (future, future))
    rc = mask_at_rest.main(["--store", "transcripts"])
    assert rc == 1   # picked back up once mtime moved past the recorded watermark
    with open(p) as fh:
        assert GHP not in fh.read()


def test_held_unmasked_file_is_retried_on_the_next_pass(tmp_path, monkeypatch):
    """A file whose backup failed must never enter STATE_PATH as settled -- the
    incremental bound (wq#3164) must not turn 'held unmasked, retry next run' into
    'skipped forever because mtime hasn't changed'."""
    backup_ok = [False]
    monkeypatch.setattr(mask_at_rest, "offsite_backup",
                        lambda paths, tag: backup_ok[0])
    p = _write(str(tmp_path / "projects" / "proj-a" / "sess1.jsonl"),
              json.dumps({"text": f"token is {GHP}"}) + "\n")
    assert mask_at_rest.main(["--store", "transcripts"]) == 1
    with open(p) as fh:
        assert GHP in fh.read()   # still held unmasked, mtime unchanged
    backup_ok[0] = True
    rc = mask_at_rest.main(["--store", "transcripts"])
    assert rc == 1   # now masked (new unaccounted record for a fresh hit this pass)
    with open(p) as fh:
        assert GHP not in fh.read()


def test_offsite_backup_timeout_covers_repo_open_cost(monkeypatch):
    """Measured 2026-09-30: restic opens the ~500G offsite repo in ~2 min, so the old
    120s cap timed out every batch regardless of size. The cap handed to subprocess must
    stay well above that, and must come from RESTIC_TIMEOUT_S."""
    assert mask_at_rest.RESTIC_TIMEOUT_S >= 900
    seen = {}

    class _Done:
        returncode = 0
        stderr = ""

    def fake_run(cmd, **kw):
        seen["timeout"] = kw.get("timeout")
        return _Done()

    monkeypatch.setattr(mask_at_rest.subprocess, "run", fake_run)
    assert mask_at_rest.offsite_backup(["/tmp/x"], tag="t") is True
    assert seen["timeout"] == mask_at_rest.RESTIC_TIMEOUT_S
