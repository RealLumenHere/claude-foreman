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


def fetch_trace_count(host, pk, sk, window_seconds, timeout=10):
    """Returns the number of traces created in the last window_seconds, or None on
    any transport/parse failure."""
    from_ts = (datetime.now(timezone.utc) - timedelta(seconds=window_seconds)).isoformat()
    query = urllib.parse.urlencode({"fromTimestamp": from_ts, "limit": 1})
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


def alert(trace_count, floor_count, window_seconds, ntfy_send=NTFY_SEND):
    msg = (f"Langfuse saw only {trace_count} traces in the last {window_seconds}s, "
           f"at/under the {floor_count} non-LiteLLM floor from wq#3122 - "
           f"LiteLLM's Langfuse callback may be failing silently again.")
    subprocess.run(
        [ntfy_send, "-t", "lumen-alerts", "-T", "LiteLLM->Langfuse trace drop", "-p", "high", msg],
        check=False)


def run(env_path, window_seconds, floor_count):
    """Returns exit code: 0 = checked (alerted or over floor), 1 = skipped
    (no creds or fetch failed) — matches the timer unit's oneshot contract."""
    host, pk, sk = load_creds(env_path)
    if not host:
        return 1
    trace_count = fetch_trace_count(host, pk, sk, window_seconds)
    if trace_count is None:
        return 1
    if under_floor(trace_count, floor_count):
        alert(trace_count, floor_count, window_seconds)
    return 0


if __name__ == "__main__":
    env_path = os.getenv("LANGFUSE_ENV", "/home/lumen/.secrets/langfuse-foreman.env")
    window = int(os.getenv("LANGFUSE_TRACE_RATE_WINDOW_S", "3600"))
    floor = int(os.getenv("LANGFUSE_TRACE_RATE_FLOOR", "66"))
    sys.exit(run(env_path, window, floor))
