#!/usr/bin/env python3
"""wq#4149 item 3: identify idle/failed OpenHands sandbox containers safe to
reap. Pure selection logic only — no docker/network calls here, so it is
unit-testable without a live daemon (same posture as wq#4111's probe.py
classifier).

A privileged lane wires this to a live source with
`docker inspect $(docker ps -aq --filter name=<SANDBOX_NAME_PREFIX>) `
(JSON array, same shape `inspect_container()` in
ops/wq4111-openhands-litellm-production/configure_and_run_task.py already
parses), then kills each name `reap_candidates()` returns via `reap_request()`
below: `DELETE /api/v1/sandboxes?sandbox_id=<id>` first (confirmed live
wq#4199: 200, container removed in <4s; the bare path form
`/api/v1/sandboxes/<id>` returns 422 — the id must be a query param), falling
back to `docker rm -f <name>` only if that call errors or is unreachable.

SANDBOX_NAME_PREFIX was confirmed live 2026-10-18 (wq#4199) against the
installed 1.8/agent-server 1.26 image: real sandbox container names are
`oh-agent-server-<id>`, not the `openhands-runtime-` guess this module
shipped with under wq#4149 (which matched nothing — fail-safe but
non-functional).
"""
from datetime import datetime, timezone

SANDBOX_NAME_PREFIX = "oh-agent-server-"  # confirmed live 2026-10-18, wq#4199


def _parse_ts(value):
    if not value or value.startswith("0001-01-01"):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def reap_candidates(containers, now=None, *, exited_grace_s=300,
                     running_idle_s=3600, name_prefix=SANDBOX_NAME_PREFIX):
    """containers: list of dicts shaped like `docker inspect` output elements
    (must have "Name" and "State": {"Status", "FinishedAt", "StartedAt"}).

    Returns the names of containers safe to `docker rm -f`:
      - exited/dead sandboxes whose FinishedAt is >= exited_grace_s in the past
        (a failed or completed task that was never cleaned up), or
      - sandboxes still "running" whose StartedAt is >= running_idle_s in the
        past (the 0.25-era harness defect this item is named for: a timed-out
        task's container left running with nothing driving it).

    Never matches a container whose name doesn't start with name_prefix —
    this must never reap the `openhands` app container itself.
    """
    now = now or datetime.now(timezone.utc)
    reap = []
    for c in containers:
        name = c.get("Name", "").lstrip("/")
        if not name.startswith(name_prefix):
            continue
        state = c.get("State", {})
        status = state.get("Status")
        if status in ("exited", "dead"):
            finished_at = _parse_ts(state.get("FinishedAt"))
            if finished_at and (now - finished_at).total_seconds() >= exited_grace_s:
                reap.append(name)
        elif status == "running":
            started_at = _parse_ts(state.get("StartedAt"))
            if started_at and (now - started_at).total_seconds() >= running_idle_s:
                reap.append(name)
    return reap


def sandbox_id_from_name(name, *, name_prefix=SANDBOX_NAME_PREFIX):
    """Strip name_prefix from a container name to get the sandbox_id the
    DELETE API expects. Returns None if name doesn't match the prefix."""
    name = name.lstrip("/")
    if not name.startswith(name_prefix):
        return None
    return name[len(name_prefix):]


def reap_request(name, *, name_prefix=SANDBOX_NAME_PREFIX,
                  base_url="http://127.0.0.1:3000"):
    """Build the (method, url) a privileged lane should call to reap `name`:
    DELETE /api/v1/sandboxes?sandbox_id=<id> — the supported path, confirmed
    live wq#4199 (200, container removed in <4s). base_url defaults to the
    host-reachable published port (UI/API stay 127.0.0.1+172.17.0.1 only per
    wq#4152). Returns None if name doesn't match name_prefix. Only fall back
    to `docker rm -f <name>` if this request errors or is unreachable.
    """
    sandbox_id = sandbox_id_from_name(name, name_prefix=name_prefix)
    if sandbox_id is None:
        return None
    return ("DELETE", f"{base_url}/api/v1/sandboxes?sandbox_id={sandbox_id}")
