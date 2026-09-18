#!/usr/bin/env python3
"""wq#1295 — cap the model set LiteLLM's background_health_checks polls.

LiteLLM's `/health` (and the background poller behind
`background_health_checks: true`) calls EVERY deployment in `model_list` on
every interval. Per kb/fleet-upgrade-plan-review.md §4.3: "LiteLLM /health
calls every configured model — real tokens, real rate limits against paid
providers, and slow enough on a large model list to trip Gatus's own
timeout." kb/tooling-findings-review.md's Day-0 install order says the same
thing from the other side: poll it, but cap it.

Two classes get excluded from the health-check roster by default:
  - EXPENSIVE cloud rungs (opus-class models) — a poll every
    `health_check_interval` seconds is a standing per-model spend for a
    check that only needs to prove reachability, not correctness.
  - LOCAL/Ollama rungs — kb/fleet-upgrade-plan-review.md §4.2: a health
    check against an evicted local model forces a cold multi-GB VRAM load,
    which can stall real inference every interval. Liveness for those is
    cheaper via `/api/tags`/`/api/ps` (out of scope here), not a chat probe.

This module is the capping decision only — pure, unit-testable, no litellm
or network dependency. It does not read the live proxy config (this BUILD
worker's sandbox has no access to it — see README.md); an operator runs
`render_capped_list` against the real `model_list` on deploy.

VERIFY on first deploy: LiteLLM's actual mechanism for scoping
background_health_checks to a subset (as of the version running on the
gateway) may be a `general_settings.health_check_models` allowlist, a
per-`model_info.health_check_enabled: false` flag, or something else
entirely — same "confirm against the live proxy" caveat wq#1299/wq#1406/
wq#1590 already flagged for their own LiteLLM integration points. This
module produces the correct NAMES either way; wiring them into whichever
key the running version actually reads is the one manual step.
"""
import re

DEFAULT_EXCLUDE_PATTERNS = (
    r"opus",  # expensive cloud rung — bills every poll (anywhere in either field)
)

# Prefix patterns matched only against the underlying litellm_params.model
# string (e.g. "ollama/qwen36", "local/glm") — local/self-hosted rungs whose
# a poll can force a cold VRAM load.
DEFAULT_EXCLUDE_PREFIXES = (
    r"^ollama/",
    r"^local[-/]",
)


def is_capped_out(model_name: str, litellm_model: str = "",
                   exclude_patterns=DEFAULT_EXCLUDE_PATTERNS,
                   exclude_prefixes=DEFAULT_EXCLUDE_PREFIXES) -> bool:
    """True if this deployment should be EXCLUDED from background health
    checks. Keyword patterns match anywhere in either the caller-facing
    model_name or the underlying litellm_params.model string; prefix
    patterns match only the start of litellm_params.model (a keyword
    pattern would false-positive on operator-chosen model_name text that
    happens to contain e.g. "local"). Case-insensitive throughout."""
    name = (model_name or "").lower()
    lm = (litellm_model or "").lower()
    if any(re.search(pat, name) or re.search(pat, lm) for pat in exclude_patterns):
        return True
    return any(re.match(pat, lm) for pat in exclude_prefixes)


def render_capped_list(model_list: list, exclude_patterns=DEFAULT_EXCLUDE_PATTERNS) -> list:
    """model_list: LiteLLM's own `model_list` structure (list of dicts with
    `model_name` and `litellm_params.model`). Returns the `model_name`
    values that SHOULD be health-checked — i.e. everything not capped out.
    Order-preserving, de-duplicated (multiple deployments can share a
    model_name for load-balancing)."""
    kept = []
    for entry in model_list:
        name = entry.get("model_name", "")
        litellm_model = (entry.get("litellm_params") or {}).get("model", "")
        if is_capped_out(name, litellm_model, exclude_patterns):
            continue
        if name not in kept:
            kept.append(name)
    return kept


if __name__ == "__main__":  # pragma: no cover - manual/deploy-time use
    import json
    import sys

    if len(sys.argv) != 2:
        print("usage: health_check_model_filter.py <path-to-live-config.yaml>", file=sys.stderr)
        sys.exit(2)

    import yaml  # deploy-time only; not required for the unit tests above

    with open(sys.argv[1]) as f:
        cfg = yaml.safe_load(f)

    capped = render_capped_list(cfg.get("model_list", []))
    print(json.dumps(capped, indent=2))
