"""Shared Langfuse ingestion helpers (wq#754). Used by both the dispatcher (one trace
per pass, one span per ticket decision) and fworker (one trace per ticket run, one span
per attempt). Best-effort everywhere by contract — see DESIGN.md §8 / RELIABILITY_SPEC.md:
callers must catch, log, and move on. Never load-bearing.
"""
import base64
import json
import os
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone

import masking


class LangfuseIngestError(Exception):
    """Raised by ingest() with the langfuse-web response body attached (masked), so
    a best-effort caller that logs repr(e) captures the real failure instead of a
    bare HTTPError with no body (wq#3211: langfuse-web logged 4002 "Invalid
    credentials" for 26h, but dispatcher/fworker only ever log repr(e) on ingest
    failure, and HTTPError.__repr__ never includes the response body — the auth
    detail langfuse-web actually returned was never captured anywhere reachable)."""


def load_creds(env_path):
    """Returns (host, public_key, secret_key), each None if the file is absent or a
    key is missing — same "log-only degradation" contract foreman_dispatcher.py uses."""
    if not os.path.exists(env_path):
        return None, None, None
    creds = {}
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export "):]
            k, _, v = line.partition("=")
            creds[k.strip()] = v.strip().strip('"').strip("'")
    host = creds.get("LANGFUSE_HOST")
    pk, sk = creds.get("LANGFUSE_PUBLIC_KEY"), creds.get("LANGFUSE_SECRET_KEY")
    if not (host and pk and sk):
        return None, None, None
    return host, pk, sk


def ingest(host, pk, sk, batch, timeout=5):
    req = urllib.request.Request(
        host.rstrip("/") + "/api/public/ingestion",
        data=json.dumps({"batch": batch}).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": "Basic " + base64.b64encode(
                     f"{pk}:{sk}".encode()).decode()},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout):
            pass
    except urllib.error.HTTPError as e:
        body, _labels = masking.mask_text(e.read(2048).decode("utf-8", "replace"))
        raise LangfuseIngestError(
            f"HTTP {e.code} {e.reason} host={host} pk={pk[:10]}... body={body}"
        ) from e
    return len(batch)


def emit_trace_with_span(env_path, trace_id, trace_name, span_name, metadata,
                          tags=None, trace_metadata=None):
    """One trace-create + one span-create in a single ingestion batch. Returns the
    number of events shipped, or None when disabled (no creds at env_path — caller
    logs a degradation event, same pattern as foreman_dispatcher.emit_langfuse)."""
    host, pk, sk = load_creds(env_path)
    if not host:
        return None
    now = datetime.now(timezone.utc).isoformat()
    batch = [
        {"id": str(uuid.uuid4()), "type": "trace-create", "timestamp": now,
         "body": {"id": trace_id, "name": trace_name, "timestamp": now,
                  "metadata": trace_metadata or {}, "tags": tags or []}},
        {"id": str(uuid.uuid4()), "type": "span-create", "timestamp": now,
         "body": {"id": str(uuid.uuid4()), "traceId": trace_id, "name": span_name,
                  "startTime": now, "endTime": now, "metadata": metadata}},
    ]
    return ingest(host, pk, sk, batch)
