#!/usr/bin/env python3
"""llm.py — LiteLLM gateway client for Prompt Forge's two smart steps (wq#957).

The LLM is used for exactly two things (Option B boundary, PROMPT_FORGE_DESIGN.md):
clarifying questions and the project-specific framing section. Every fleet fact in
the output prompt comes from fleet_context.py, never from here.

Key resolution follows docs/litellm/service_keys.md — env first, then the named
service-key file. The master key is never used. When no key or no gateway is
available every call returns None and the caller degrades to deterministic-only
output; a dead gateway must never mean a dead prompt generator.

Stdlib-only: urllib against the OpenAI-compatible /v1/chat/completions route.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

GATEWAY = os.environ.get("PROMPT_FORGE_LITELLM_URL", "http://127.0.0.1:4000")
MODEL = os.environ.get("PROMPT_FORGE_MODEL", "claude-sonnet-5")
KEY_FILE = Path(
    os.environ.get(
        "PROMPT_FORGE_KEY_FILE",
        "/home/lumen/.secrets/litellm-service-keys/prompt-forge.key",
    )
)
TIMEOUT_S = 60


def _api_key() -> str | None:
    key = os.environ.get("LITELLM_API_KEY", "").strip()
    if key:
        return key
    try:
        key = KEY_FILE.read_text().strip()
    except OSError:
        return None
    return key or None


def available() -> bool:
    return _api_key() is not None


def chat(system: str, user: str, max_tokens: int = 1500) -> str | None:
    """One completion; None on any failure (caller degrades gracefully)."""
    key = _api_key()
    if not key:
        return None
    body = json.dumps({
        "model": MODEL,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }).encode()
    req = urllib.request.Request(
        f"{GATEWAY}/v1/chat/completions",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            data = json.loads(resp.read())
        return data["choices"][0]["message"]["content"]
    except (urllib.error.URLError, OSError, KeyError, IndexError, ValueError):
        return None


def extract_json(text: str) -> dict | None:
    """Pull the first JSON object out of a model reply (handles ``` fences)."""
    if not text:
        return None
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    for i, ch in enumerate(text[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    obj = json.loads(text[start : i + 1])
                except ValueError:
                    return None
                return obj if isinstance(obj, dict) else None
    return None
