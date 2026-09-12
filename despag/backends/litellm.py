"""wq#1811: despag P1-6 — the litellm-mechanical harness's env branch.

Exactly the local-pool branch `fworker.build_env` takes for a local-pool model
(harness/fworker.py:1101-1104), read from the source rather than re-derived:

    if is_local_model(model):
        env["ANTHROPIC_BASE_URL"] = LITELLM_BASE_URL
        env["ANTHROPIC_AUTH_TOKEN"] = read_litellm_key()
        env["CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY"] = "1"

`ANTHROPIC_BASE_URL` and the key-file path use fworker's own env-var names
(FOREMAN_LITELLM_BASE_URL / FOREMAN_LITELLM_KEY_FILE) so the two processes agree on
where the gateway and its key live without despag hardcoding a second source of truth.
The key is read fresh from disk on every call (fworker.py:243 `read_litellm_key`) and
is never assigned into this module's or despag's own process env — only into the
per-call copy handed back to the caller, which becomes one `claude -p` child's env and
nothing else's (harness/fworker.py:1097-1100's T4 invariant, same requirement here).
"""
from __future__ import annotations

import os

LITELLM_BASE_URL = os.environ.get("FOREMAN_LITELLM_BASE_URL", "http://localhost:4000")
LITELLM_KEY_FILE = os.environ.get("FOREMAN_LITELLM_KEY_FILE", "/home/lumen/.secrets/foreman-litellm.token")


def read_litellm_key() -> str:
    with open(LITELLM_KEY_FILE) as f:
        return f.read().strip()


def build_env(base_env: dict, model_id: str) -> dict:
    """-> a copy of base_env with the litellm-mechanical auth branch applied.

    `base_env` is the caller's per-attempt env (never os.environ mutated in place,
    never merged back) — same discipline as fworker.build_env, which builds a fresh
    dict per attempt and hands it to exactly one child (FW:1097-1100).
    """
    env = dict(base_env)
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
    env["ANTHROPIC_BASE_URL"] = LITELLM_BASE_URL
    env["ANTHROPIC_AUTH_TOKEN"] = read_litellm_key()
    env["CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY"] = "1"
    return env
