"""rotation_vault_gate unit tests (wq#3747) — exercised with a dummy credential
value only, never a real one. Fully offline: no live `bw`/Vaultwarden needed,
because the module fails closed whenever BW_SESSION is absent — exactly the
behavior under test.
"""
import hashlib
import importlib
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rotation_vault_gate as gate  # noqa: E402

DUMMY_OLD = "dummy-test-credential-old-000111222"
DUMMY_NEW = "dummy-test-credential-new-333444555"


def test_fingerprint_is_sha256_12():
    assert gate.fingerprint(DUMMY_OLD) == hashlib.sha256(DUMMY_OLD.encode()).hexdigest()[:12]
    assert len(gate.fingerprint(DUMMY_OLD)) == 12


def test_require_backup_fails_closed_with_no_old_value():
    assert gate.require_backup("dummy-cred", "") is False
    assert gate.require_backup("dummy-cred", None) is False


def test_require_backup_fails_closed_without_bw_session(monkeypatch):
    monkeypatch.delenv("BW_SESSION", raising=False)
    assert gate.vault_backup_value("dummy-cred") is None
    assert gate.require_backup("dummy-cred", DUMMY_OLD) is False


def test_require_backup_true_on_matching_vault_value(monkeypatch):
    monkeypatch.setattr(gate, "vault_backup_value", lambda name: DUMMY_OLD)
    assert gate.require_backup("dummy-cred", DUMMY_OLD) is True


def test_require_backup_false_on_mismatched_vault_value(monkeypatch):
    monkeypatch.setattr(gate, "vault_backup_value", lambda name: "something-else-entirely")
    assert gate.require_backup("dummy-cred", DUMMY_OLD) is False


def test_require_backup_false_when_vault_lookup_fails(monkeypatch):
    monkeypatch.setattr(gate, "vault_backup_value", lambda name: None)
    assert gate.require_backup("dummy-cred", DUMMY_OLD) is False


def test_log_rotation_never_writes_plaintext(tmp_path, monkeypatch):
    log_path = tmp_path / "rotation-safety-gate.log"
    monkeypatch.setattr(gate, "LOG_PATH", str(log_path))
    gate.log_rotation("test-script", "dummy-cred", DUMMY_OLD, DUMMY_NEW, "rotated")
    text = log_path.read_text()
    assert DUMMY_OLD not in text and DUMMY_NEW not in text
    entry = json.loads(text.strip())
    assert entry["old_fp"] == gate.fingerprint(DUMMY_OLD)
    assert entry["new_fp"] == gate.fingerprint(DUMMY_NEW)
    assert entry["status"] == "rotated" and entry["script"] == "test-script" and entry["name"] == "dummy-cred"
    assert oct(os.stat(log_path).st_mode)[-3:] == "600"


def test_log_rotation_appends_multiple_entries(tmp_path, monkeypatch):
    log_path = tmp_path / "rotation-safety-gate.log"
    monkeypatch.setattr(gate, "LOG_PATH", str(log_path))
    gate.log_rotation("s1", "dummy-cred", DUMMY_OLD, DUMMY_NEW, "rotated")
    gate.log_rotation("s2", "dummy-cred", DUMMY_NEW, "another-new-value", "rotated")
    lines = log_path.read_text().strip().splitlines()
    assert len(lines) == 2


def test_require_backup_or_refuse_blocks_and_logs_without_plaintext(tmp_path, monkeypatch):
    log_path = tmp_path / "rotation-safety-gate.log"
    monkeypatch.setattr(gate, "LOG_PATH", str(log_path))
    monkeypatch.setattr(gate, "vault_backup_value", lambda name: None)
    assert gate.require_backup_or_refuse("rotate-fleet-pw.sh", "fleet-pw", DUMMY_OLD) is False
    entry = json.loads(log_path.read_text().strip())
    assert entry["status"] == "refused_no_vault_backup"
    assert entry["old_fp"] == gate.fingerprint(DUMMY_OLD) and entry["new_fp"] is None
    assert DUMMY_OLD not in log_path.read_text()


def test_require_backup_or_refuse_passes_with_matching_backup(monkeypatch):
    monkeypatch.setattr(gate, "vault_backup_value", lambda name: DUMMY_OLD)
    assert gate.require_backup_or_refuse("rotate-fleet-pw.sh", "fleet-pw", DUMMY_OLD) is True


def test_cli_require_backup_exit_codes(monkeypatch, capsys):
    monkeypatch.setattr(gate, "vault_backup_value", lambda name: DUMMY_OLD)
    monkeypatch.setattr(sys, "stdin", io.StringIO(DUMMY_OLD))
    assert gate._cli(["require-backup", "fleet-pw", "rotate-fleet-pw.sh"]) == 0

    monkeypatch.setattr(gate, "vault_backup_value", lambda name: None)
    monkeypatch.setattr(sys, "stdin", io.StringIO(DUMMY_OLD))
    assert gate._cli(["require-backup", "fleet-pw", "rotate-fleet-pw.sh"]) == 1


def test_cli_log_roundtrip(tmp_path, monkeypatch):
    log_path = tmp_path / "rotation-safety-gate.log"
    monkeypatch.setattr(gate, "LOG_PATH", str(log_path))
    monkeypatch.setattr(sys, "stdin", io.StringIO(f"{DUMMY_OLD}\n{DUMMY_NEW}"))
    assert gate._cli(["log", "rotate-fleet-pw.sh", "fleet-pw", "rotated"]) == 0
    entry = json.loads(log_path.read_text().strip())
    assert entry["old_fp"] == gate.fingerprint(DUMMY_OLD) and entry["new_fp"] == gate.fingerprint(DUMMY_NEW)


def test_cli_unrecognized_invocation_is_rejected():
    assert gate._cli([]) == 64
    assert gate._cli(["bogus"]) == 64
