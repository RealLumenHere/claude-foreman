#!/usr/bin/env python3
"""wq#4018: report, by hash only, whether the OpenHands compose still carries the
burned LiteLLM key and whether the gateway still accepts it.

Never prints the key: only the 12-hex sha256 mask_replay keys rotations on, the
gateway's HTTP status for that key, and the (non-secret) base URL it targets.
Exit 0 once the burned value is gone or the gateway rejects it (401/403).
"""
import hashlib
import re
import sys
import urllib.error
import urllib.request

COMPOSE = "/home/lumen/containers/openhands/docker-compose.yml"
BURNED_SHA12 = "edce1aefdbdd"
GATEWAY = "http://localhost:4000"


def compose_env(path=COMPOSE):
    env = {}
    with open(path) as fh:
        for line in fh:
            m = re.match(r"\s*-?\s*(LLM_[A-Z_]+)\s*[=:]\s*(\S+)", line)
            if m:
                env[m.group(1)] = m.group(2).strip("'\"")
    return env


def sha12(key):
    # masking.py hashes the value AFTER the kept prefix (`sk-`), so mask_replay's
    # value_sha256_12 names the body, not the whole key.
    body = key[3:] if key.startswith("sk-") else key
    return hashlib.sha256(body.encode()).hexdigest()[:12]


def gateway_status(key):
    req = urllib.request.Request(f"{GATEWAY}/key/info",
                                 headers={"Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code
    except OSError as e:
        return f"unreachable ({type(e).__name__})"


def scan_copies(path=COMPOSE):
    """Hash LLM_API_KEY in every compose copy beside the live file (backups the
    wq#4007 fix left), so the burned value can be found and tested by hash."""
    import os
    import time
    top = os.path.dirname(path)
    seen = {}
    for name in sorted(os.listdir(top)):
        p = os.path.join(top, name)
        if not (name.startswith("docker-compose") and os.path.isfile(p)):
            continue
        key = compose_env(p).get("LLM_API_KEY")
        mtime = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(os.path.getmtime(p)))
        h = sha12(key) if key else None
        print(f"{name} mtime={mtime} key_sha12={h}")
        if key and h not in seen:
            seen[h] = key
    for h, key in seen.items():
        print(f"  key_sha12={h} gateway={gateway_status(key)}")
    return seen


def main():
    if "--copies" in sys.argv:
        seen = scan_copies()
        live = BURNED_SHA12 in seen and gateway_status(seen[BURNED_SHA12]) not in (401, 403)
        return 1 if live else 0
    env = compose_env()
    key = env.get("LLM_API_KEY")
    if not key:
        print("LLM_API_KEY: not inline in compose")
        return 0
    h = sha12(key)
    burned = h == BURNED_SHA12
    base = env.get("LLM_BASE_URL", "<unset>")
    print(f"LLM_API_KEY: key_sha12={h} burned={burned} len={len(key)}")
    print(f"LLM_BASE_URL: {base if 'sk-' not in base else '<masked>'}")
    status = gateway_status(key)
    print(f"gateway {GATEWAY}/key/info with this key: {status}")
    return 0 if not burned or status in (401, 403) else 1


if __name__ == "__main__":
    sys.exit(main())
