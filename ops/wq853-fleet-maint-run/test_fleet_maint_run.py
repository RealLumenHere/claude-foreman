"""fleet_maint_run.py -- ordered rolling executor with G0-G7 gates (wq#853).

Every item command is a real shell string (true/false/echo/exit N) run through the
real run_cmd -- there is no fleet to fake, only shell exit codes and stdout. External
integrations the runner shells out to in phase 3b (wq, ntfy, state-regen, episode
commit) are pointed at a tiny fake script that logs its argv and exits 0, mirroring
the fake_lumen_run.py pattern harness/tests already uses.
"""
import hashlib
import json
import os
import stat
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fleet_maint_run as fmr  # noqa: E402


FAKE_EXTERNAL = os.path.join(HERE, "fake_external.sh")


def write_batch(tmp_path, items, window_date="2026-09-12", preflight=None):
    batch = {"window_date": window_date, "items": items}
    if preflight is not None:
        batch["preflight"] = preflight
    path = tmp_path / "batch.json"
    path.write_text(json.dumps(batch, indent=2))
    raw = path.read_bytes()
    return str(path), hashlib.sha256(raw).hexdigest()


def base_item(id, node="gameboy", **overrides):
    item = {
        "id": id,
        "lane": "APP",
        "node": node,
        "component": f"comp{id}",
        "current_version": "1.0",
        "target_version": "2.0",
        "apply_cmd": "true",
        "pre_state_cmd": "echo pre-state",
        "rollback_cmd": "true",
        "rollback_dryrun_cmd": "true",
        "health_probe_cmd": "true",
    }
    item.update(overrides)
    return item


@pytest.fixture(autouse=True)
def fast_and_faked(tmp_path, monkeypatch):
    """Zero-second G5 waits and every external binary replaced with a logging fake --
    no test should sleep 60/300s or touch a real wq/ntfy/state-regen/episode tool."""
    call_log = tmp_path / "calls.log"
    script = f"#!/usr/bin/env bash\necho \"$@\" >> {call_log}\nexit 0\n"
    with open(FAKE_EXTERNAL, "w") as f:
        f.write(script)
    os.chmod(FAKE_EXTERNAL, os.stat(FAKE_EXTERNAL).st_mode | stat.S_IEXEC)

    monkeypatch.setattr(fmr, "WQ_BIN", FAKE_EXTERNAL)
    monkeypatch.setattr(fmr, "NTFY_BIN", FAKE_EXTERNAL)
    monkeypatch.setattr(fmr, "STATE_REGEN_BIN", FAKE_EXTERNAL)
    # EPISODE_BIN is invoked as [sys.executable, EPISODE_BIN, ...] -- point it at a
    # trivial python script so `python3 <script> commit ...` exits 0.
    fake_py = tmp_path / "fake_episode.py"
    fake_py.write_text("import sys\nsys.exit(0)\n")
    monkeypatch.setattr(fmr, "EPISODE_BIN", str(fake_py))

    yield call_log
    os.remove(FAKE_EXTERNAL)


def run(tmp_path, items, window_date="2026-09-12", preflight=None, wq_ticket=None):
    batch_path, sha = write_batch(tmp_path, items, window_date=window_date, preflight=preflight)
    registry = tmp_path / "registry.json"
    report_dir = tmp_path / "reports"
    return fmr.run_batch(batch_path, sha, registry_path=str(registry), report_dir=str(report_dir),
                          wq_ticket=wq_ticket, sleep_fn=lambda s: None, g5_t1=0, g5_t2=0), registry, report_dir


# ---- G0 ----------------------------------------------------------------------------------

def test_g0_sha256_mismatch_aborts_whole_run_and_still_records(tmp_path):
    batch_path, _real_sha = write_batch(tmp_path, [base_item(1)])
    registry = tmp_path / "registry.json"
    report_dir = tmp_path / "reports"
    result = fmr.run_batch(batch_path, "0" * 64, registry_path=str(registry),
                            report_dir=str(report_dir), sleep_fn=lambda s: None)
    assert result["status"] == "ABORTED_PREFLIGHT"
    assert "G0 FAILED" in result["stop_reason"]
    assert result["items"] == []
    # phase 3b still ran: a report exists even though nothing was touched
    report_path = os.path.join(str(report_dir), "maint_window_unknown-date.md")
    assert os.path.exists(report_path)


# ---- batch validation --------------------------------------------------------------------

def test_missing_rollback_cmd_aborts_before_any_mutation(tmp_path):
    bad = base_item(1)
    del bad["rollback_cmd"]
    result, _reg, _rd = run(tmp_path, [bad])
    assert result["status"] == "ABORTED_PREFLIGHT"
    assert "rollback_cmd" in result["stop_reason"]
    assert result["items"] == []


def test_unknown_node_aborts_before_any_mutation(tmp_path):
    result, _reg, _rd = run(tmp_path, [base_item(1, node="not-a-fleet-node")])
    assert result["status"] == "ABORTED_PREFLIGHT"
    assert "ordering law" in result["stop_reason"]


def test_missing_health_probe_aborts_before_any_mutation(tmp_path):
    bad = base_item(1)
    del bad["health_probe_cmd"]
    result, _reg, _rd = run(tmp_path, [bad])
    assert result["status"] == "ABORTED_PREFLIGHT"
    assert "health_probe_cmd" in result["stop_reason"]


def test_requires_backup_without_precondition_cmd_aborts(tmp_path):
    bad = base_item(1, requires_backup=True)
    result, _reg, _rd = run(tmp_path, [bad])
    assert result["status"] == "ABORTED_PREFLIGHT"
    assert "backup_precondition_cmd" in result["stop_reason"]


# ---- ordering law (A11 section 5) ---------------------------------------------------------

def test_ordering_law_leaf_service_sobek_maat_lumen(tmp_path):
    items = [
        base_item(1, node="lumen"),
        base_item(2, node="maat"),
        base_item(3, node="sobek"),
        base_item(4, node="anubis"),
        base_item(5, node="sia"),
    ]
    result, _reg, _rd = run(tmp_path, items)
    assert result["status"] == "COMPLETED"
    order = [r["node"] for r in result["items"]]
    assert order == ["sia", "anubis", "sobek", "maat", "lumen"]


def test_reboot_runs_after_package_items_on_same_node(tmp_path):
    items = [
        base_item(1, node="sia", reboot=True, consent=True, rollback_cmd=None),
        base_item(2, node="sia"),
    ]
    # reboot items don't need rollback_cmd/pre_state_cmd/rollback_dryrun_cmd
    items[0].pop("rollback_cmd", None)
    items[0].pop("pre_state_cmd", None)
    items[0].pop("rollback_dryrun_cmd", None)
    result, _reg, _rd = run(tmp_path, items)
    assert result["status"] == "COMPLETED"
    ids_in_order = [r["id"] for r in result["items"]]
    assert ids_in_order == [2, 1]  # package item (id 2) before the reboot (id 1)


# ---- stop-on-first-failure -----------------------------------------------------------------

def test_apply_failure_triggers_rollback_and_stops_run(tmp_path):
    items = [
        base_item(1, node="gameboy"),
        base_item(2, node="apep", apply_cmd="false"),
        base_item(3, node="thoth"),
    ]
    result, registry, _rd = run(tmp_path, items)
    assert result["status"] == "STOPPED"
    statuses = {r["id"]: r["status"] for r in result["items"]}
    assert statuses[1] == "OK"
    assert statuses[2] == "ROLLED_BACK"
    assert statuses[3] == "NOT_ATTEMPTED"
    # the failed item's paired restore was cleared by its own rollback path never
    # having reached G7 -- but it also never leaks a stale registration for item 1,
    # which completed cleanly.
    assert json.loads(registry.read_text()) == {}


def test_g5_health_probe_failure_triggers_rollback_and_stops_run(tmp_path):
    items = [base_item(1, health_probe_cmd="false")]
    result, _reg, _rd = run(tmp_path, items)
    assert result["status"] == "STOPPED"
    assert result["items"][0]["status"] == "ROLLED_BACK"
    assert "G5" in result["items"][0]["detail"]


def test_rollback_command_itself_failing_is_flagged_for_manual_intervention(tmp_path):
    items = [base_item(1, apply_cmd="false", rollback_cmd="false")]
    result, _reg, _rd = run(tmp_path, items)
    assert result["items"][0]["status"] == "ROLLBACK_FAILED"
    assert "MANUAL INTERVENTION" in result["items"][0]["detail"]


def test_g6_new_failed_unit_triggers_rollback(tmp_path):
    # failed_units_cmd returns nothing at G1 (baseline empty), then a unit name at G6
    marker = str(HERE) + "/.g6-marker-does-not-exist"
    fu_cmd = f"test -f {marker} && echo broken.service || true"
    items = [base_item(1, failed_units_cmd=fu_cmd,
                        apply_cmd=f"touch {marker}")]
    try:
        result, _reg, _rd = run(tmp_path, items)
        assert result["items"][0]["status"] == "ROLLED_BACK"
        assert "G6" in result["items"][0]["detail"]
    finally:
        if os.path.exists(marker):
            os.remove(marker)


# ---- ABSENT and reboot consent -------------------------------------------------------------

def test_absent_item_is_recorded_not_skipped_and_run_continues(tmp_path):
    items = [
        {"id": 1, "node": "hathor", "component": "tailscale", "absent": True},
        base_item(2, node="lumen"),
    ]
    result, _reg, _rd = run(tmp_path, items)
    assert result["status"] == "COMPLETED"
    statuses = {r["id"]: r["status"] for r in result["items"]}
    assert statuses[1] == "ABSENT"
    assert statuses[2] == "OK"


def test_reboot_without_consent_is_fail_closed(tmp_path):
    item = base_item(1, node="lumen", reboot=True, consent=False)
    for f in ("rollback_cmd", "pre_state_cmd", "rollback_dryrun_cmd"):
        item.pop(f, None)
    result, _reg, _rd = run(tmp_path, [item])
    assert result["status"] == "STOPPED"
    assert result["items"][0]["status"] == "ABORTED"
    assert result["items"][0]["gate_failed"] == "CONSENT"


# ---- replication lag gate (Ma'at) -----------------------------------------------------------

def test_maat_replication_lag_gate_blocks_item(tmp_path):
    item = base_item(1, node="maat", requires_replication_lag_zero=True,
                      replication_lag_cmd="false")
    result, _reg, _rd = run(tmp_path, [item])
    assert result["items"][0]["status"] == "ABORTED"
    assert result["items"][0]["gate_failed"] == "REPL_LAG"


# ---- P4 paired-restore registry --------------------------------------------------------------

def test_paired_restore_registered_before_apply_and_cleared_after_g7(tmp_path):
    marker = str(HERE) + "/.p4-marker-does-not-exist"
    # apply_cmd checks the registry already has an entry for this item BEFORE mutating
    item = base_item(1, node="lumen")
    batch_path, sha = write_batch(tmp_path, [item])
    registry_path = tmp_path / "registry.json"

    seen_during_apply = {}

    real_run_cmd = fmr.run_cmd

    def spying_run_cmd(cmd, timeout=180):
        if cmd == item["apply_cmd"]:
            seen_during_apply["registry"] = json.loads(registry_path.read_text()) \
                if registry_path.exists() else {}
        return real_run_cmd(cmd, timeout=timeout)

    result = fmr.run_batch(batch_path, sha, registry_path=str(registry_path),
                            report_dir=str(tmp_path / "reports"),
                            run_cmd=spying_run_cmd, sleep_fn=lambda s: None, g5_t1=0, g5_t2=0)
    assert result["status"] == "COMPLETED"
    assert "1" in seen_during_apply["registry"]  # registered before apply ran
    assert json.loads(registry_path.read_text()) == {}  # cleared after clean G7


# ---- dispatcher-stop paired restore always runs (A11 section 5 standing rule) ---------------

def test_dispatcher_restore_runs_even_when_a_later_item_fails(tmp_path):
    preflight = {"dispatcher_stop_cmd": "true", "dispatcher_restore_cmd": f"echo restored >> {tmp_path}/restored.log"}
    items = [base_item(1, apply_cmd="false")]
    result, _reg, _rd = run(tmp_path, items, preflight=preflight)
    assert result["status"] == "STOPPED"
    assert os.path.exists(tmp_path / "restored.log")


def test_dispatcher_restore_runs_even_on_g0_failure(tmp_path):
    batch_path, _real_sha = write_batch(
        tmp_path, [base_item(1)],
        preflight={"dispatcher_stop_cmd": "true",
                   "dispatcher_restore_cmd": f"echo restored >> {tmp_path}/restored.log"})
    result = fmr.run_batch(batch_path, "0" * 64, registry_path=str(tmp_path / "registry.json"),
                            report_dir=str(tmp_path / "reports"), sleep_fn=lambda s: None)
    assert result["status"] == "ABORTED_PREFLIGHT"
    # dispatcher was never stopped (G0 failed first) so there's nothing to restore --
    # this asserts the *absence* of a bogus restore call, the mirror image of the
    # "always runs" guarantee: it only fires for a stop that actually happened.
    assert not os.path.exists(tmp_path / "restored.log")


# ---- informational (no apply_cmd) line items -------------------------------------------------

def test_item_with_no_apply_cmd_needs_no_gates(tmp_path):
    item = {"id": 1, "node": "gameboy", "component": "apt-tail", "lane": "APT"}
    result, _reg, _rd = run(tmp_path, [item])
    assert result["status"] == "COMPLETED"
    assert result["items"][0]["status"] == "OK"


# ---- phase 3b always produces a report and closes the wq ticket ------------------------------

def test_phase_3b_closes_wq_ticket_with_report_path(tmp_path, fast_and_faked):
    items = [base_item(1)]
    result, _reg, report_dir = run(tmp_path, items, wq_ticket=999)
    calls = fast_and_faked.read_text()
    assert "done 999" in calls
    assert result["report_path"] in calls


def test_phase_3b_report_lists_not_attempted_items_after_a_stop(tmp_path):
    items = [base_item(1, apply_cmd="false"), base_item(2, node="lumen")]
    result, _reg, report_dir = run(tmp_path, items)
    report_path = os.path.join(str(report_dir), "maint_window_2026-09-12.md")
    text = open(report_path).read()
    assert "NOT_ATTEMPTED" in text
    assert "ROLLED_BACK" in text
