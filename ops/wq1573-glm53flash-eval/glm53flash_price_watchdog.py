#!/usr/bin/env python3
"""wq#1573 — page lumen-fleet if z-ai/glm-5.3-flash's OpenRouter price moves >25%.

Context: glm-5.3-flash (ex stealth 'ox-alpha') went from a free viral preview
to a cheap-paid rung on 2026-09-09 ($0.075/M in, $0.25/M out, 1.3M ctx). We're
routing real workload onto it BECAUSE it's cheap — if OpenRouter revises that
promo-adjacent price, everyone downstream (fleet research synthesis, minerva
defer-research, messenger) is silently paying more per token with no signal.
This is a price tripwire, not a spend tripwire (paid_spend_tripwire.py covers
spend) — it compares OpenRouter's live catalog price against the pinned
baseline recorded when the rung was added, independent of how much we've
actually spent.

Data source: OpenRouter's public models catalog (GET /api/v1/models), which
lists per-model pricing without auth. No LiteLLM master key needed.

Usage: glm53flash_price_watchdog.py [--print]
Env:
  GLM53FLASH_MOVE_THRESHOLD_PCT   alert threshold, percent (default 25)
  GLM53FLASH_BASELINE_IN_USD_M    pinned baseline $/M input tokens (default 0.075)
  GLM53FLASH_BASELINE_OUT_USD_M   pinned baseline $/M output tokens (default 0.25)
  GLM53FLASH_OPENROUTER_MODEL_ID  OpenRouter model id (default z-ai/glm-5.3-flash)
  GLM53FLASH_OPENROUTER_URL       catalog URL (default https://openrouter.ai/api/v1/models)
  GLM53FLASH_NTFY                 path to ntfy-send (default /home/lumen/bin/ntfy-send)
  GLM53FLASH_TOPIC                ntfy topic (default lumen-fleet)
  GLM53FLASH_STATE_DIR            state dir (default /home/lumen/agents/glm53flash-price-watchdog)
"""
import json
import os
import pathlib
import subprocess
import sys
import urllib.request

DEFAULT_MODEL_ID = "z-ai/glm-5.3-flash"
DEFAULT_URL = "https://openrouter.ai/api/v1/models"
DEFAULT_THRESHOLD_PCT = 25.0
DEFAULT_BASELINE_IN = 0.075
DEFAULT_BASELINE_OUT = 0.25


def pct_move(baseline: float, current: float) -> float:
    """Signed percent change of current vs baseline. baseline<=0 is undefined (0 move)."""
    if baseline <= 0:
        return 0.0
    return (current - baseline) / baseline * 100.0


def find_model_pricing(catalog: dict, model_id: str):
    """Extract (usd_per_m_in, usd_per_m_out) for model_id from an OpenRouter
    /models response. OpenRouter prices are USD per single token; this
    project's baselines are USD per million tokens, so scale by 1e6.
    Returns None if the model isn't in the catalog (e.g. renamed/removed —
    that is itself alert-worthy, treated as a move by the caller)."""
    for m in catalog.get("data", []):
        if m.get("id") == model_id:
            pricing = m.get("pricing", {})
            try:
                return float(pricing["prompt"]) * 1e6, float(pricing["completion"]) * 1e6
            except (KeyError, TypeError, ValueError):
                return None
    return None


def check_price_move(baseline_in, baseline_out, current, threshold_pct):
    """current: (usd_per_m_in, usd_per_m_out) or None if the model vanished
    from the catalog. Returns a breach dict or None."""
    if current is None:
        return {
            "reason": "model missing from OpenRouter catalog",
            "move_in_pct": None,
            "move_out_pct": None,
        }
    cur_in, cur_out = current
    move_in = pct_move(baseline_in, cur_in)
    move_out = pct_move(baseline_out, cur_out)
    if abs(move_in) > threshold_pct or abs(move_out) > threshold_pct:
        return {
            "reason": "price moved beyond threshold",
            "move_in_pct": round(move_in, 1),
            "move_out_pct": round(move_out, 1),
            "current_in_usd_m": cur_in,
            "current_out_usd_m": cur_out,
        }
    return None


def fetch_catalog(url: str, timeout: float = 10.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def already_alerted(state_dir: pathlib.Path, breach: dict) -> bool:
    """Edge-trigger like litellm-auth-alert / paid_fallback_alert: only page
    once per distinct breach shape, not every run."""
    marker = state_dir / "last_breach.json"
    if not marker.exists():
        return False
    try:
        return json.loads(marker.read_text()) == breach
    except (json.JSONDecodeError, OSError):
        return False


def record_alert(state_dir: pathlib.Path, breach: dict) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "last_breach.json").write_text(json.dumps(breach))


def clear_state(state_dir: pathlib.Path) -> None:
    marker = state_dir / "last_breach.json"
    if marker.exists():
        marker.unlink()


def send_ntfy(ntfy_bin: str, topic: str, message: str) -> None:
    subprocess.run([ntfy_bin, topic, message], check=False, timeout=10)


def main(argv):
    model_id = os.environ.get("GLM53FLASH_OPENROUTER_MODEL_ID", DEFAULT_MODEL_ID)
    url = os.environ.get("GLM53FLASH_OPENROUTER_URL", DEFAULT_URL)
    threshold_pct = float(os.environ.get("GLM53FLASH_MOVE_THRESHOLD_PCT", DEFAULT_THRESHOLD_PCT))
    baseline_in = float(os.environ.get("GLM53FLASH_BASELINE_IN_USD_M", DEFAULT_BASELINE_IN))
    baseline_out = float(os.environ.get("GLM53FLASH_BASELINE_OUT_USD_M", DEFAULT_BASELINE_OUT))
    ntfy_bin = os.environ.get("GLM53FLASH_NTFY", "/home/lumen/bin/ntfy-send")
    topic = os.environ.get("GLM53FLASH_TOPIC", "lumen-fleet")
    state_dir = pathlib.Path(
        os.environ.get("GLM53FLASH_STATE_DIR", "/home/lumen/agents/glm53flash-price-watchdog")
    )

    catalog = fetch_catalog(url)
    current = find_model_pricing(catalog, model_id)
    breach = check_price_move(baseline_in, baseline_out, current, threshold_pct)

    if "--print" in argv:
        print(json.dumps({"model": model_id, "current": current, "breach": breach}, indent=2))

    if breach is None:
        clear_state(state_dir)
        return 0

    if already_alerted(state_dir, breach):
        return 0

    send_ntfy(
        ntfy_bin,
        topic,
        f"glm-5.3-flash price watchdog: {breach['reason']} "
        f"(in {breach.get('move_in_pct')}%, out {breach.get('move_out_pct')}%) "
        f"vs pinned baseline ${baseline_in}/${baseline_out} per M — re-decide routing (wq#1573)",
    )
    record_alert(state_dir, breach)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
