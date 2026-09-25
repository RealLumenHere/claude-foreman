"""Test cpu_pressure module (wq#2631/wq#2647)."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import cpu_pressure


def _write_psi(path, some_avg60):
    with open(path, "w") as f:
        f.write(f"some avg10=0.00 avg60={some_avg60:.2f} avg300=0.00 total=100\n")


def test_ok():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".psi", delete=False) as f:
        path = f.name
    try:
        _write_psi(path, 0.0)
        old = cpu_pressure.PSI_PATH
        cpu_pressure.PSI_PATH = path
        v, r = cpu_pressure.check()
        assert v == "ok", f"expected ok, got {v}: {r}"
        print(f"  PASS ok: {v} {r}")
    finally:
        cpu_pressure.PSI_PATH = old
        os.unlink(path)


def test_tight():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".psi", delete=False) as f:
        path = f.name
    try:
        _write_psi(path, 15.0)  # >10 (tight), <30 (deny)
        old = cpu_pressure.PSI_PATH
        cpu_pressure.PSI_PATH = path
        v, r = cpu_pressure.check()
        assert v == "tight", f"expected tight, got {v}: {r}"
        print(f"  PASS tight: {v} {r}")
    finally:
        cpu_pressure.PSI_PATH = old
        os.unlink(path)


def test_deny():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".psi", delete=False) as f:
        path = f.name
    try:
        _write_psi(path, 40.0)  # >30 -> deny
        old = cpu_pressure.PSI_PATH
        cpu_pressure.PSI_PATH = path
        v, r = cpu_pressure.check()
        assert v == "deny", f"expected deny, got {v}: {r}"
        print(f"  PASS deny: {v} {r}")
    finally:
        cpu_pressure.PSI_PATH = old
        os.unlink(path)


def test_missing_file():
    old = cpu_pressure.PSI_PATH
    cpu_pressure.PSI_PATH = "/nonexistent/path/cpu.pressure"
    try:
        v, r = cpu_pressure.check()
        assert v == "ok", f"missing file should fail-open, got {v}: {r}"
        print(f"  PASS missing_file: {v} {r}")
    finally:
        cpu_pressure.PSI_PATH = old


def test_custom_thresholds():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".psi", delete=False) as f:
        path = f.name
    try:
        _write_psi(path, 5.0)
        old = cpu_pressure.PSI_PATH
        cpu_pressure.PSI_PATH = path
        v, r = cpu_pressure.check({"cpu_some_tight": 1.0, "cpu_some_deny": 4.0})
        assert v == "deny", f"custom thresholds should deny at 5%, got {v}: {r}"
        print(f"  PASS custom_thresholds: {v} {r}")
    finally:
        cpu_pressure.PSI_PATH = old
        os.unlink(path)


if __name__ == "__main__":
    print("CPU pressure tests (wq#2631/wq#2647):")
    test_ok()
    test_tight()
    test_deny()
    test_missing_file()
    test_custom_thresholds()
    print("\nALL TESTS PASSED")
