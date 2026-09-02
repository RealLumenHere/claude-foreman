#!/usr/bin/env python3
"""wq#1299 — Phoenix tracer on the gateway node via LiteLLM OTLP.

LiteLLM's built-in OpenTelemetry logger has one sampler for every event; a
head-based OTel sampler can't decide "trace all errors" because the outcome
isn't known until the call finishes. This subclass makes that decision where
the outcome IS known — in the success/failure callback hooks themselves —
and delegates to the real LiteLLM OTel logger (whatever exporter batching and
fail-open behavior it has) for anything that passes the gate.

Deploy: drop next to LiteLLM's config (or anywhere on its PYTHONPATH), then
in the proxy config.yaml (see litellm_config_snippet.yaml):

    litellm_settings:
      success_callback: ["litellm_phoenix_sampler.phoenix_logger"]
      failure_callback: ["litellm_phoenix_sampler.phoenix_logger"]

Env:
  PHOENIX_SAMPLE_RATE   fraction of successful calls to trace (default 0.2)
  PHOENIX_OTLP_ENDPOINT OTLP/HTTP endpoint for the Phoenix collector
                         (default http://127.0.0.1:6006/v1/traces — loopback,
                         co-located with the gateway per kb/fleet-upgrade-plan-review.md)
"""
import logging
import os
import random

logger = logging.getLogger("litellm_phoenix_sampler")

try:
    from litellm.integrations.opentelemetry import OpenTelemetry, OpenTelemetryConfig
    _HAVE_LITELLM = True
except ImportError:  # pragma: no cover - exercised only off-gateway (tests, dev)
    OpenTelemetry = object
    OpenTelemetryConfig = None
    _HAVE_LITELLM = False


def should_sample_success(rate: float, rng=random.random) -> bool:
    """Pure decision function, unit-testable without litellm/opentelemetry
    installed. rate <= 0 never samples, rate >= 1 always samples."""
    if rate <= 0:
        return False
    if rate >= 1:
        return True
    return rng() < rate


class PhoenixSamplingLogger(OpenTelemetry):
    """success events: sampled; failure events: always traced.

    Wrapped in try/except so a Phoenix/OTLP outage degrades to "no trace"
    rather than a broken gateway call — the ticket's fails-open guardrail
    enforced in code rather than assumed from LiteLLM's own behavior
    (VERIFY: LiteLLM's callback dispatch already isolates callback
    exceptions from the response path, per kb/fleet-upgrade-plan-review.md
    §3 — this is belt-and-suspenders on top of that, not a substitute for
    confirming it against the live proxy log the first time an OTLP call
    fails).
    """

    def __init__(self, sample_rate: float = None, **kwargs):
        if not _HAVE_LITELLM:
            raise RuntimeError(
                "litellm is not installed — this class only runs inside the "
                "LiteLLM proxy process; use should_sample_success() directly "
                "for tests."
            )
        endpoint = os.environ.get(
            "PHOENIX_OTLP_ENDPOINT", "http://127.0.0.1:6006/v1/traces"
        )
        config = OpenTelemetryConfig(exporter="otlp_http", endpoint=endpoint)
        super().__init__(config=config, **kwargs)
        self.sample_rate = (
            sample_rate
            if sample_rate is not None
            else float(os.environ.get("PHOENIX_SAMPLE_RATE", "0.2"))
        )

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        if not should_sample_success(self.sample_rate):
            return
        try:
            await super().async_log_success_event(
                kwargs, response_obj, start_time, end_time
            )
        except Exception:
            logger.warning("phoenix export failed for success event", exc_info=True)

    async def async_log_failure_event(self, kwargs, response_obj, start_time, end_time):
        try:
            await super().async_log_failure_event(
                kwargs, response_obj, start_time, end_time
            )
        except Exception:
            logger.warning("phoenix export failed for failure event", exc_info=True)


phoenix_logger = PhoenixSamplingLogger() if _HAVE_LITELLM else None
