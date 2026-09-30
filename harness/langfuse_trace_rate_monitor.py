"""LiteLLM->Langfuse trace-rate floor alerting (wq#3122).

wq#1555 found the litellm gateway's Langfuse callback started throwing
"Unexpected error occurred" on the great majority of /chat/completions after a
2026-09-28T10:25:19Z container restart: ClickHouse traces/hour dropped from a
60-630/hr range to an 11-66/hr floor that never recovered, and stayed that way
for 30+ hours before anyone noticed. That floor is real traffic from
non-LiteLLM Langfuse producers, so it never reads as "zero traces" - only a
rate-floor check catches it, not a liveness/health check.

Root cause (confirmed by extracting the exact pinned wheels rather than
guessing): the live gateway runs litellm 1.83.10 (confirmed via
`curl 127.0.0.1:4000/health/readiness` -> litellm_version), whose bundled
langfuse/langfuse.py wraps every callback in a try/except that only calls
`verbose_logger.exception(...)` - but that log line is gated behind the
proxy's configured log_level, which health/readiness also reports as
WARNING. The literal string this ticket found in stdout
("Unexpected error occurred. Please check your request and contact support")
is not litellm's - it is langfuse-python's own catch-all in
langfuse/parse_error.py (`defaultErrorResponse`), raised from the background
IngestionConsumer.upload() thread (langfuse/_task_manager/ingestion_consumer.py)
whenever a batch POST to /api/public/ingestion fails for any reason that
is not one of its typed API errors - the real exception only reaches stdout
via `log.debug(exception)`, which WARNING-level suppresses. That is why
grepping litellm/langfuse .py sources for the actual fault text turned up
nothing: the real error never got logged at the level the proxy runs at.

This module cannot fix the live gateway (no docker access from a build
worker, and the ticket explicitly forbids restarting litellm from this lane
to test - a fix requires a coordinated maintenance window). What it can do,
without touching the container, is poll Langfuse's own public API for the
trace count in a rolling window and alert if it is at/under the known
non-LiteLLM floor - closing the 30-hour blind spot this ticket exists to
report, whether or not the next drop has the same root cause.

The traces-list endpoint's response shape (`meta.totalItems`, `fromTimestamp`
query param) is confirmed against the actual langfuse-python 2.57.13a0 wheel
this fleet's own manual reproduction used (langfuse/api/resources/trace/
client.py, langfuse/api/resources/utils/resources/pagination/types/
meta_response.py) - not guessed. Same "log-only degradation" contract as
langfuse_client.load_creds and langfuse_lag_monitor: missing creds or an
unreachable endpoint is a skip, not a crash - never load-bearing for the
caller (timer unit).
"""
import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from langfuse_client import load_creds

TRACES_ENDPOINT_PATH = os.getenv("LANGFUSE_TRACES_ENDPOINT_PATH", "/api/public/traces")
NTFY_SEND = os.getenv("LANGFUSE_TRACE_RATE_NTFY_SEND", "/home/lumen/bin/ntfy-send")

# wq#3211 (2026-09-30). This monitor used to read /home/lumen/.secrets/langfuse-foreman.env and
# count EVERY trace in the FOREMAN project (foreman-dispatch, fworker:*, canaries). LiteLLM's
# callback writes to a different project (lumen-pipeline, the key in agent/.env), so the monitor
# never measured LiteLLM at all: a 46 h loss of every LiteLLM trace (config sent literal
# placeholder credentials) went unseen, while aq 2492 fired 100+ times on foreman traffic alone.
# Defaults now point at the project LiteLLM writes to, use the SAME env file the litellm
# container loads (single source of truth, no third copy to drift after a rotation), and count
# only LiteLLM's own trace name. The completion probe (wq#3169) sends a real completion per
# host every 30 min, so a healthy callback yields >= ~13 litellm-acompletion traces per window
# and the floor is zero. agent/.env's LANGFUSE_HOST is the docker-network name, so the host is
# overridden with the published port.
DEFAULT_ENV_PATH = "/home/lumen/agent/.env"
DEFAULT_HOST = "http://127.0.0.1:3020"
DEFAULT_TRACE_NAME = "litellm-acompletion"
DEFAULT_WINDOW_S = 2700
DEFAULT_FLOOR = 0


def fetch_trace_count(host, pk, sk, window_seconds, timeout=10, name=None):
    """Returns the number of traces created in the last window_seconds, or None on
    any transport/parse failure."""
    from_ts = (datetime.now(timezone.utc) - timedelta(seconds=window_seconds)).isoformat()
    params = {"fromTimestamp": from_ts, "limit": 1}
    if name:
        params["name"] = name
    query = urllib.parse.urlencode(params)
    req = urllib.request.Request(
        host.rstrip("/") + TRACES_ENDPOINT_PATH + "?" + query,
        headers={"Authorization": "Basic " + _basic_auth(pk, sk)})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
    except Exception:
        return None
    meta = data.get("meta")
    if not isinstance(meta, dict):
        return None
    value = meta.get("totalItems")
    return value if isinstance(value, int) else None


def _basic_auth(pk, sk):
    import base64
    return base64.b64encode(f"{pk}:{sk}".encode()).decode()


def under_floor(trace_count, floor_count):
    """Pure predicate, split out from fetch/alert so it's testable without network.

    True when the observed count is at/under the known non-LiteLLM floor (wq#3122:
    11-66/hr kept flowing for 30h while LiteLLM's own traces silently stopped
    arriving), meaning LiteLLM's callback has likely gone quiet again.
    """
    return trace_count is not None and trace_count <= floor_count


def alert(trace_count, floor_count, window_seconds, ntfy_send=NTFY_SEND, name=None, pk=None):
    what = f"'{name}' traces" if name else "traces"
    proj = f" (project key {pk[:12]}...)" if pk else ""
    msg = (f"Langfuse saw only {trace_count} {what} in the last {window_seconds}s{proj}, "
           f"at/under the floor of {floor_count} - "
           f"LiteLLM's Langfuse callback may be failing silently again (wq#3122/wq#3211).")
    subprocess.run(
        [ntfy_send, "-t", "lumen-alerts", "-T", "LiteLLM->Langfuse trace drop", "-p", "high", msg],
        check=False)


def run(env_path, window_seconds, floor_count, name=None, host_override=None):
    """Returns exit code: 0 = checked (alerted or over floor), 1 = skipped
    (no creds or fetch failed) — matches the timer unit's oneshot contract."""
    host, pk, sk = load_creds(env_path)
    if not host:
        return 1
    if host_override:
        host = host_override
    if name:
        trace_count = fetch_trace_count(host, pk, sk, window_seconds, name=name)
    else:
        trace_count = fetch_trace_count(host, pk, sk, window_seconds)
    if trace_count is None:
        return 1
    if under_floor(trace_count, floor_count):
        if name:
            alert(trace_count, floor_count, window_seconds, name=name, pk=pk)
        else:
            alert(trace_count, floor_count, window_seconds)
    return 0


if __name__ == "__main__":
    env_path = os.getenv("LANGFUSE_ENV", DEFAULT_ENV_PATH)
    window = int(os.getenv("LANGFUSE_TRACE_RATE_WINDOW_S", str(DEFAULT_WINDOW_S)))
    floor = int(os.getenv("LANGFUSE_TRACE_RATE_FLOOR", str(DEFAULT_FLOOR)))
    trace_name = os.getenv("LANGFUSE_TRACE_RATE_NAME", DEFAULT_TRACE_NAME)
    host = os.getenv("LANGFUSE_TRACE_RATE_HOST", DEFAULT_HOST)
    sys.exit(run(env_path, window, floor, name=trace_name or None, host_override=host or None))
