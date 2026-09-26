"""cpu_pressure.py — read Linux PSI CPU counters for admission decisions (wq#2631/wq#2647).

wq#2791: a single worker running inside a cgroup CPU quota (cpu.max) shows up as PSI
`some` pressure at the ROOT (/proc/pressure/cpu) whenever it stalls on its own throttle —
that's a cgroup-local ceiling, not the box being saturated (wq#2720: 96% root `some`
avg60, 73% idle, load 12/44). The gate exists to protect the control plane
(system.slice: desktop-commander-mcp, cloudflared, the litellm docker, etc.) and other
users (user.slice), so by default it measures pressure IN THOSE CGROUPS instead of at
the root. A real-saturation term (root `some` + loadavg/core) is kept alongside so an
actually-saturated box (wq#2631 04:28Z: 28 workers, load 104/44) still denies.

Mirrors psi_pressure.py's memory-PSI shape (wq#844) but for CPU `some` pressure (`full`
is undefined for cpu.pressure — the kernel only exposes `some`, so there's no full_deny).

Returns a verdict: "ok", "tight", or "deny" (with a reason string).
"""
from __future__ import annotations

import os


_DEFAULT_PSI_PATH = "/proc/pressure/cpu"
PSI_PATH = os.environ.get("PSI_CPU_PATH", _DEFAULT_PSI_PATH)

_DEFAULT_PROTECTED_PATHS = (
    "/sys/fs/cgroup/system.slice/cpu.pressure",
    "/sys/fs/cgroup/user.slice/cpu.pressure",
)
_protected_env = os.environ.get("PSI_CPU_PROTECTED_PATHS")
PROTECTED_PATHS = (
    tuple(p.strip() for p in _protected_env.split(",") if p.strip())
    if _protected_env else _DEFAULT_PROTECTED_PATHS
)

# Thresholds (percent). Overridable via the tight_cfg dict passed to check().
DEFAULT_SOME_TIGHT = 10.0
DEFAULT_SOME_DENY = 30.0

# Real-saturation term: root `some` alone can be a false positive (cgroup throttle), so
# it only denies/tights when paired with box-wide load/core also being high.
DEFAULT_LOAD_SAT = 1.5
LOAD_SAT = float(os.environ.get("PSI_CPU_LOAD_SAT", DEFAULT_LOAD_SAT))


def _parse_psi(path: str | None = None) -> dict[str, float]:
    """Parse a /proc/pressure/cpu-shaped file into {some_avg60, ...}."""
    if path is None:
        path = PSI_PATH
    result = {}
    try:
        with open(path) as f:
            for line in f:
                parts = line.strip().split()
                if not parts:
                    continue
                kind = parts[0]  # "some" (cpu.pressure has no "full" line)
                for token in parts[1:]:
                    if "=" in token:
                        key, val = token.split("=", 1)
                        try:
                            result[f"{kind}_{key}"] = float(val)
                        except ValueError:
                            pass
    except (OSError, ValueError):
        pass  # fail-open: no PSI = no pressure signal
    return result


def _load_per_core() -> float:
    try:
        load1 = os.getloadavg()[0]
    except OSError:
        return 0.0
    return load1 / (os.cpu_count() or 1)


def _root_mode_active() -> bool:
    """PSI_CPU_MODE=root (or protected) is an explicit lever. Absent that, a PSI_PATH
    override (env or, as the existing single-path tests do, a direct module-attribute
    set) is treated as opting into the old single-root-path behaviour automatically."""
    mode = os.environ.get("PSI_CPU_MODE")
    if mode == "root":
        return True
    if mode == "protected":
        return False
    return PSI_PATH != _DEFAULT_PSI_PATH


def check(tight_cfg: dict | None = None) -> tuple[str, str]:
    """Check PSI CPU pressure.

    Args:
        tight_cfg: optional dict with cpu_some_tight, cpu_some_deny, cpu_load_sat
            overrides.

    Returns:
        (verdict, reason) where verdict is "ok", "tight", or "deny".
    """
    if tight_cfg is None:
        tight_cfg = {}
    some_tight = tight_cfg.get("cpu_some_tight", DEFAULT_SOME_TIGHT)
    some_deny = tight_cfg.get("cpu_some_deny", DEFAULT_SOME_DENY)
    load_sat = tight_cfg.get("cpu_load_sat", LOAD_SAT)

    root_psi = _parse_psi(PSI_PATH)
    root_some60 = root_psi.get("some_avg60", 0.0)

    if _root_mode_active():
        if not root_psi:
            return "ok", "cpu_psi_unavailable"
        if root_some60 > some_deny:
            return "deny", f"root_some={root_some60:.2f}%>={some_deny}%"
        if root_some60 > some_tight:
            return "tight", f"root_some={root_some60:.2f}%>={some_tight}%"
        return "ok", f"cpu_some={root_some60:.2f}%"

    protected_some = 0.0
    for p in PROTECTED_PATHS:
        psi = _parse_psi(p)
        if psi:
            protected_some = max(protected_some, psi.get("some_avg60", 0.0))

    load_per_core = _load_per_core()
    metrics = (f"protected_some={protected_some:.2f}% root_some={root_some60:.2f}% "
               f"load_per_core={load_per_core:.2f}")

    if protected_some > some_deny:
        return "deny", f"protected_some={protected_some:.2f}%>={some_deny}% ({metrics})"
    if root_some60 >= some_deny and load_per_core >= load_sat:
        return "deny", (f"root_some={root_some60:.2f}%>={some_deny}% "
                         f"load_per_core={load_per_core:.2f}>={load_sat} ({metrics})")
    if protected_some > some_tight:
        return "tight", f"protected_some={protected_some:.2f}%>={some_tight}% ({metrics})"
    if root_some60 >= some_tight and load_per_core >= load_sat:
        return "tight", (f"root_some={root_some60:.2f}%>={some_tight}% "
                          f"load_per_core={load_per_core:.2f}>={load_sat} ({metrics})")

    return "ok", metrics
