"""Langfuse export-lag alerting (wq#1552). Polls Langfuse's export-lag metrics API
and shells out to the fleet's `ntfy-send` when lag exceeds threshold, instead of
reimplementing an ntfy client (that binary already owns the alert-gate/burst/OOB
plumbing, see /home/lumen/bin/ntfy-send).

The exact metrics endpoint/response shape shipped in the v4.32.0 release could not be
confirmed from this worker (no outbound web access in this session) — LAG_ENDPOINT_PATH
and the response field name are both overridable via env so this can be pointed at the
real path without a code change once confirmed. Same "log-only degradation" contract as
langfuse_client.load_creds: missing creds or an unreachable endpoint is a skip, not a
crash — never load-bearing for the caller (timer unit).
"""
import json
import os
import subprocess
import sys
import urllib.request

from langfuse_client import load_creds

LAG_ENDPOINT_PATH = os.getenv("LANGFUSE_LAG_ENDPOINT_PATH", "/api/public/metrics/export-lag")
LAG_FIELD = os.getenv("LANGFUSE_LAG_FIELD", "lagSeconds")
NTFY_SEND = os.getenv("LANGFUSE_LAG_NTFY_SEND", "/home/lumen/bin/ntfy-send")


def fetch_lag_seconds(host, pk, sk, timeout=10):
    """Returns the export lag in seconds, or None on any transport/parse failure."""
    req = urllib.request.Request(
        host.rstrip("/") + LAG_ENDPOINT_PATH,
        headers={"Authorization": "Basic " + _basic_auth(pk, sk)})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
    except Exception:
        return None
    value = data.get(LAG_FIELD)
    return value if isinstance(value, (int, float)) else None


def _basic_auth(pk, sk):
    import base64
    return base64.b64encode(f"{pk}:{sk}".encode()).decode()


def over_threshold(lag_seconds, threshold_seconds):
    """Pure predicate, split out from fetch/alert so it's testable without network."""
    return lag_seconds is not None and lag_seconds > threshold_seconds


def alert(lag_seconds, threshold_seconds, ntfy_send=NTFY_SEND):
    msg = f"Langfuse export lag is {lag_seconds}s, exceeding threshold of {threshold_seconds}s."
    subprocess.run(
        [ntfy_send, "-t", "lumen-alerts", "-T", "Langfuse export lag", "-p", "high", msg],
        check=False)


def run(env_path, threshold_seconds):
    """Returns exit code: 0 = checked (alerted or under threshold), 1 = skipped
    (no creds or fetch failed) — matches the timer unit's oneshot contract."""
    host, pk, sk = load_creds(env_path)
    if not host:
        return 1
    lag_seconds = fetch_lag_seconds(host, pk, sk)
    if lag_seconds is None:
        return 1
    if over_threshold(lag_seconds, threshold_seconds):
        alert(lag_seconds, threshold_seconds)
    return 0


if __name__ == "__main__":
    env_path = os.getenv("LANGFUSE_ENV", "/home/lumen/.secrets/langfuse-foreman.env")
    threshold = int(os.getenv("LANGFUSE_LAG_THRESHOLD_S", "300"))
    sys.exit(run(env_path, threshold))
