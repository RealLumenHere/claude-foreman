#!/usr/bin/env python3
"""wq#1712 — is the gateway's recorded spend cache-aware?

The DeepSeek canary (deepseek_cache_canary.ts) proved the provider serves 94-98%
of our prompt tokens from its context cache. That only turns into a lower bill if
DeepSeek charges less for a cache hit AND our accounting knows it. This probe
answers the accounting half, two ways:

  1. `/model/info` — does the gateway carry `cache_read_input_token_cost` for the
     rung? If it does not, LiteLLM prices every prompt token at the full input
     rate, so `LiteLLM_SpendLogs.spend` is an upper bound, not the invoice.
  2. The canary's own four rows — call1 was a 0% cache hit and calls 2/3 were
     97.9% hits at an identical 6,933 prompt tokens. If all three carry the same
     `spend`, the accounting is cache-blind and the discrepancy is measured, not
     inferred.

Read-only: two GETs against the local proxy. Credential rides in the
Authorization header, read from a 0600 file, never printed (wq#948 pattern,
copied from ops/wq1406-paid-fallback-guard/paid_spend_tripwire.py).

Run via lumen-run (the master-key file is guard-denied to a worker sandbox):
  /home/lumen/bin/lumen-run start --name wq1712-spendprobe -- \
    python3 /home/lumen/worktrees/wq1712-a1/ops/wq1712-prompt-cache-survey/spend_accounting_probe.py
"""
import datetime
import json
import os
import pathlib
import urllib.request

BASE = os.environ.get("LITELLM_BASE_URL", "http://localhost:4000")
KEY_FILE = os.environ.get("LITELLM_MASTER_KEY_FILE", "/home/lumen/.secrets/litellm-master.env")
MODELS = ("deepseek-v4-flash", "deepseek-v4-pro", "glm-5.3-flash-zai", "cerebras-oss-120b")
OUT = pathlib.Path(__file__).with_name("spend_accounting_probe.json")

COST_KEYS = (
    "input_cost_per_token",
    "output_cost_per_token",
    "cache_read_input_token_cost",
    "cache_creation_input_token_cost",
)


def read_key() -> tuple[str, str]:
    """Parse the 0600 env file line-wise. `text.split("=", 1)[1]` (the shape
    paid_spend_tripwire.py uses) swallows every following line when the file has
    more than one entry, which is how this probe first got a 401."""
    for line in pathlib.Path(KEY_FILE).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        var, _, value = line.partition("=")
        var = var.removeprefix("export ").strip()
        if "KEY" in var.upper():
            return value.strip().strip("'\""), var
    raise SystemExit(f"no key-shaped var in {KEY_FILE}")


def get(path: str, key: str):
    req = urllib.request.Request(f"{BASE}{path}", headers={"Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def model_costs(key: str) -> dict:
    """Per-rung cost registration as the gateway has it, cost fields only."""
    data = get("/model/info", key)
    rows = data.get("data") if isinstance(data, dict) else data
    out = {}
    for row in rows or []:
        name = row.get("model_name")
        if name not in MODELS:
            continue
        info = row.get("model_info") or {}
        out[name] = {k: info.get(k) for k in COST_KEYS}
    return out


def todays_deepseek_rows(key: str) -> list[dict]:
    """Today's deepseek-v4-flash rows, newest first, cost-relevant fields only."""
    today = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    rows, page = [], 1
    while True:
        data = get(f"/spend/logs?start_date={today}&end_date={today}&page={page}&page_size=200", key)
        batch = data if isinstance(data, list) else data.get("data") or []
        if not batch:
            break
        for row in batch:
            if (row.get("model") or "") != "deepseek-v4-flash":
                continue
            rows.append({
                "startTime": row.get("startTime"),
                "spend": row.get("spend"),
                "prompt_tokens": row.get("prompt_tokens"),
                "completion_tokens": row.get("completion_tokens"),
                "cache_hit": row.get("cache_hit"),
                "cache_key": bool(row.get("cache_key")),
            })
        if len(batch) < 200:
            break
        page += 1
    rows.sort(key=lambda r: r["startTime"] or "", reverse=True)
    return rows


def main() -> int:
    key, var = read_key()

    result = {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "base_url": BASE,
        # Diagnostics only — name and length, never the value (instinct 4).
        "key_var": var,
        "key_len": len(key),
    }
    try:
        result["model_costs"] = model_costs(key)
    except Exception as exc:
        result["model_costs_error"] = f"{type(exc).__name__}: {exc}"
    try:
        rows = todays_deepseek_rows(key)
        # The canary fired four 6,900-ish-token prompts; surface those directly.
        result["canary_shaped_rows"] = [r for r in rows if (r["prompt_tokens"] or 0) > 6000][:12]
        result["deepseek_rows_today"] = len(rows)
        result["deepseek_spend_today"] = round(sum(float(r["spend"] or 0) for r in rows), 6)
        result["deepseek_prompt_tokens_today"] = sum(int(r["prompt_tokens"] or 0) for r in rows)
    except Exception as exc:
        result["spend_logs_error"] = f"{type(exc).__name__}: {exc}"

    OUT.write_text(json.dumps(result, indent=2))
    print(json.dumps({k: v for k, v in result.items() if k != "canary_shaped_rows"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
