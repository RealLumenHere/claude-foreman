#!/usr/bin/env python3
"""wq#1406 — alert when a request is served via a PAID fallback deployment.

The DeepSeek/LPP runaway (Sep 1-2 2026): Cerebras 402'd, the router silently
fell through to deepseek-v4-flash, and nothing paged — $23/day burned for
two days before anyone noticed by reading a bill. This closes that hole at
the gateway: any request LiteLLM serves off a non-primary, PAID deployment
fires ntfy ("X is down, serving via paid Y") so a silent multi-day drain is
impossible, independent of whether the caller (LPP, foreman, etc.) has its
own client-side alerting.

Deploy: drop next to LiteLLM's config (PYTHONPATH), then in the proxy
config.yaml (see litellm_config_snippet.yaml):

    litellm_settings:
      success_callback: [..., "paid_fallback_alert.paid_fallback_logger"]

Env:
  PAID_FALLBACK_NTFY          path to ntfy-send (default /home/lumen/bin/ntfy-send)
  PAID_FALLBACK_TOPIC         ntfy topic (default lumen-alerts)
  PAID_FALLBACK_COOLDOWN_MIN  edge-trigger cooldown per model_group, minutes
                               (default 30) — same "alert once per breach,
                               not every call" contract as wq#911's
                               litellm-auth-alert, so a primary that stays
                               down for hours doesn't spam.
  PAID_FALLBACK_STATE_DIR     state dir (default /home/lumen/agents/paid-fallback-alert)

VERIFY on first deploy (same caveat as wq#1299's phoenix sampler): the exact
kwargs shape LiteLLM's success callback receives is version-dependent.
Requested-group vs served-model is expected at
kwargs["litellm_params"]["metadata"]["model_group"] (requested) and
kwargs.get("model") or response_obj.model (served) per LiteLLM's documented
custom-callback contract as of training cutoff — confirm against the live
proxy the first time a real fallback fires (force one by killing a primary
deployment) and adjust `extract_model_group`/`extract_served_model` if the
keys have moved.
"""
import json
import logging
import os
import pathlib
import subprocess
import time

logger = logging.getLogger("paid_fallback_alert")

try:
    from litellm.integrations.custom_logger import CustomLogger
    _HAVE_LITELLM = True
except ImportError:  # pragma: no cover - exercised only off-gateway (tests, dev)
    CustomLogger = object
    _HAVE_LITELLM = False

PAID_PROVIDER_PREFIXES = (
    "deepseek/", "openrouter/", "mistral/", "anthropic/", "openai/", "cerebras/",
)


def is_paid_model(litellm_model: str) -> bool:
    """Pure classification, shared rule with audit_fallback_chains.py."""
    m = (litellm_model or "").strip().lower()
    return any(m.startswith(p) for p in PAID_PROVIDER_PREFIXES)


def is_fallback_response(requested_model_group: str, served_deployment_group: str) -> bool:
    """True when the deployment that actually served the call is not the
    group the caller asked for — i.e. a fallback happened."""
    if not requested_model_group or not served_deployment_group:
        return False
    return requested_model_group != served_deployment_group


def should_alert(state: dict, key: str, now: float, cooldown_s: float) -> bool:
    """Edge-triggered: True the first time `key` breaches, and again only
    after `cooldown_s` of no repeat check-in — same contract as wq#911's
    litellm-auth-alert (alert once per breach, not once per request)."""
    last = state.get(key)
    if last is None:
        return True
    return (now - last) >= cooldown_s


def _state_path() -> pathlib.Path:
    state_dir = pathlib.Path(
        os.environ.get("PAID_FALLBACK_STATE_DIR", "/home/lumen/agents/paid-fallback-alert")
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
        logger.warning("paid_fallback_alert: failed to persist state", exc_info=True)


def notify_paid_fallback(requested_model_group: str, served_model: str) -> None:
    """Fire-and-forget ntfy post. Never raises into the caller's path
    (fails-open, same guardrail as wq#1299's exporter wrapping)."""
    ntfy = os.environ.get("PAID_FALLBACK_NTFY", "/home/lumen/bin/ntfy-send")
    topic = os.environ.get("PAID_FALLBACK_TOPIC", "lumen-alerts")
    msg = (
        f"{requested_model_group} is down, serving via paid fallback {served_model}. "
        f"Check the primary deployment; this will keep billing until it recovers "
        f"(re-alerts every {os.environ.get('PAID_FALLBACK_COOLDOWN_MIN', '30')}min "
        f"while the breach persists)."
    )
    try:
        subprocess.run(
            [ntfy, "-t", topic, "-p", "urgent", "-T", "LiteLLM paid fallback in use",
             "--quiet", msg],
            timeout=15, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        logger.warning("paid_fallback_alert: ntfy dispatch failed", exc_info=True)


def handle_success_event(requested_model_group: str, served_model_group: str, served_model: str) -> bool:
    """Core decision + side-effect, factored out of the async hook so it's
    unit-testable without litellm installed. Returns True if it alerted."""
    if not is_fallback_response(requested_model_group, served_model_group):
        return False
    if not is_paid_model(served_model):
        return False
    cooldown_s = float(os.environ.get("PAID_FALLBACK_COOLDOWN_MIN", "30")) * 60
    path = _state_path()
    state = _load_state(path)
    now = time.time()
    if not should_alert(state, requested_model_group, now, cooldown_s):
        return False
    state[requested_model_group] = now
    _save_state(path, state)
    notify_paid_fallback(requested_model_group, served_model)
    return True


def extract_model_group(kwargs: dict) -> str:
    metadata = ((kwargs.get("litellm_params") or {}).get("metadata")) or {}
    return metadata.get("model_group") or kwargs.get("model") or ""


def extract_served_model(kwargs: dict, response_obj) -> str:
    return kwargs.get("model") or getattr(response_obj, "model", "") or ""


if _HAVE_LITELLM:

    class PaidFallbackAlertLogger(CustomLogger):
        """Registered as a success_callback. Compares the requested
        model_group to what actually served the call; alerts (edge-triggered,
        fails-open) when the server was a paid deployment and the group
        differs from what was requested."""

        async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
            try:
                requested = extract_model_group(kwargs)
                served = extract_served_model(kwargs, response_obj)
                handle_success_event(requested, served, served)
            except Exception:
                logger.warning("paid_fallback_alert: failed to process success event", exc_info=True)

    paid_fallback_logger = PaidFallbackAlertLogger()
else:
    paid_fallback_logger = None
