"""psi_pressure.py — read Linux PSI memory counters for dispatch decisions (wq#844).

Reads /proc/pressure/memory directly. No state file needed — PSI is instant.

Returns a verdict: "ok", "tight", or "deny" (with a reason string).
Thresholds from the ticket (wq#755 contention audit §5):
  some avg60 >5%  → tight (throttle to high-pri only)
  some avg60 >15% → deny  (hard stop + alert)
  full avg60 >0%  → deny  (hard stop + alert: ANY full stall = box is hurting)
"""
from __future__ import annotations

import os


PSI_PATH = os.environ.get("PSI_MEMORY_PATH", "/proc/pressure/memory")

# Thresholds (percent). Overridable via config.json tight_mode.psi_* fields.
DEFAULT_SOME_TIGHT = 5.0
DEFAULT_SOME_DENY = 15.0
DEFAULT_FULL_DENY = 0.0  # any full stall = deny


def _parse_psi(path: str | None = None) -> dict[str, float]:
    """Parse /proc/pressure/memory into {some_avg60, full_avg60, ...}."""
    if path is None:
        path = PSI_PATH
    result = {}
    try:
        with open(path) as f:
            for line in f:
                parts = line.strip().split()
                if not parts:
                    continue
                kind = parts[0]  # "some" or "full"
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
    """Check PSI memory pressure.

    Args:
        tight_cfg: the tight_mode block from dispatcher config.json.
            Optional fields: psi_some_tight, psi_some_deny, psi_full_deny.

    Returns:
        (verdict, reason) where verdict is "ok", "tight", or "deny".
    """
    if tight_cfg is None:
        tight_cfg = {}
    some_tight = tight_cfg.get("psi_some_tight", DEFAULT_SOME_TIGHT)
    some_deny = tight_cfg.get("psi_some_deny", DEFAULT_SOME_DENY)
    full_deny = tight_cfg.get("psi_full_deny", DEFAULT_FULL_DENY)

    psi = _parse_psi()
    if not psi:
        return "ok", "psi_unavailable"

    some60 = psi.get("some_avg60", 0.0)
    full60 = psi.get("full_avg60", 0.0)

    # hard stops first (deny)
    if full60 > full_deny:
        return "deny", f"psi_full_avg60={full60:.2f}%>={full_deny}%"
    if some60 > some_deny:
        return "deny", f"psi_some_avg60={some60:.2f}%>={some_deny}%"
    # throttle
    if some60 > some_tight:
        return "tight", f"psi_some_avg60={some60:.2f}%>={some_tight}%"

    return "ok", f"psi_some={some60:.2f}%_full={full60:.2f}%"
