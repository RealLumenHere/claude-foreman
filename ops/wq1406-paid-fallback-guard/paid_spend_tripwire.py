#!/usr/bin/env python3
"""wq#1406 — per-paid-model daily spend tripwire.

Pages (ntfy) when any paid model's spend for the current UTC day exceeds a
threshold. This is the "if the alert in paid_fallback_alert.py gets missed
or silenced, the bill still gets caught" backstop — a page, not a cap: per
Frank (ticket text), this must not auto-throttle a genuinely-needed paid
call, it must make the drain impossible to miss.

Data source: LiteLLM's own SpendLogs, via the proxy's `/spend/logs` admin
endpoint (same auth pattern as litellm-budget-alert: master key from
/home/lumen/.secrets/litellm-master.env, Bearer header, never in a URL —
instinct 3). Queried directly rather than through Langfuse/clickhouse
(litellm-cost's source) because SpendLogs is LiteLLM's own cost ledger and
this tripwire's job is "did we overspend on a specific model", which is
exactly what that table is for.

Usage: paid_spend_tripwire.py [--print]
Env:
  PAID_SPEND_THRESHOLD_USD        default per-model daily threshold (default 5.00)
  PAID_SPEND_THRESHOLD_<MODEL>    per-model override, MODEL uppercased with
                                   non-alnum -> '_' (e.g.
                                   PAID_SPEND_THRESHOLD_DEEPSEEK_V4_FLASH=10)
  PAID_SPEND_STATE_DIR            default /home/lumen/agents/paid-spend-tripwire
  LITELLM_BASE_URL                default http://localhost:4000
  LITELLM_MASTER_KEY_FILE         default /home/lumen/.secrets/litellm-master.env
"""
import datetime
import json
import os
import pathlib
import re
import subprocess
import sys
import urllib.request

PAID_PROVIDER_PREFIXES = (
    "deepseek/", "openrouter/", "mistral/", "anthropic/", "openai/", "cerebras/",
)


def is_paid_model(litellm_model: str) -> bool:
    m = (litellm_model or "").strip().lower()
    return any(m.startswith(p) for p in PAID_PROVIDER_PREFIXES)


def threshold_env_key(model: str) -> str:
    return "PAID_SPEND_THRESHOLD_" + re.sub(r"[^A-Za-z0-9]+", "_", model.upper()).strip("_")


def threshold_for(model: str, env: dict, default: float) -> float:
    key = threshold_env_key(model)
    if key in env:
        try:
            return float(env[key])
        except ValueError:
            pass
    return default


def check_thresholds(spend_by_model: dict[str, float], env: dict) -> list[dict]:
    """Pure decision function: {model: today's $ spend} -> list of breaches.
    Only paid models are ever checked (a free/local model can't page here
    even if a bad row makes it look like it has spend > 0)."""
    default = float(env.get("PAID_SPEND_THRESHOLD_USD", "5.00"))
    breaches = []
    for model, spend in spend_by_model.items():
        if not is_paid_model(model):
            continue
        threshold = threshold_for(model, env, default)
        if spend >= threshold:
            breaches.append({"model": model, "spend_usd": spend, "threshold_usd": threshold})
    return breaches


def fetch_today_spend_by_model(base_url: str, master_key: str) -> dict[str, float]:
    """Sums LiteLLM_SpendLogs rows for the current UTC day by model, via the
    proxy's /spend/logs endpoint (paginated, admin-scoped: credential rides
    in the Authorization header, never a URL query string — wq#948).
    """
    today = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    totals: dict[str, float] = {}
    page = 1
    while True:
        req = urllib.request.Request(
            f"{base_url}/spend/logs?start_date={today}&end_date={today}&page={page}&page_size=200",
            headers={"Authorization": f"Bearer {master_key}"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        rows = data if isinstance(data, list) else data.get("data") or []
        if not rows:
            break
        for row in rows:
            model = row.get("model") or row.get("model_group") or "unknown"
            totals[model] = totals.get(model, 0.0) + float(row.get("spend") or 0.0)
        if len(rows) < 200:
            break
        page += 1
    return totals


def ntfy(msg: str, priority: str = "urgent") -> None:
    ntfy_bin = os.environ.get("PAID_SPEND_NTFY", "/home/lumen/bin/ntfy-send")
    topic = os.environ.get("PAID_SPEND_TOPIC", "lumen-alerts")
    try:
        subprocess.run(
            [ntfy_bin, "-t", topic, "-p", priority, "-T", "LiteLLM paid-spend tripwire", "--quiet", msg],
            timeout=15, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        print(f"ntfy error: {e}", file=sys.stderr)


def _state_path() -> pathlib.Path:
    state_dir = pathlib.Path(
        os.environ.get("PAID_SPEND_STATE_DIR", "/home/lumen/agents/paid-spend-tripwire")
    )
    state_dir.mkdir(parents=True, exist_ok=True)
    return state_dir / "state.json"


def main(argv: list[str]) -> int:
    base_url = os.environ.get("LITELLM_BASE_URL", "http://localhost:4000")
    key_file = pathlib.Path(
        os.environ.get("LITELLM_MASTER_KEY_FILE", "/home/lumen/.secrets/litellm-master.env")
    )
    try:
        master_key = key_file.read_text().split("=", 1)[1].strip()
    except Exception as e:
        msg = f"paid_spend_tripwire: cannot read master key ({key_file}): {e}"
        print(msg, file=sys.stderr)
        ntfy(msg)
        return 1

    try:
        spend_by_model = fetch_today_spend_by_model(base_url, master_key)
    except Exception as e:
        msg = f"paid_spend_tripwire: /spend/logs fetch failed, spend NOT confirmed within threshold: {e}"
        print(msg, file=sys.stderr)
        ntfy(msg)
        return 1

    breaches = check_thresholds(spend_by_model, os.environ)

    today = datetime.date.today().isoformat()
    state_path = _state_path()
    try:
        prev = json.loads(state_path.read_text())
    except Exception:
        prev = {}
    already_alerted = set(prev.get("date") == today and prev.get("alerted") or [])

    new_breaches = [b for b in breaches if b["model"] not in already_alerted]
    if new_breaches:
        lines = [f"{b['model']}: ${b['spend_usd']:.2f} >= ${b['threshold_usd']:.2f}" for b in new_breaches]
        ntfy("Paid model(s) over daily spend threshold today:\n" + "\n".join(lines))

    alerted_today = (set(already_alerted) | {b["model"] for b in breaches}) if breaches else set()
    json.dump({"date": today, "alerted": sorted(alerted_today)}, state_path.open("w"))

    if "--print" in argv or sys.stdout.isatty():
        for model, spend in sorted(spend_by_model.items(), key=lambda kv: -kv[1]):
            flag = "PAID" if is_paid_model(model) else "free"
            print(f"  {model:30s} ${spend:8.4f}  [{flag}]")
        print(f"\n{len(breaches)} breach(es), {len(new_breaches)} new.")

    return 1 if new_breaches else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
