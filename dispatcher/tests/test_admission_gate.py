"""Test admission_gate module (wq#2631/wq#2647/wq#2790)."""
import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import admission_gate
import concurrency
import cpu_pressure
import local_capacity


class _FakeUnits:
    """Patch concurrency.slots_available for a deterministic ceiling test."""


def test_ok_when_room_and_cpu_quiet():
    import concurrency
    old_slots = concurrency.slots_available
    old_cpu_path = cpu_pressure.PSI_PATH
    with tempfile.NamedTemporaryFile(mode="w", suffix=".psi", delete=False) as f:
        path = f.name
        f.write("some avg10=0.00 avg60=0.00 avg300=0.00 total=1\n")
    try:
        concurrency.slots_available = lambda *a, **k: (1, ["fworker-wq1"])
        cpu_pressure.PSI_PATH = path
        v, r, detail = admission_gate.check()
        assert v == "ok", f"expected ok, got {v}: {r}"
        print(f"  PASS ok_when_room_and_cpu_quiet: {v} {r}")
    finally:
        concurrency.slots_available = old_slots
        cpu_pressure.PSI_PATH = old_cpu_path
        os.unlink(path)


def test_deny_on_ceiling():
    import concurrency
    old_slots = concurrency.slots_available
    try:
        concurrency.slots_available = lambda *a, **k: (0, ["fworker-wq1", "fworker-wq2", "fworker-wq3"])
        v, r, detail = admission_gate.check()
        assert v == "deny", f"expected deny, got {v}: {r}"
        assert "concurrency_ceiling" in r
        print(f"  PASS deny_on_ceiling: {v} {r}")
    finally:
        concurrency.slots_available = old_slots


def test_deny_on_cpu_pressure():
    import concurrency
    old_slots = concurrency.slots_available
    old_cpu_path = cpu_pressure.PSI_PATH
    with tempfile.NamedTemporaryFile(mode="w", suffix=".psi", delete=False) as f:
        path = f.name
        f.write("some avg10=0.00 avg60=90.00 avg300=0.00 total=1\n")
    try:
        concurrency.slots_available = lambda *a, **k: (1, ["fworker-wq1"])
        cpu_pressure.PSI_PATH = path
        v, r, detail = admission_gate.check()
        assert v == "deny", f"expected deny, got {v}: {r}"
        assert "cpu_pressure" in r
        print(f"  PASS deny_on_cpu_pressure: {v} {r}")
    finally:
        concurrency.slots_available = old_slots
        cpu_pressure.PSI_PATH = old_cpu_path
        os.unlink(path)


def test_override_requires_both_vars():
    import concurrency
    old_slots = concurrency.slots_available
    old_env = dict(os.environ)
    try:
        concurrency.slots_available = lambda *a, **k: (0, ["fworker-wq1"])
        os.environ["ADMISSION_GATE_OVERRIDE"] = "1"
        os.environ.pop("ADMISSION_GATE_OVERRIDE_REASON", None)
        v, r, detail = admission_gate.check()
        assert v == "deny", f"override with no reason must not bypass, got {v}: {r}"

        os.environ["ADMISSION_GATE_OVERRIDE_REASON"] = "manual burst approved by Frank"
        v, r, detail = admission_gate.check()
        assert v == "ok", f"override with both vars set should bypass, got {v}: {r}"
        assert "manual burst approved by Frank" in r
        print(f"  PASS override_requires_both_vars: {v} {r}")
    finally:
        concurrency.slots_available = old_slots
        os.environ.clear()
        os.environ.update(old_env)



# ---- wq#2790: partitioned check (--ticket/--model) --------------------------------------

@pytest.fixture
def quiet_cpu(monkeypatch, tmp_path):
    """A PSI fixture with zero pressure, so partition tests aren't at the mercy of the
    real box's /proc/pressure/cpu (existing tests above do the same inline)."""
    path = tmp_path / "quiet.psi"
    path.write_text("some avg10=0.00 avg60=0.00 avg300=0.00 total=1\n")
    monkeypatch.setattr(cpu_pressure, "PSI_PATH", str(path))
    return path


def test_local_ticket_admits_on_free_mac_slot_while_cloud_fworkers_run(monkeypatch, quiet_cpu):
    """wq#2127: 3 cloud fworkers running must not eat the local rung's ceiling — a local
    ticket is bounded by its own backend's free slots, never the flat 3."""
    monkeypatch.setattr(local_capacity, "http_probe", lambda url, timeout=2.0: True)
    monkeypatch.setattr(concurrency, "list_running_units",
                        lambda prefix="fworker-wq": ["fworker-wq101", "fworker-wq102",
                                                     "fworker-wq103"])
    monkeypatch.setattr(concurrency, "is_local_unit", lambda u, model_lookup=None: False)
    cfg = {"cloud_ceiling": 10, "local_ceiling": 12,
          "local_backends": {"hathor-llama8b": {"health_url": "http://hathor.test/api/ps",
                                                "slots": 2}}}
    monkeypatch.setattr(admission_gate, "_load_config", lambda path=None: cfg)

    v, r, detail = admission_gate.check(ticket=42, model="hathor-llama8b")
    assert v == "ok", f"expected ok with 2 free Mac slots, got {v}: {r}"
    assert detail["partition"] == "local"
    assert detail["running"] == 0        # the 3 cloud fworkers don't count as local units
    assert detail["free_slots"] == 2


def test_local_group_resolves_to_a_member(monkeypatch, quiet_cpu):
    """wq#2154: a `local-worker` ticket's verdict names the concrete backend it would
    run on, not the routing group name."""
    monkeypatch.setattr(local_capacity, "http_probe", lambda url, timeout=2.0: True)
    monkeypatch.setattr(concurrency, "list_running_units", lambda prefix="fworker-wq": [])
    cfg = {"local_ceiling": 12,
          "local_backends": {
              "hathor-llama8b": {"health_url": "http://hathor.test/api/ps", "slots": 2},
              "nephthys-qwen14b": {"health_url": "http://neph.test/api/ps", "slots": 2},
              "local-worker": {"group": ["hathor-llama8b", "nephthys-qwen14b"]}}}
    monkeypatch.setattr(admission_gate, "_load_config", lambda path=None: cfg)

    v, r, detail = admission_gate.check(ticket=43, model="local-worker")
    assert v == "ok", f"expected ok, got {v}: {r}"
    assert detail["partition"] == "local"
    assert detail["member"] == "hathor-llama8b"   # tie on free slots: config order


def test_cloud_ticket_denied_at_budget_tapered_ceiling_not_flat_three(monkeypatch, quiet_cpu):
    """wq#2127: the cloud ceiling is config cloud_ceiling tapered by spendable budget
    (burn_rate.budget_ceiling), never the harness/concurrency.CLOUD_CEILING=3 flat cap.
    session at 65% used (spendable 5 of a 10 taper) tapers 10 -> 5: 4 running admits,
    5 running denies — both outcomes the old flat-3 gate would have gotten wrong."""
    monkeypatch.setattr(admission_gate, "_governor_limits",
                        lambda path=None: {"session": {"percent": 65},
                                           "weekly_all": {"percent": 40}})
    monkeypatch.setattr(admission_gate, "_load_config", lambda path=None: {"cloud_ceiling": 10})
    monkeypatch.setattr(concurrency, "is_local_unit", lambda u, model_lookup=None: False)

    monkeypatch.setattr(concurrency, "list_running_units",
                        lambda prefix="fworker-wq": [f"fworker-wq{i}" for i in range(4)])
    v, r, detail = admission_gate.check(ticket=44, model="sonnet-5")
    assert detail["ceiling"] == 5, f"expected tapered ceiling 5, got {detail}"
    assert v == "ok", f"4 running is under the tapered ceiling of 5, got {v}: {r}"

    monkeypatch.setattr(concurrency, "list_running_units",
                        lambda prefix="fworker-wq": [f"fworker-wq{i}" for i in range(5)])
    v, r, detail = admission_gate.check(ticket=44, model="sonnet-5")
    assert detail["ceiling"] == 5
    assert v == "deny", f"5 running is at the tapered ceiling of 5, got {v}: {r}"
    assert "concurrency_ceiling" in r


def test_noarg_check_reads_ceiling_from_config(monkeypatch, quiet_cpu, tmp_path):
    """wq#2127: with neither --ticket nor --model, the ceiling comes from config.json's
    cloud_ceiling, not the hardcoded 3."""
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({"cloud_ceiling": 7}))
    monkeypatch.setattr(admission_gate, "CONFIG_PATH", str(config_file))

    monkeypatch.setattr(concurrency, "list_running_units",
                        lambda prefix="fworker-wq": [f"fworker-wq{i}" for i in range(7)])
    v, r, detail = admission_gate.check()
    assert detail["ceiling"] == 7
    assert v == "deny", f"7 running is at config's ceiling of 7, got {v}: {r}"

    monkeypatch.setattr(concurrency, "list_running_units",
                        lambda prefix="fworker-wq": [f"fworker-wq{i}" for i in range(6)])
    v, r, detail = admission_gate.check()
    assert detail["ceiling"] == 7
    assert v == "ok", f"6 running is under config's ceiling of 7, got {v}: {r}"


if __name__ == "__main__":
    print("admission_gate tests (wq#2631/wq#2647):")
    test_ok_when_room_and_cpu_quiet()
    test_deny_on_ceiling()
    test_deny_on_cpu_pressure()
    test_override_requires_both_vars()
    print("\nALL TESTS PASSED")
