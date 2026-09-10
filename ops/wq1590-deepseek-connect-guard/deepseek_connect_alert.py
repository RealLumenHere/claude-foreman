#!/usr/bin/env python3
"""wq#1590 — page when deepseek-v4-flash's connection failure rate spikes.

FLEET_COST_PLAN.md D4: 94,339 failures out of 159,474 requests (59.2%) to
deepseek-v4-flash over 35 days, all `litellm.InternalServerError
DeepseekException - Cannot connect to host api.deepseek.com:443`. Failed
attempts bill nothing directly, but they drive the retry+fallback machinery
that does (see wq#1406's paid_fallback_alert.py, the same directory-shaped
guard for the fallback side of that machinery). Nobody paged on this for 35
days because nothing was watching failures on this rung in real time — it
only surfaced by reading a bill after the fact.

This closes that gap the same way wq#1406/wq#718 already close its neighbors:
a small windowed-rate breaker (deliberately mirroring
dispatcher/circuit_breaker.py's MIN_SAMPLES + FAILURE_RATE_THRESHOLD shape,
applied here to per-call connect failures instead of fworker attempts)
wired into LiteLLM as a pair of success/failure callbacks, so a sustained
connect-failure spike on deepseek pages the same day it starts instead of
the same month it's billed.

Deploy: drop next to LiteLLM's config (PYTHONPATH), then in the proxy
config.yaml (see litellm_config_snippet.yaml):

    litellm_settings:
      success_callback: [..., "deepseek_connect_alert.deepseek_connect_logger"]
      failure_callback: [..., "deepseek_connect_alert.deepseek_connect_logger"]

Env:
  DEEPSEEK_ALERT_NTFY          path to ntfy-send (default /home/lumen/bin/ntfy-send)
  DEEPSEEK_ALERT_TOPIC         ntfy topic (default lumen-alerts)
  DEEPSEEK_ALERT_COOLDOWN_MIN  edge-trigger cooldown, minutes (default 30) —
                                same "alert once per breach, not every call"
                                contract as wq#1406/wq#911.
  DEEPSEEK_ALERT_WINDOW_MIN    trailing window for the rate, minutes (default 90,
                                same default as dispatcher/circuit_breaker.py)
  DEEPSEEK_ALERT_MIN_SAMPLES   samples required before a rate is trusted (default 15)
  DEEPSEEK_ALERT_THRESHOLD     failure-rate trip point, 0..1 (default 0.5 — below
                                the 0.592 this ticket measured, so the real incident
                                would have tripped it with margin)
  DEEPSEEK_ALERT_STATE_DIR     state dir (default /home/lumen/agents/deepseek-connect-alert)

VERIFY on first deploy (same caveat as wq#1299/wq#1406): the exact kwargs
shape LiteLLM's success/failure callbacks receive, and where the raised
exception's string lives on the failure path (tried here as
str(kwargs.get("exception")) with a fallback to the traceback string LiteLLM
also passes), are version-dependent — confirm against the live proxy and
adjust `extract_error_str` if the keys have moved.
"""
import json
import logging
import os
import pathlib
import re
import subprocess
import time

logger = logging.getLogger("deepseek_connect_alert")

try:
    from litellm.integrations.custom_logger import CustomLogger
    _HAVE_LITELLM = True
except ImportError:  # pragma: no cover - exercised only off-gateway (tests, dev)
    CustomLogger = object
    _HAVE_LITELLM = False

# The exact failure class this ticket measured, plus the other connect-level
# shapes the same underlying problem (DNS/TLS/TCP to api.deepseek.com not
# completing) can surface as. Deliberately NOT matching on 4xx/5xx application
# errors (bad key, rate limit, content policy) — those aren't "can't connect."
CONNECT_FAILURE_PATTERNS = re.compile(
    r"cannot connect to host"
    r"|connection refused"
    r"|connection reset"
    r"|connection timed out"
    r"|timed out"
    r"|sslerror"
    r"|ssl.*handshake"
    r"|name or service not known"
    r"|temporary failure in name resolution",
    re.IGNORECASE,
)


def is_connect_failure(error_str: str) -> bool:
    """Pure classification: does this exception text describe a connect-level
    failure (DNS/TCP/TLS never completing) rather than an application error?"""
    return bool(CONNECT_FAILURE_PATTERNS.search(error_str or ""))


def is_deepseek_model(litellm_model: str) -> bool:
    """Pure classification, same shape as wq#1406's is_paid_model."""
    m = (litellm_model or "").strip().lower()
    return m.startswith("deepseek/") or m.startswith("deepseek-")


def should_alert(state: dict, key: str, now: float, cooldown_s: float) -> bool:
    """Edge-triggered: True the first time `key` breaches, and again only
    after `cooldown_s` of no repeat check-in — same contract as wq#1406's
    paid_fallback_alert.should_alert."""
    last = state.get(key)
    if last is None:
        return True
    return (now - last) >= cooldown_s


def _state_dir() -> pathlib.Path:
    d = pathlib.Path(
        os.environ.get("DEEPSEEK_ALERT_STATE_DIR", "/home/lumen/agents/deepseek-connect-alert")
    )
    d.mkdir(parents=True, exist_ok=True)
    return d


def _events_path() -> pathlib.Path:
    return _state_dir() / "events.json"


def _alert_state_path() -> pathlib.Path:
    return _state_dir() / "alert_state.json"


def _load_json(path: pathlib.Path, default):
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def _save_json(path: pathlib.Path, obj) -> None:
    try:
        path.write_text(json.dumps(obj))
    except Exception:
        logger.warning("deepseek_connect_alert: failed to persist %s", path, exc_info=True)


def prune_events(events: list, now: float, window_s: float) -> list:
    since = now - window_s
    return [e for e in events if e[0] >= since]


def windowed_rate(events: list) -> dict:
    total = len(events)
    failures = sum(1 for _ts, is_failure in events if is_failure)
    rate = (failures / total) if total else 0.0
    return {"total": total, "failures": failures, "rate": rate}


def record_event(is_failure: bool, now: float = None) -> dict:
    """Append one (timestamp, is_failure) sample for a deepseek call, prune to
    the trailing window, persist, and return the resulting windowed stats."""
    now = now if now is not None else time.time()
    window_s = float(os.environ.get("DEEPSEEK_ALERT_WINDOW_MIN", "90")) * 60
    path = _events_path()
    events = _load_json(path, [])
    events.append([now, bool(is_failure)])
    events = prune_events(events, now, window_s)
    _save_json(path, events)
    return windowed_rate(events)


def notify_connect_spike(stats: dict) -> None:
    """Fire-and-forget ntfy post. Never raises into the caller's path (fails-open,
    same guardrail as wq#1299/wq#1406)."""
    ntfy = os.environ.get("DEEPSEEK_ALERT_NTFY", "/home/lumen/bin/ntfy-send")
    topic = os.environ.get("DEEPSEEK_ALERT_TOPIC", "lumen-alerts")
    window_min = os.environ.get("DEEPSEEK_ALERT_WINDOW_MIN", "90")
    cooldown_min = os.environ.get("DEEPSEEK_ALERT_COOLDOWN_MIN", "30")
    msg = (
        f"deepseek-v4-flash connect-failure rate {stats['failures']}/{stats['total']} "
        f"({stats['rate']:.0%}) over the last {window_min}min — api.deepseek.com "
        f"connectivity looks unhealthy, not just an application error. Re-alerts "
        f"every {cooldown_min}min while it persists."
    )
    try:
        subprocess.run(
            [ntfy, "-t", topic, "-p", "urgent", "-T", "deepseek-v4-flash connect failures spiking",
             "--quiet", msg],
            timeout=15, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        logger.warning("deepseek_connect_alert: ntfy dispatch failed", exc_info=True)


def handle_call_event(litellm_model: str, error_str: str = None, now: float = None) -> bool:
    """Core decision + side-effect for one completed (success or failed) call
    to a deepseek deployment. Returns True if it alerted. Factored out of the
    async hooks so it's unit-testable without litellm installed."""
    if not is_deepseek_model(litellm_model):
        return False
    is_failure = is_connect_failure(error_str) if error_str else False
    stats = record_event(is_failure, now=now)
    min_samples = int(os.environ.get("DEEPSEEK_ALERT_MIN_SAMPLES", "15"))
    threshold = float(os.environ.get("DEEPSEEK_ALERT_THRESHOLD", "0.5"))
    if stats["total"] < min_samples or stats["rate"] <= threshold:
        return False
    now = now if now is not None else time.time()
    cooldown_s = float(os.environ.get("DEEPSEEK_ALERT_COOLDOWN_MIN", "30")) * 60
    alert_path = _alert_state_path()
    alert_state = _load_json(alert_path, {})
    if not should_alert(alert_state, litellm_model, now, cooldown_s):
        return False
    alert_state[litellm_model] = now
    _save_json(alert_path, alert_state)
    notify_connect_spike(stats)
    return True


def extract_model(kwargs: dict) -> str:
    return kwargs.get("model") or ""


def extract_error_str(kwargs: dict) -> str:
    exc = kwargs.get("exception")
    if exc is not None:
        return str(exc)
    return str(kwargs.get("traceback_exception") or "")


if _HAVE_LITELLM:

    class DeepseekConnectAlertLogger(CustomLogger):
        """Registered as both success_callback and failure_callback — the
        rate's denominator needs successes too, not just failures."""

        async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
            try:
                handle_call_event(extract_model(kwargs))
            except Exception:
                logger.warning("deepseek_connect_alert: failed to process success event", exc_info=True)

        async def async_log_failure_event(self, kwargs, response_obj, start_time, end_time):
            try:
                handle_call_event(extract_model(kwargs), extract_error_str(kwargs))
            except Exception:
                logger.warning("deepseek_connect_alert: failed to process failure event", exc_info=True)

    deepseek_connect_logger = DeepseekConnectAlertLogger()
else:
    deepseek_connect_logger = None
