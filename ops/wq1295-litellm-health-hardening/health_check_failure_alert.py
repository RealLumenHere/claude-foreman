#!/usr/bin/env python3
"""wq#1295 — page ntfy when LiteLLM's background health check finds a
deployment unhealthy.

Companion to `health_check_model_filter.py` (which caps WHICH models get
polled) and `litellm_config_snippet.yaml` (which turns polling on). This is
the other half: background_health_checks with nothing subscribed to its
result is exactly the "logs failures but never watches them" pattern
kb/tooling-findings-review.md opens with. Registered as a `failure_callback`
so it fires on any failed call — including the health-check poller's own
synthetic calls, per LiteLLM's documented behavior of routing background
health-check failures through the same failure_callback path as real
request failures.

Deploy: drop next to LiteLLM's config (PYTHONPATH), then in the proxy
config.yaml (see litellm_config_snippet.yaml):

    litellm_settings:
      failure_callback: [..., "health_check_failure_alert.health_check_alert_logger"]

Env:
  HEALTH_CHECK_ALERT_NTFY          path to ntfy-send (default /home/lumen/bin/ntfy-send)
  HEALTH_CHECK_ALERT_TOPIC         ntfy topic (default lumen-alerts)
  HEALTH_CHECK_ALERT_COOLDOWN_MIN  edge-trigger cooldown per model, minutes
                                    (default 30) — same "alert once per
                                    breach, not once per request" contract as
                                    wq#911/wq#1406, so a deployment down for
                                    hours doesn't spam every poll interval.
  HEALTH_CHECK_ALERT_STATE_DIR     state dir (default /home/lumen/agents/health-check-alert)

VERIFY on first deploy (same caveat as wq#1299/wq#1406/wq#1590): the exact
kwargs shape LiteLLM's failure callback receives, and whether background
health-check failures carry a distinguishing marker (vs. a real request
failure), is version-dependent — confirm against the live proxy the first
time a deployment is taken down deliberately, and adjust
`extract_model_name`/`extract_error_reason` if the keys have moved.
"""
import json
import logging
import os
import pathlib
import subprocess
import time

logger = logging.getLogger("health_check_failure_alert")

try:
    from litellm.integrations.custom_logger import CustomLogger
    _HAVE_LITELLM = True
except ImportError:  # pragma: no cover - exercised only off-gateway (tests, dev)
    CustomLogger = object
    _HAVE_LITELLM = False


def should_alert(state: dict, key: str, now: float, cooldown_s: float) -> bool:
    """Edge-triggered: True the first time `key` breaches, and again only
    after `cooldown_s` of no repeat check-in."""
    last = state.get(key)
    if last is None:
        return True
    return (now - last) >= cooldown_s


def _state_path() -> pathlib.Path:
    state_dir = pathlib.Path(
        os.environ.get("HEALTH_CHECK_ALERT_STATE_DIR", "/home/lumen/agents/health-check-alert")
    )
    state_dir.mkdir(parents=True, exist_ok=True)
    return state_dir / "state.json"


def _load_state(path: pathlib.Path) -> dict:
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def _save_state(path: pathlib.Path, state: dict) -> None:
    try:
        path.write_text(json.dumps(state))
    except Exception:
        logger.warning("health_check_failure_alert: failed to persist state", exc_info=True)


def notify_unhealthy(model_name: str, reason: str) -> None:
    """Fire-and-forget ntfy post. Never raises into the caller's path
    (fails-open, same guardrail as wq#1299/wq#1406)."""
    ntfy = os.environ.get("HEALTH_CHECK_ALERT_NTFY", "/home/lumen/bin/ntfy-send")
    topic = os.environ.get("HEALTH_CHECK_ALERT_TOPIC", "lumen-alerts")
    cooldown = os.environ.get("HEALTH_CHECK_ALERT_COOLDOWN_MIN", "30")
    msg = (
        f"LiteLLM health check: {model_name} is unhealthy ({reason}). "
        f"Re-alerts every {cooldown}min while the breach persists."
    )
    try:
        subprocess.run(
            [ntfy, "-t", topic, "-p", "urgent", "-T", "LiteLLM deployment unhealthy",
             "--quiet", msg],
            timeout=15, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        logger.warning("health_check_failure_alert: ntfy dispatch failed", exc_info=True)


def handle_failure_event(model_name: str, reason: str) -> bool:
    """Core decision + side-effect, factored out of the async hook so it's
    unit-testable without litellm installed. Returns True if it alerted."""
    if not model_name:
        return False
    cooldown_s = float(os.environ.get("HEALTH_CHECK_ALERT_COOLDOWN_MIN", "30")) * 60
    path = _state_path()
    state = _load_state(path)
    now = time.time()
    if not should_alert(state, model_name, now, cooldown_s):
        return False
    state[model_name] = now
    _save_state(path, state)
    notify_unhealthy(model_name, reason)
    return True


def extract_model_name(kwargs: dict) -> str:
    metadata = ((kwargs.get("litellm_params") or {}).get("metadata")) or {}
    return metadata.get("model_group") or kwargs.get("model") or ""


def extract_error_reason(kwargs: dict) -> str:
    exc = kwargs.get("exception")
    if exc is not None:
        return str(exc)[:200]
    return kwargs.get("traceback_exception", "unknown error")[:200] if kwargs.get("traceback_exception") else "unknown error"


if _HAVE_LITELLM:

    class HealthCheckFailureAlertLogger(CustomLogger):
        """Registered as a failure_callback. Extracts the failing model and
        reason, then alerts (edge-triggered, fails-open) — the alerting
        half of Day-0's background_health_checks item."""

        async def async_log_failure_event(self, kwargs, response_obj, start_time, end_time):
            try:
                model_name = extract_model_name(kwargs)
                reason = extract_error_reason(kwargs)
                handle_failure_event(model_name, reason)
            except Exception:
                logger.warning("health_check_failure_alert: failed to process failure event", exc_info=True)

    health_check_alert_logger = HealthCheckFailureAlertLogger()
else:
    health_check_alert_logger = None
