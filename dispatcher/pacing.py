#!/usr/bin/env python3
"""pacing.py — AUTO-4 forward-looking weekly-budget pacing (wq#716).

router.governor_pressure (DESIGN.md §6 layer 2) is reactive: it floors on
*remaining* percentage, so e.g. a Fable floor of "10% remaining" only bites once
90% of the week's budget is already gone, however fast it got there. That let the
2026-08-13..08-20 week burn 99% of Fable in the first 36 hours (~21% of the week)
while the floor sat untouched the whole time.

Pacing adds a second, forward-looking layer on top of the governor's own gate: it
projects the *current* burn rate out to each weekly pool's reset
(usage_pct / elapsed_fraction_of_week) and throttles before that projection would
blow past the budget, not after. It never replaces the governor's floor check —
`governor_deny` still wins outright; pacing only adds `delay` (skip non-critical
tickets this pass) and `skip` (skip every non-exempt ticket drawing on the hot
pool this pass) verdicts on top of an `ok`/`tight` governor verdict.

This module is pure — no subprocess calls, no dispatcher state, no I/O beyond
reading its own config file. foreman_dispatcher.py wires `decide` in as a policy
hook (`PACING_HOOK`, mirroring the `LANGFUSE_EMIT` seam) so tests can swap it and
the dispatcher pass stays the single place that owns state, logging, and ntfy.
"""
import json
import os
from datetime import datetime, timezone

DISPATCHER_DIR = os.path.dirname(os.path.abspath(__file__))
PACING_CONFIG_PATH = os.environ.get(
    "FOREMAN_PACING_CONFIG", os.path.join(DISPATCHER_DIR, "pacing_config.json"))

WEEK_SECONDS = 7 * 24 * 3600
POLICIES = ("aggressive", "conservative", "off")

# weekly pool -> only paces models whose name contains this substring (None = every model)
WEEKLY_POOLS = {"weekly_all": None, "fable_weekly_scoped": "fable"}

DEFAULT_CONFIG = {
    "policy": "conservative",
    "min_elapsed_fraction": 0.02,   # ~3.4h into the week; earlier, a rate is just noise
    "delay_min_pri": 1,             # under 'delay', only pri<=this still dispatches
    "ntfy_dedup_hours": 6,
    "policies": {
        "conservative": {"delay_at_projected_pct": 90, "skip_at_projected_pct": 130},
        "aggressive": {"delay_at_projected_pct": 120, "skip_at_projected_pct": 180},
    },
}

_ACTION_RANK = {"ok": 0, "delay": 1, "skip": 2}


class PacingConfigError(Exception):
    pass


class PacingVerdict:
    """action: 'ok' | 'delay' | 'skip'. pool/detail are None when there's no pressure."""
    __slots__ = ("action", "pool", "reason", "detail")

    def __init__(self, action, pool, reason, detail=None):
        self.action, self.pool, self.reason, self.detail = action, pool, reason, detail

    def __repr__(self):
        return f"PacingVerdict({self.action!r}, {self.pool!r}, {self.reason!r})"

    def as_fields(self):
        fields = {"pacing_action": self.action, "pacing_reason": self.reason}
        if self.pool:
            fields["pacing_pool"] = self.pool
        if self.detail:
            fields["pacing_detail"] = self.detail
        return fields


# ---- config (fail toward the safe default, never toward crashing the pass) -----------

def _validate(cfg, path):
    if cfg.get("policy") not in POLICIES:
        raise PacingConfigError(f"{path}: policy must be one of {POLICIES}")
    mef = cfg.get("min_elapsed_fraction")
    if not (isinstance(mef, (int, float)) and not isinstance(mef, bool) and 0 <= mef < 1):
        raise PacingConfigError(f"{path}: min_elapsed_fraction must be a number in [0, 1)")
    dmp = cfg.get("delay_min_pri")
    if not (isinstance(dmp, int) and not isinstance(dmp, bool)):
        raise PacingConfigError(f"{path}: delay_min_pri must be an integer")
    ndh = cfg.get("ntfy_dedup_hours")
    if not (isinstance(ndh, (int, float)) and not isinstance(ndh, bool) and ndh >= 0):
        raise PacingConfigError(f"{path}: ntfy_dedup_hours must be a non-negative number")
    if cfg["policy"] == "off":
        return
    pols = cfg.get("policies") or {}
    p = pols.get(cfg["policy"])
    if not isinstance(p, dict):
        raise PacingConfigError(f"{path}: policies.{cfg['policy']} missing")
    for k in ("delay_at_projected_pct", "skip_at_projected_pct"):
        if not isinstance(p.get(k), (int, float)) or isinstance(p.get(k), bool):
            raise PacingConfigError(f"{path}: policies.{cfg['policy']}.{k} must be a number")
    if p["skip_at_projected_pct"] < p["delay_at_projected_pct"]:
        raise PacingConfigError(
            f"{path}: policies.{cfg['policy']}.skip_at_projected_pct must be "
            ">= delay_at_projected_pct")


def load_config(path=None):
    """Returns (config, warning). `warning` is non-None only when the file exists but is
    invalid — pacing then falls back to DEFAULT_CONFIG (conservative) rather than failing
    the whole dispatch pass; pacing is an add-on layer, not the governor's own fail-closed
    budget gate (DESIGN §6 layer 2 already owns that, and already fails closed)."""
    path = path or PACING_CONFIG_PATH
    if not os.path.exists(path):
        return dict(DEFAULT_CONFIG), None
    try:
        with open(path) as f:
            cfg = json.load(f)
        if not isinstance(cfg, dict):
            raise PacingConfigError(f"{path}: top level must be an object")
        _validate(cfg, path)
    except (OSError, ValueError, PacingConfigError) as e:
        return dict(DEFAULT_CONFIG), f"pacing config invalid, using conservative default: {e}"
    return cfg, None


# ---- projection ------------------------------------------------------------------------

def project_pool(limits, pool, now, min_elapsed_fraction):
    """None when there isn't enough reliable data to project (missing/stale resets_at, or
    too early into the window to trust a rate) — never guess a projection."""
    b = (limits or {}).get(pool) or {}
    pct, resets_at = b.get("percent"), b.get("resets_at")
    if pct is None or not resets_at:
        return None
    try:
        reset_dt = datetime.fromisoformat(resets_at)
    except (TypeError, ValueError):
        return None
    remaining_s = (reset_dt - now).total_seconds()
    if remaining_s <= 0 or remaining_s > WEEK_SECONDS:
        return None  # already rolled over, or a bogus horizon — the governor's own poll
                     # will refresh this; pacing never guesses at a window it can't see
    elapsed_fraction = 1 - (remaining_s / WEEK_SECONDS)
    if elapsed_fraction < min_elapsed_fraction:
        return None  # too early into the week for a rate to mean anything yet
    return {"pool": pool, "usage_pct": pct, "elapsed_fraction": elapsed_fraction,
            "projected_pct": pct / elapsed_fraction}


def assess(limits, model, config, now=None):
    """Worst-case verdict across every weekly pool `model` draws from."""
    now = now or datetime.now(timezone.utc)
    if config["policy"] == "off":
        return PacingVerdict("ok", None, "pacing policy=off")
    thresholds = config["policies"][config["policy"]]
    worst = PacingVerdict("ok", None, "within pace")
    for pool, only_for in WEEKLY_POOLS.items():
        if only_for and only_for not in model:
            continue
        proj = project_pool(limits, pool, now, config["min_elapsed_fraction"])
        if proj is None:
            continue
        pct = proj["projected_pct"]
        detail = (f"usage {proj['usage_pct']:.0f}% at {proj['elapsed_fraction']:.0%} of "
                  f"week -> projects to {pct:.0f}% by reset")
        if pct >= thresholds["skip_at_projected_pct"]:
            action, reason = "skip", (f"projected {pct:.0f}% >= skip threshold "
                                      f"{thresholds['skip_at_projected_pct']}%")
        elif pct >= thresholds["delay_at_projected_pct"]:
            action, reason = "delay", (f"projected {pct:.0f}% >= delay threshold "
                                       f"{thresholds['delay_at_projected_pct']}%")
        else:
            continue
        v = PacingVerdict(action, pool, reason, detail)
        if _ACTION_RANK[v.action] > _ACTION_RANK[worst.action]:
            worst = v
    return worst


# ---- dispatcher hook (§ guardrails: fire-exempt, bounded to this pass) ----------------

def decide(pri, model, limits, config, exempt, now=None):
    """The per-ticket call the dispatcher's budget stage makes. `exempt` is True for
    tickets that already cleared Frank's own fire-approval gate — pacing MUST NOT block
    a Frank-approved fire ticket (wq#716 guardrail). Bounded to this pass only: nothing
    here is persisted beyond the caller's ntfy-dedup ledger, so a cooled-off pool
    un-throttles on the very next tick — pacing can never permanently disable dispatch."""
    if exempt:
        return PacingVerdict("ok", None, "frank-approved fire ticket: pacing exempt")
    v = assess(limits, model, config, now=now)
    if v.action == "skip":
        return v
    if v.action == "delay" and pri > config["delay_min_pri"]:
        return v
    return PacingVerdict("ok", v.pool, v.reason)
