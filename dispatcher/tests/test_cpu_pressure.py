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


class _ProtectedModeFixture:
    """wq#2791: pins mode=protected + deterministic root/protected/load inputs so a
    single cgroup-throttled worker (high root `some`, low load/core) can't read as
    box-wide pressure, while a real-saturation case (high root `some` AND high
    load/core) still denies."""

    def __init__(self, root_some, protected_somes, load_per_core):
        self.root_some = root_some
        self.protected_somes = protected_somes
        self.load_per_core = load_per_core
        self.root_path = None
        self.protected_paths = []

    def __enter__(self):
        self.old_mode = os.environ.get("PSI_CPU_MODE")
        self.old_psi_path = cpu_pressure.PSI_PATH
        self.old_protected_paths = cpu_pressure.PROTECTED_PATHS
        self.old_load_per_core = cpu_pressure._load_per_core

        os.environ["PSI_CPU_MODE"] = "protected"

        with tempfile.NamedTemporaryFile(mode="w", suffix=".psi", delete=False) as f:
            self.root_path = f.name
        _write_psi(self.root_path, self.root_some)
        cpu_pressure.PSI_PATH = self.root_path

        for some in self.protected_somes:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".psi", delete=False) as f:
                p = f.name
            _write_psi(p, some)
            self.protected_paths.append(p)
        cpu_pressure.PROTECTED_PATHS = tuple(self.protected_paths)

        cpu_pressure._load_per_core = lambda: self.load_per_core
        return self

    def __exit__(self, *exc):
        if self.old_mode is None:
            os.environ.pop("PSI_CPU_MODE", None)
        else:
            os.environ["PSI_CPU_MODE"] = self.old_mode
        cpu_pressure.PSI_PATH = self.old_psi_path
        cpu_pressure.PROTECTED_PATHS = self.old_protected_paths
        cpu_pressure._load_per_core = self.old_load_per_core
        os.unlink(self.root_path)
        for p in self.protected_paths:
            os.unlink(p)


def test_protected_mode_cgroup_throttle_is_not_box_pressure():
    """wq#2720 shape: one worker throttled in its own cgroup quota drives root `some`
    to 96% while the protected cgroups (control plane) and load/core are both low ->
    ok, not a box-wide halt."""
    with _ProtectedModeFixture(root_some=96.0, protected_somes=[1.0, 0.0],
                                load_per_core=0.3):
        v, r = cpu_pressure.check()
        assert v == "ok", f"expected ok (cgroup-local throttle, not box pressure), got {v}: {r}"
        print(f"  PASS protected_mode_cgroup_throttle_is_not_box_pressure: {v} {r}")


def test_protected_mode_real_saturation_denies():
    """wq#2631 04:28Z shape: root `some` and load/core both elevated -> real
    saturation, deny even though the protected cgroups themselves are quiet."""
    with _ProtectedModeFixture(root_some=50.0, protected_somes=[2.0, 0.0],
                                load_per_core=2.4):
        v, r = cpu_pressure.check()
        assert v == "deny", f"expected deny (real saturation), got {v}: {r}"
        assert "root_some" in r and "load_per_core" in r, f"reason should name the term: {r}"
        print(f"  PASS protected_mode_real_saturation_denies: {v} {r}")


def test_protected_mode_protected_path_denies_alone():
    """A protected cgroup (control plane) at 40% denies on its own, independent of
    root/load."""
    with _ProtectedModeFixture(root_some=0.0, protected_somes=[40.0, 0.0],
                                load_per_core=0.0):
        v, r = cpu_pressure.check()
        assert v == "deny", f"expected deny (protected cgroup saturated), got {v}: {r}"
        assert "protected_some" in r, f"reason should name protected_some: {r}"
        print(f"  PASS protected_mode_protected_path_denies_alone: {v} {r}")


def test_mode_root_env_reproduces_old_verdicts():
    """PSI_CPU_MODE=root is the rollback lever: single-path root behaviour, same
    verdicts as before wq#2791."""
    old_mode = os.environ.get("PSI_CPU_MODE")
    os.environ["PSI_CPU_MODE"] = "root"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".psi", delete=False) as f:
        path = f.name
    try:
        old_path = cpu_pressure.PSI_PATH
        cpu_pressure.PSI_PATH = path

        _write_psi(path, 0.0)
        v, r = cpu_pressure.check()
        assert v == "ok", f"root=0% should be ok, got {v}: {r}"

        _write_psi(path, 15.0)
        v, r = cpu_pressure.check()
        assert v == "tight", f"root=15% should be tight, got {v}: {r}"

        _write_psi(path, 40.0)
        v, r = cpu_pressure.check()
        assert v == "deny", f"root=40% should be deny, got {v}: {r}"

        print("  PASS mode_root_env_reproduces_old_verdicts")
    finally:
        cpu_pressure.PSI_PATH = old_path
        if old_mode is None:
            os.environ.pop("PSI_CPU_MODE", None)
        else:
            os.environ["PSI_CPU_MODE"] = old_mode
        os.unlink(path)


if __name__ == "__main__":
    print("CPU pressure tests (wq#2631/wq#2647/wq#2791):")
    test_ok()
    test_tight()
    test_deny()
    test_missing_file()
    test_custom_thresholds()
    test_protected_mode_cgroup_throttle_is_not_box_pressure()
    test_protected_mode_real_saturation_denies()
    test_protected_mode_protected_path_denies_alone()
    test_mode_root_env_reproduces_old_verdicts()
    print("\nALL TESTS PASSED")
