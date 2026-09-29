"""Langfuse pipeline-lag alerting via canary read-back (wq#1552, repointed wq#3124).

The original wq#1552 design polled /api/public/metrics/export-lag — an endpoint that
does not exist in Langfuse v3 or v4 (guessed from a version string, never probed), so
the monitor could never alert. wq#3124 repoints it at a signal probed live against
this deployment (v4.32.0, 2026-09-29): each pass ingests a canary trace via
POST /api/public/ingestion and verifies the PREVIOUS pass's canary is readable via
GET /api/public/traces/{id} (observed 404 -> 200 within ~10s of ingest). "Lag" is
canary staleness: seconds since a canary was last confirmed end-to-end through
ingestion -> queue -> worker -> ClickHouse -> read API. By construction the monitor
cannot be green while ingestion is dead — if the POST fails, staleness keeps growing
and trips the alert (the 2026-09-28 LiteLLM trace drop, wq#3122, would have paged).

The worker's own :3030/api/health probes were considered and rejected: the port is
unpublished (container-IP only, needs docker exec + IP discovery), and
queueConsumption.secondsSinceLastActivity conflates an idle fleet with a stuck queue
while failIfEventPropagationStuck reports enabled:false in legacy mode. See
ops/wq1554-langfuse-lag-monitor/README.md for the full decision record.

Alerts shell out to the fleet's `ntfy-send` (that binary owns the alert-gate/burst/OOB
plumbing, see /home/lumen/bin/ntfy-send). Same "log-only degradation" contract as
langfuse_client.load_creds: missing creds or a fully unreachable endpoint is a skip
(exit 1), not a crash — never load-bearing for the caller (timer unit).
"""
import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone

from langfuse_client import load_creds, ingest

NTFY_SEND = os.getenv("LANGFUSE_LAG_NTFY_SEND", "/home/lumen/bin/ntfy-send")
STATE_PATH = os.getenv(
    "LANGFUSE_LAG_STATE", "/home/lumen/.local/state/langfuse_lag_monitor.json")
CANARY_NAME = "langfuse-lag-monitor-canary"


def load_state(path=None):
    """Returns the persisted canary state, or {} when absent/corrupt (a lost state
    file just restarts the measurement — never a crash)."""
    try:
        with open(path or STATE_PATH) as f:
            state = json.load(f)
        return state if isinstance(state, dict) else {}
    except Exception:
        return {}


def save_state(state, path=None):
    path = path or STATE_PATH
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(state, f)
    except Exception:
        pass


def fetch_trace_status(host, pk, sk, trace_id, timeout=10):
    """HTTP status of GET /api/public/traces/{id}: 200 = canary visible end-to-end,
    404 = not (yet) propagated, None = transport failure (indeterminate)."""
    req = urllib.request.Request(
        f"{host.rstrip('/')}/api/public/traces/{trace_id}",
        headers={"Authorization": "Basic " + base64.b64encode(
            f"{pk}:{sk}".encode()).decode()})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return None


def ingest_canary(host, pk, sk):
    """Ships one canary trace; returns its id, or None on failure."""
    trace_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    batch = [{"id": str(uuid.uuid4()), "type": "trace-create", "timestamp": now,
              "body": {"id": trace_id, "name": CANARY_NAME, "timestamp": now,
                       "metadata": {"source": "langfuse_lag_monitor"},
                       "tags": ["lag-canary"]}}]
    try:
        ingest(host, pk, sk, batch)
        return trace_id
    except Exception:
        return None


def over_threshold(lag_seconds, threshold_seconds):
    """Pure predicate, split out from fetch/alert so it's testable without network."""
    return lag_seconds is not None and lag_seconds > threshold_seconds


def alert(lag_seconds, threshold_seconds, ntfy_send=NTFY_SEND):
    msg = (f"Langfuse pipeline stale: no canary trace confirmed end-to-end for "
           f"{int(lag_seconds)}s (threshold {threshold_seconds}s). Ingestion or the "
           f"worker/ClickHouse path is likely down (cf. wq#3122).")
    subprocess.run(
        [ntfy_send, "-t", "lumen-alerts", "-T", "Langfuse pipeline stale", "-p", "high", msg],
        check=False)


def run(env_path, threshold_seconds, state_path=None, now=None):
    """One monitor pass. Returns exit code: 0 = checked (canary confirmed, ingested,
    or staleness alerted), 1 = skipped (no creds, or neither read-back nor ingestion
    reached the server) — matches the timer unit's oneshot contract."""
    host, pk, sk = load_creds(env_path)
    if not host:
        return 1
    now = now if now is not None else time.time()
    state = load_state(state_path)
    checked = False

    pending_id = state.get("pending_id")
    if pending_id:
        status = fetch_trace_status(host, pk, sk, pending_id)
        if status == 200:
            state["last_ok_at"] = now
            state.pop("pending_id", None)
            state.pop("pending_ingested_at", None)
            checked = True
        elif status is not None:
            checked = True  # server answered; canary just hasn't propagated

    # Staleness: seconds since a canary last flowed end-to-end. Before any
    # confirmation ever, measure from the oldest unverified ingest instead so a
    # pipeline that was born broken still pages.
    basis = state.get("last_ok_at") or state.get("pending_ingested_at")
    lag_seconds = (now - basis) if basis else None
    if over_threshold(lag_seconds, threshold_seconds):
        alert(lag_seconds, threshold_seconds)

    # Only plant a new canary when none is outstanding: an unverified pending is
    # the oldest evidence of breakage and must not be papered over.
    if not state.get("pending_id"):
        trace_id = ingest_canary(host, pk, sk)
        if trace_id:
            state["pending_id"] = trace_id
            state["pending_ingested_at"] = now
            checked = True

    save_state(state, state_path)
    return 0 if checked else 1


if __name__ == "__main__":
    env_path = os.getenv("LANGFUSE_ENV", "/home/lumen/.secrets/langfuse-foreman.env")
    threshold = int(os.getenv("LANGFUSE_LAG_THRESHOLD_S", "2700"))
    sys.exit(run(env_path, threshold))
