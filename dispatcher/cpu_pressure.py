"""cpu_pressure.py — read Linux PSI CPU counters for admission decisions (wq#2631/wq#2647).

Reads /proc/pressure/cpu directly. No state file needed — PSI is instant. Mirrors
psi_pressure.py's memory-PSI shape (wq#844) but for CPU `some` pressure (`full` is
undefined for cpu.pressure — the kernel only exposes `some`, so there's no full_deny).

Returns a verdict: "ok", "tight", or "deny" (with a reason string).
"""
from __future__ import annotations

import os


PSI_PATH = os.environ.get("PSI_CPU_PATH", "/proc/pressure/cpu")

# Thresholds (percent). Overridable via the tight_cfg dict passed to check().
DEFAULT_SOME_TIGHT = 10.0
DEFAULT_SOME_DENY = 30.0


def _parse_psi(path: str | None = None) -> dict[str, float]:
    """Parse /proc/pressure/cpu into {some_avg60, ...}."""
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


def check(tight_cfg: dict | None = None) -> tuple[str, str]:
    """Check PSI CPU pressure.

    Args:
        tight_cfg: optional dict with cpu_some_tight, cpu_some_deny overrides.

    Returns:
        (verdict, reason) where verdict is "ok", "tight", or "deny".
    """
    if tight_cfg is None:
        tight_cfg = {}
    some_tight = tight_cfg.get("cpu_some_tight", DEFAULT_SOME_TIGHT)
    some_deny = tight_cfg.get("cpu_some_deny", DEFAULT_SOME_DENY)

    psi = _parse_psi()
    if not psi:
        return "ok", "cpu_psi_unavailable"

    some60 = psi.get("some_avg60", 0.0)

    if some60 > some_deny:
        return "deny", f"cpu_some_avg60={some60:.2f}%>={some_deny}%"
    if some60 > some_tight:
        return "tight", f"cpu_some_avg60={some60:.2f}%>={some_tight}%"

    return "ok", f"cpu_some={some60:.2f}%"
