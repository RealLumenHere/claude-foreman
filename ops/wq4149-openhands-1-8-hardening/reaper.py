#!/usr/bin/env python3
"""wq#4149 item 3: identify idle/failed OpenHands sandbox containers safe to
reap. Pure selection logic only — no docker calls here, so it is unit-testable
without a live daemon (same posture as wq#4111's probe.py classifier).

A privileged lane wires this to a live source with
`docker inspect $(docker ps -aq --filter name=<SANDBOX_NAME_PREFIX>) `
(JSON array, same shape `inspect_container()` in
ops/wq4111-openhands-litellm-production/configure_and_run_task.py already
parses) and then `docker rm -f` on the returned names.

SANDBOX_NAME_PREFIX is a BEST-GUESS based on OpenHands' own naming convention
for agent-server sandbox containers (`openhands-runtime-*` in older releases);
it is NOT confirmed against the installed 1.8/agent-server 1.26 image from
this lane (no docker access). A privileged lane MUST confirm the real prefix
with `docker ps -a --format '{{.Names}}'` before trusting reap_candidates'
output against this host's containers — do not run `docker rm -f` on an
unconfirmed name filter.
"""
from datetime import datetime, timezone

SANDBOX_NAME_PREFIX = "openhands-runtime-"  # UNCONFIRMED — see module docstring


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
