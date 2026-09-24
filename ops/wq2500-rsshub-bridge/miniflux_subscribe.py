#!/usr/bin/env python3
"""wq#2500 — subscribe the RSSHub velocity-feed routes to Miniflux.

Adds two feeds via Miniflux's REST API (POST /v1/feeds), never `docker exec`
(harness/guard.sh denies the container CLI to workers except ps/logs,
kb/ISSUE-LOG.md #653) — the API call is the one remote action this bundle can run
unattended, since it only needs network, not root on Sia.

Credentials: MINIFLUX_BASE_URL and MINIFLUX_API_KEY must already be in the
environment when this runs. This script never reads a credential file itself —
same convention as bin/fleet-update-check:8-9 (GH_TOKEN): the caller reads the 0600
file and exports the value, so no token ever appears in this script's argv or source.
"""
import json
import os
import sys
import urllib.error
import urllib.request

# The two routes acceptance criterion 2 requires. RSSHUB_BASE defaults to loopback
# because Miniflux (Sia) and RSSHub (this bundle) are co-located on the same box.
RSSHUB_BASE = os.environ.get("RSSHUB_BASE", "http://127.0.0.1:1200")
FEEDS = [
    {
        "feed_url": f"{RSSHUB_BASE}/github/trending/daily/python",
        "category_title": "velocity-feed",
    },
    {
        # LiteLLM: the fleet's LLM gateway, already self-hosted
        # (research/FLEET_SERVICE_ATLAS.md:33, litellm:4000 docker container).
        "feed_url": f"{RSSHUB_BASE}/github/release/BerriAI/litellm",
        "category_title": "velocity-feed",
    },
]


def _request(base_url, api_key, method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=data,
        method=method,
        headers={"X-Auth-Token": api_key, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def _category_id(base_url, api_key, title):
    status, cats = _request(base_url, api_key, "GET", "/v1/categories")
    if status != 200:
        raise RuntimeError(f"GET /v1/categories -> {status}: {cats}")
    for c in cats:
        if c.get("title") == title:
            return c["id"]
    status, cat = _request(base_url, api_key, "POST", "/v1/categories", {"title": title})
    if status not in (200, 201):
        raise RuntimeError(f"POST /v1/categories -> {status}: {cat}")
    return cat["id"]


def subscribe(base_url, api_key, feed_url, category_id):
    status, body = _request(
        base_url, api_key, "POST", "/v1/feeds",
        {"feed_url": feed_url, "category_id": category_id},
    )
    if status == 201:
        return "created", body
    if status == 400 and "already exists" in json.dumps(body).lower():
        return "already-subscribed", body
    raise RuntimeError(f"POST /v1/feeds {feed_url} -> {status}: {body}")


def main():
    base_url = os.environ.get("MINIFLUX_BASE_URL")
    api_key = os.environ.get("MINIFLUX_API_KEY")
    if not base_url or not api_key:
        print("MINIFLUX_BASE_URL and MINIFLUX_API_KEY must both be set in the "
              "environment (never as CLI args — they'd land in argv/ps).", file=sys.stderr)
        return 2

    category_id = _category_id(base_url, api_key, FEEDS[0]["category_title"])
    exit_code = 0
    for feed in FEEDS:
        try:
            outcome, body = subscribe(base_url, api_key, feed["feed_url"], category_id)
            print(f"{outcome}: {feed['feed_url']} (feed_id={body.get('feed_id', body.get('id'))})")
        except RuntimeError as e:
            print(f"FAILED: {feed['feed_url']}: {e}", file=sys.stderr)
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
