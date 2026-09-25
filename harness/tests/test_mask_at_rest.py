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
    projects root, gateway log dir, run-log dir or state file."""
    monkeypatch.setattr(mask_at_rest, "PROJECTS_ROOT", str(tmp_path / "projects"))
    monkeypatch.setattr(mask_at_rest, "GATEWAY_LOG_DIR", str(tmp_path / "gw"))
    monkeypatch.setattr(mask_at_rest, "RUN_LOG_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(mask_at_rest, "STATE_PATH", str(tmp_path / "state.json"))
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


def test_failed_offsite_backup_blocks_the_mask(tmp_path, monkeypatch):
    monkeypatch.setattr(mask_at_rest, "offsite_backup", lambda paths, tag: False)
    p = _write(str(tmp_path / "projects" / "proj-a" / "sess1.jsonl"),
              json.dumps({"text": f"token is {GHP}"}) + "\n")
    rc = mask_at_rest.main(["--store", "transcripts"])
    assert rc == 0   # nothing masked this pass -> no NEW unaccounted record emitted
    with open(p) as fh:
        assert GHP in fh.read()   # untouched: masking a value with no off-box copy
                                  # of the original would be a silent, unrecoverable loss


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
