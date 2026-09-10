#!/usr/bin/env python3
"""wq#1647 — retire the orphaned `lpp-cerebras` LiteLLM key: set max_budget=0.

Decision (recorded in README.md): the key's only consumer, lumen-pokemon-live,
runs on lpp-glm.env (verified live via `systemctl show lumen-pokemon-live -p
EnvironmentFiles`), and lpp-glm's model allowlist covers every brain leg
including the cerebras fallback rung. The $10 burn was wq#1578's misconfig
(glm roles on the cerebras-scoped key). So:
  - NOT raise: any future spend on this key is a bug; refusal-at-cap is the
    alarm we want, and it must survive the 30d window roll.
  - NOT rotate: rotation is for compromise, and a fresh spend window would
    silently re-enable stale consumers.
  - RETIRE: max_budget=0 keeps the gateway refusing forever (spend >= 0 after
    any reset) and litellm-budget-alert skips budget<=0 keys, ending the 6h
    alert noise while the record and local key file stay matched (no
    "rotated or revoked?" failure path).

Credential hygiene (wq#948): the raw key travels only in Authorization headers
and POST bodies, never in a URL; nothing secret is ever printed.
"""
import hashlib
import json
import pathlib
import sys
import urllib.error
import urllib.request

LITELLM = "http://localhost:4000"
ALIAS = "lpp-cerebras"
KEY_FILE = pathlib.Path("/home/lumen/.secrets/litellm-service-keys/lpp-cerebras.key")
MASTER_ENV = pathlib.Path("/home/lumen/.secrets/litellm-master.env")


def master_key() -> str:
    return MASTER_ENV.read_text().split("=", 1)[1].strip()


def gateway_record(mk: str, token_sha: str) -> dict | None:
    """Find the key's record by sha256 token via admin /key/list (wq#948)."""
    page, pages = 1, 1
    while page <= pages:
        req = urllib.request.Request(
            f"{LITELLM}/key/list?return_full_object=true&include_team_keys=true&size=100&page={page}",
            headers={"Authorization": f"Bearer {mk}"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        for rec in data.get("keys") or []:
            if isinstance(rec, dict) and rec.get("token") == token_sha:
                return rec
        pages = int(data.get("total_pages") or 1)
        page += 1
    return None


def probe_refused(raw_key: str) -> tuple[bool, str]:
    """One cheap completion on the retired key; expect a budget refusal."""
    body = json.dumps({
        "model": "cerebras-oss-120b",
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1,
    }).encode()
    req = urllib.request.Request(
        f"{LITELLM}/chat/completions",
        data=body,
        headers={"Authorization": f"Bearer {raw_key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return False, f"UNEXPECTED SUCCESS http {resp.status}"
    except urllib.error.HTTPError as e:
        detail = e.read()[:300].decode(errors="replace")
        refused = e.code == 400 and "budget" in detail.lower()
        return refused, f"http {e.code} {'budget refusal' if refused else detail}"


def main() -> int:
    mk = master_key()
    raw = KEY_FILE.read_text().strip()
    token_sha = hashlib.sha256(raw.encode()).hexdigest()

    rec = gateway_record(mk, token_sha)
    if rec is None:
        print(f"{ALIAS}: no gateway key matches the local file — nothing to retire", file=sys.stderr)
        return 1
    if rec.get("key_alias") != ALIAS:
        print(f"refusing: matched record alias {rec.get('key_alias')!r} != {ALIAS!r}", file=sys.stderr)
        return 1
    print(f"before: alias={rec['key_alias']} spend={rec.get('spend')} max_budget={rec.get('max_budget')}")

    upd = urllib.request.Request(
        f"{LITELLM}/key/update",
        data=json.dumps({"key": raw, "max_budget": 0.0}).encode(),
        headers={"Authorization": f"Bearer {mk}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(upd, timeout=15) as resp:
        if resp.status != 200:
            print(f"/key/update http {resp.status}", file=sys.stderr)
            return 1

    after = gateway_record(mk, token_sha)
    # 0.0 is falsy — do not `or`-default here, it would turn a correct 0.0 into a failure
    if after is None or after.get("max_budget") is None or float(after["max_budget"]) != 0.0:
        print(f"verify failed: max_budget={after and after.get('max_budget')}", file=sys.stderr)
        return 1
    print(f"after:  alias={after['key_alias']} spend={after.get('spend')} max_budget={after.get('max_budget')}")

    refused, note = probe_refused(raw)
    print(f"probe:  {note}")
    if not refused:
        print("retired key was NOT refused — investigate before closing", file=sys.stderr)
        return 1
    print(f"{ALIAS} retired: max_budget=0, gateway refuses, budget-alert now skips it")
    return 0


if __name__ == "__main__":
    sys.exit(main())
