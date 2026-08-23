"""Test psi_pressure module (wq#844)."""
import json
import os
import sys
import tempfile

# Make the dispatcher dir importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import psi_pressure


def _write_psi(path, some_avg60, full_avg60):
    with open(path, "w") as f:
        f.write(f"some avg10=0.00 avg60={some_avg60:.2f} avg300=0.00 total=100\n")
        f.write(f"full avg10=0.00 avg60={full_avg60:.2f} avg300=0.00 total=50\n")


def test_ok():
    """Normal: both near zero."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".psi", delete=False) as f:
        path = f.name
    try:
        _write_psi(path, 0.0, 0.0)
        old = psi_pressure.PSI_PATH
        psi_pressure.PSI_PATH = path
        v, r = psi_pressure.check()
        assert v == "ok", f"expected ok, got {v}: {r}"
        print(f"  PASS ok: {v} {r}")
    finally:
        psi_pressure.PSI_PATH = old
        os.unlink(path)


def test_tight():
    """some avg60 = 8% (>5%, <15%) -> tight."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".psi", delete=False) as f:
        path = f.name
    try:
        _write_psi(path, 8.0, 0.0)
        old = psi_pressure.PSI_PATH
        psi_pressure.PSI_PATH = path
        v, r = psi_pressure.check()
        assert v == "tight", f"expected tight, got {v}: {r}"
        print(f"  PASS tight: {v} {r}")
    finally:
        psi_pressure.PSI_PATH = old
        os.unlink(path)


def test_deny_some():
    """some avg60 = 20% (>15%) -> deny."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".psi", delete=False) as f:
        path = f.name
    try:
        _write_psi(path, 20.0, 0.0)
        old = psi_pressure.PSI_PATH
        psi_pressure.PSI_PATH = path
        v, r = psi_pressure.check()
        assert v == "deny", f"expected deny, got {v}: {r}"
        print(f"  PASS deny_some: {v} {r}")
    finally:
        psi_pressure.PSI_PATH = old
        os.unlink(path)


def test_deny_full():
    """full avg60 = 0.5% (>0%) -> deny (hard stop on ANY full stall)."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".psi", delete=False) as f:
        path = f.name
    try:
        _write_psi(path, 1.0, 0.5)
        old = psi_pressure.PSI_PATH
        psi_pressure.PSI_PATH = path
        v, r = psi_pressure.check()
        assert v == "deny", f"expected deny, got {v}: {r}"
        assert "full" in r, f"reason should mention full: {r}"
        print(f"  PASS deny_full: {v} {r}")
    finally:
        psi_pressure.PSI_PATH = old
        os.unlink(path)

def test_missing_file():
    """Missing PSI file -> fail-open (ok)."""
    old = psi_pressure.PSI_PATH
    psi_pressure.PSI_PATH = "/nonexistent/proc/pressure/memory"
    try:
        v, r = psi_pressure.check()
        assert v == "ok", f"expected ok (fail-open), got {v}: {r}"
        print(f"  PASS missing_file: {v} {r}")
    finally:
        psi_pressure.PSI_PATH = old


def test_custom_thresholds():
    """Custom thresholds via tight_cfg."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".psi", delete=False) as f:
        path = f.name
    try:
        _write_psi(path, 3.0, 0.0)  # 3% some
        old = psi_pressure.PSI_PATH
        psi_pressure.PSI_PATH = path
        # Default (5%) -> ok
        v, _ = psi_pressure.check()
        assert v == "ok", f"default should be ok at 3%"
        # Custom tight at 2% -> tight
        v, r = psi_pressure.check({"psi_some_tight": 2.0})
        assert v == "tight", f"custom 2% should be tight at 3%: {v} {r}"
        print(f"  PASS custom_thresholds")
    finally:
        psi_pressure.PSI_PATH = old
        os.unlink(path)


def test_boundary_full_zero():
    """full avg60 = 0.00 exactly -> ok (threshold is >0, not >=0)."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".psi", delete=False) as f:
        path = f.name
    try:
        _write_psi(path, 0.0, 0.0)
        old = psi_pressure.PSI_PATH
        psi_pressure.PSI_PATH = path
        v, _ = psi_pressure.check()
        assert v == "ok", f"full=0.00 exactly should be ok"
        print(f"  PASS boundary_full_zero")
    finally:
        psi_pressure.PSI_PATH = old
        os.unlink(path)


if __name__ == "__main__":
    print("PSI pressure tests (wq#844):")
    test_ok()
    test_tight()
    test_deny_some()
    test_deny_full()
    test_missing_file()
    test_custom_thresholds()
    test_boundary_full_zero()
    print("\nALL TESTS PASSED")
