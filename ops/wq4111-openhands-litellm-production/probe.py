#!/usr/bin/env python3
"""wq#4111: host-side, non-privileged readiness probe for the OpenHands/LiteLLM pair.

No docker, no secrets, no compose read — every check here is a plain HTTP GET/POST
against ports already published on the Lumen host, so a BUILD worker (or lumen_state,
closing wq#4007 TODO item 9: add a real `/alive` probe, NOT `/api/health`, which is SPA
HTML) can run it unprivileged.

Findings from the 2026-10-17 run this script codifies:
  - LiteLLM gateway: GET /health/readiness -> 200 (gateway itself is reachable from
    the Lumen host; this does NOT prove the openhands container's own network
    namespace can reach it, which is the still-open wq#4007 network-attach bug).
  - OpenHands UI root: GET / -> 200 (container up, port published on the host).
  - OpenHands API routing: GET /api/conversations returns the SPA's static
    index.html (text/html, same content-length as /) instead of JSON, while POST to
    the same path returns a real FastAPI 405 (application/json, "Method Not
    Allowed"). The 405 proves the API router has a route at that path; the GET
    behavior means something ahead of it (a StaticFiles(html=True) catch-all mounted
    before the API router, most likely) is swallowing GET before the router sees it.
    This is a concrete, reproducible lead on why wq#4007 saw 0 rows in openhands.db:
    the conversation-create flow may never reach the backend for a plain GET-driven
    client. Needs confirmation from inside the container (route ordering in the
    installed version's ASGI app) by a lane that can read the image, not guessed here.
"""
import json
import sys
import urllib.error
import urllib.request

LITELLM = "http://127.0.0.1:4000"
OPENHANDS = "http://127.0.0.1:3000"
TIMEOUT = 5


def _get(url, method="GET", data=None, headers=None):
    req = urllib.request.Request(url, method=method, data=data, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), e.read()
    except OSError as e:
        return None, {}, str(e).encode()


def check_litellm_ready():
    status, _, _ = _get(f"{LITELLM}/health/readiness")
    return status == 200


def check_openhands_up():
    status, _, _ = _get(f"{OPENHANDS}/")
    return status == 200


def classify_api_routing():
    """Returns one of: "ok" (GET returns JSON), "spa_fallback" (GET returns the SPA
    shell instead of JSON — the wq#4007-era routing anomaly), "down" (unreachable),
    "unknown" (neither of the above matched)."""
    status, headers, body = _get(f"{OPENHANDS}/api/conversations")
    if status is None:
        return "down"
    content_type = headers.get("content-type", "")
    if status == 200 and "application/json" in content_type:
        return "ok"
    if status == 200 and "text/html" in content_type and body.lstrip().startswith(b"<!DOCTYPE html"):
        return "spa_fallback"
    return "unknown"


def main():
    litellm_ready = check_litellm_ready()
    openhands_up = check_openhands_up()
    routing = classify_api_routing()
    print(f"litellm /health/readiness: {'OK' if litellm_ready else 'FAIL'}")
    print(f"openhands UI root: {'OK' if openhands_up else 'FAIL'}")
    print(f"openhands API routing (GET /api/conversations): {routing}")
    result = {
        "litellm_ready": litellm_ready,
        "openhands_up": openhands_up,
        "api_routing": routing,
    }
    print(json.dumps(result))
    return 0 if litellm_ready and openhands_up else 1


if __name__ == "__main__":
    sys.exit(main())
