"""Test admission_gate module (wq#2631/wq#2647)."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import admission_gate
import cpu_pressure


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


if __name__ == "__main__":
    print("admission_gate tests (wq#2631/wq#2647):")
    test_ok_when_room_and_cpu_quiet()
    test_deny_on_ceiling()
    test_deny_on_cpu_pressure()
    test_override_requires_both_vars()
    print("\nALL TESTS PASSED")
