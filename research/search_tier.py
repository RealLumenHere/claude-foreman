#!/usr/bin/env python3
"""search_tier — tiered research search for trigger.py (wq#4232).

Order, cheapest/most-available first:
  1. DeepSeek Search (`/v1/tools/search`) — the fleet's keyed primary engine
     (same endpoint/key fleet-search's `deepseek_search()` already uses,
     bin/fleet-search:343). Tried first for ~90% of lookups per the ticket.
  2. stealth-browser — a real anti-bot browser, but it is an MCP server
     (ops/wq4206-stealth-browser-mcp), not a shell-callable binary: a bare
     script like trigger.py has no MCP client. This tier is therefore a
     caller-injected hook (`browser_fetch`) rather than a subprocess — an
     agent session holding the stealth-browser MCP tools passes one in; a
     plain cron-driven trigger.py run has none and falls through tier 2.
  3. SearXNG, via the fleet's existing fleet-search CLI (wq#999) — kept as
     the final fallback until DeepSeek + stealth-browser are proven stable,
     per this ticket's retirement clause.

Every tier is fail-soft (wq#632/§1 pattern used throughout this package):
no key, no network, or a bad reply falls through to the next tier instead
of raising. `tiered_search()` returns (results, tier_used) so callers and
tests can see which tier actually answered.
"""
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))

DEEPSEEK_SEARCH_URL = os.environ.get(
    "DEEPSEEK_SEARCH_URL", "https://api.deepseek.com/v1/tools/search")
DEEPSEEK_KEY_FILE = os.environ.get(
    "DEEPSEEK_KEY_FILE", "/home/lumen/.secrets/deepseek.token")
FLEET_SEARCH_BIN = os.environ.get("FLEET_SEARCH_BIN", "/home/lumen/bin/fleet-search")
TIMEOUT_S = 5


def _deepseek_key(key_file=DEEPSEEK_KEY_FILE):
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if key:
        return key
    try:
        with open(key_file) as f:
            return f.read().strip() or None
    except OSError:
        return None


def deepseek_search(query, n=6, url=DEEPSEEK_SEARCH_URL, key_file=DEEPSEEK_KEY_FILE,
                     timeout=TIMEOUT_S):
    """Tier 1. None on any failure (no key, network, 401/402/429, bad JSON) —
    callers fall through to the next tier rather than treating this as fatal."""
    key = _deepseek_key(key_file)
    if not key:
        return None
    body = json.dumps({"query": query, "num_results": n}).encode()
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return None
            data = json.loads(resp.read())
    except (urllib.error.URLError, OSError, ValueError):
        return None
    results = data.get("results")
    return results if isinstance(results, list) and results else None


def searxng_search(query, n=6, fleet_search_bin=FLEET_SEARCH_BIN, timeout=TIMEOUT_S):
    """Tier 3 (final fallback) — shells out to fleet-search's existing
    SearXNG-backed `web` source (wq#999) rather than re-implementing it."""
    if not os.path.exists(fleet_search_bin):
        return None
    cmd = [sys.executable, fleet_search_bin, "web", query, "--n", str(n)]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        data = json.loads(out.stdout)
    except (subprocess.SubprocessError, ValueError, OSError):
        return None
    if not data.get("ok"):
        return None
    results = data.get("results")
    return results if isinstance(results, list) and results else None


def tiered_search(query, n=6, browser_fetch=None):
    """DeepSeek Search -> stealth-browser (only when the caller injected
    `browser_fetch`) -> SearXNG. Returns (results, tier) where tier is one of
    "deepseek", "stealth-browser", "searxng", or "none"."""
    results = deepseek_search(query, n)
    if results:
        return results, "deepseek"
    if browser_fetch is not None:
        results = browser_fetch(query, n)
        if results:
            return results, "stealth-browser"
    results = searxng_search(query, n)
    if results:
        return results, "searxng"
    return [], "none"


def findings_block(query, n=6, search=tiered_search):
    """Render tiered_search's output as a brief-ready text block, or "" if
    every tier came up empty — an absent section, not a misleading empty one."""
    results, tier = search(query, n)
    if not results:
        return ""
    lines = [f"Search findings (tier: {tier}):"]
    for r in results[:n]:
        if not isinstance(r, dict):
            continue
        title = r.get("title") or r.get("url") or ""
        url = r.get("url") or ""
        snippet = r.get("snippet") or r.get("content") or ""
        lines.append(f"- {title} ({url}): {snippet}".strip())
    return "\n".join(lines) + "\n" if len(lines) > 1 else ""
