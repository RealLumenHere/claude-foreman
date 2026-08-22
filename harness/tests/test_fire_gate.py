"""wq#717 (AUTO-5) fire_gate unit tests — zero quota, no subprocess. Covers the
documented failure modes (FIRE_MODE.md) for the fleet-fires manual-approval workflow:
malformed/missing approval, ticket-id reuse, bad token, plan drift, and the
diagnose/hold/apply/timeout_release decision table.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import fire_gate  # noqa: E402


def _wire(tmp_path, monkeypatch, token="s3cr3t-token"):
    monkeypatch.setattr(fire_gate, "PLANS_DIR", str(tmp_path / "plans"))
    monkeypatch.setattr(fire_gate, "APPROVALS_DIR", str(tmp_path / "approvals"))
    token_file = tmp_path / "fire.token"
    if token is not None:
        token_file.write_text(token)
    monkeypatch.setattr(fire_gate, "FIRE_TOKEN_FILE", str(token_file))
    return token


def _approve(tmp_path, tid, plan_text, token="s3cr3t-token", **overrides):
    body = {"ticket": tid, "plan_hash": fire_gate.sha256_text(plan_text), "token": token}
    body.update(overrides)
    fire_gate.write_plan(tid, plan_text)  # ensure plans dir exists
    os.makedirs(fire_gate.APPROVALS_DIR, exist_ok=True)
    with open(fire_gate.approval_path(tid), "w") as f:
        json.dump(body, f)


def test_no_plan_means_diagnose(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    action, info = fire_gate.next_action(717)
    assert action == "diagnose"
    assert info == {}


def test_plan_no_approval_holds(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    fire_gate.write_plan(717, "# plan\n")
    action, info = fire_gate.next_action(717)
    assert action == "hold"
    assert info["code"] == "missing"


def test_valid_approval_applies(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    plan = "# plan\ncurrent state: fine\n"
    fire_gate.write_plan(717, plan)
    _approve(tmp_path, 717, plan)
    action, info = fire_gate.next_action(717)
    assert action == "apply"
    assert info["plan_text"] == plan


def test_malformed_json_holds_not_applies(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    plan = "# plan\n"
    fire_gate.write_plan(717, plan)
    os.makedirs(fire_gate.APPROVALS_DIR, exist_ok=True)
    with open(fire_gate.approval_path(717), "w") as f:
        f.write("{not json")
    ok, code, _ = fire_gate.validate_approval(717, plan)
    assert not ok and code == "malformed_json"
    action, info = fire_gate.next_action(717)
    assert action == "hold"
    assert info["code"] == "malformed_json"


def test_missing_fields_rejected(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    plan = "# plan\n"
    fire_gate.write_plan(717, plan)
    os.makedirs(fire_gate.APPROVALS_DIR, exist_ok=True)
    with open(fire_gate.approval_path(717), "w") as f:
        json.dump({"ticket": 717}, f)
    ok, code, _ = fire_gate.validate_approval(717, plan)
    assert not ok and code == "missing_fields"


def test_ticket_mismatch_rejected(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    plan = "# plan\n"
    _approve(tmp_path, 717, plan)
    # Someone copy-pastes wq717's approval file onto wq718's approval path — reject.
    fire_gate.write_plan(718, plan)
    os.makedirs(fire_gate.APPROVALS_DIR, exist_ok=True)
    with open(fire_gate.approval_path(717)) as f:
        copied = f.read()
    with open(fire_gate.approval_path(718), "w") as f:
        f.write(copied)
    ok, code, _ = fire_gate.validate_approval(718, plan)
    assert not ok and code == "ticket_mismatch"


def test_bad_token_rejected(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch, token="real-token")
    plan = "# plan\n"
    _approve(tmp_path, 717, plan, token="guessed-token")
    ok, code, _ = fire_gate.validate_approval(717, plan)
    assert not ok and code == "bad_token"


def test_token_file_missing_fails_closed(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch, token=None)
    plan = "# plan\n"
    _approve(tmp_path, 717, plan, token="anything")
    ok, code, _ = fire_gate.validate_approval(717, plan)
    assert not ok and code == "token_file_missing"


def test_plan_drift_rejected(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    original = "# plan\ncurrent state: A\n"
    _approve(tmp_path, 717, original)
    # Plan gets rewritten (re-diagnosis) after Frank already approved the old text.
    fire_gate.write_plan(717, "# plan\ncurrent state: B\n")
    ok, code, _ = fire_gate.validate_approval(717, fire_gate.read_plan(717))
    assert not ok and code == "plan_drift"
    action, info = fire_gate.next_action(717)
    assert action == "hold"
    assert info["code"] == "plan_drift"


def test_timeout_releases_when_no_approval(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    fire_gate.write_plan(717, "# plan\n")
    plan_mtime = os.path.getmtime(fire_gate.plan_path(717))
    future = plan_mtime + 25 * 3600  # past the 24h default timeout
    action, info = fire_gate.next_action(717, now=future)
    assert action == "timeout_release"
    assert info["code"] == "missing"


def test_within_timeout_still_holds(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    fire_gate.write_plan(717, "# plan\n")
    plan_mtime = os.path.getmtime(fire_gate.plan_path(717))
    soon = plan_mtime + 1 * 3600  # 1h in, well under the 24h default
    action, _ = fire_gate.next_action(717, now=soon)
    assert action == "hold"


def test_custom_timeout_hours_respected(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    fire_gate.write_plan(717, "# plan\n")
    plan_mtime = os.path.getmtime(fire_gate.plan_path(717))
    action, _ = fire_gate.next_action(717, timeout_hours=1, now=plan_mtime + 2 * 3600)
    assert action == "timeout_release"


def test_clear_fire_state_removes_all_three(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    plan = "# plan\n"
    _approve(tmp_path, 717, plan)
    with open(fire_gate.drift_path(717), "w") as f:
        f.write("drift\n")
    fire_gate.clear_fire_state(717)
    assert fire_gate.read_plan(717) is None
    assert not os.path.exists(fire_gate.approval_path(717))
    assert not os.path.exists(fire_gate.drift_path(717))


def test_clear_fire_state_is_idempotent(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    fire_gate.clear_fire_state(999)  # nothing on disk — must not raise


def test_sha256_text_is_stable_and_sensitive():
    a = fire_gate.sha256_text("hello")
    b = fire_gate.sha256_text("hello")
    c = fire_gate.sha256_text("hello ")
    assert a == b
    assert a != c
