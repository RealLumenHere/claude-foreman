#!/usr/bin/env python3
"""vlogs_mirror.py — journal mirror for fleet ntfy pages (wq#1416).

The self-hosted ntfy container's own cache.db only retains ~12h (NTFY_CACHE_DURATION,
that container's config — not this repo). Investigating the 09-02 night stall, page
delivery before 06:00 UTC could not be proven from ntfy alone: alerts.db (aq) recorded
that an ntfy-send was attempted, but not what the page actually said, and every caller
of this module (dispatch_health.py, self_heal.py, dispatch_recover.py,
dispatch_config_lint.py, foreman_dispatcher.py, notify_sinks.py) discards ntfy-send's
own subprocess output on success — so the alert content was never logged anywhere
durable.

Every fleet host already runs vlogs-forwarder.service, which tails the systemd journal
and ships every line to victorialogs at bes:9428 (see
ops/wq1302-n8n-eval/inventory.json). These scripts already run under systemd units, so
a plain print here lands in that same journal for free and rides the existing forwarder
with no new transport, no new service, and no dependency on ntfy's own retention — a
page from days ago stays retrievable by querying victorialogs for event=ntfy_publish
long after cache.db has rolled it off.

Printed to stderr, not stdout: several callers (circuit_breaker.py's `status` and
`test-ntfy` CLI commands) print their own single-JSON-object result to stdout, and a
caller may pipe or json.loads() that output, so mirroring must not interleave with it.
stderr lands in the same journal stream as stdout under systemd.
"""
import json
import sys
from datetime import datetime, timezone


def mirror(source, title, priority, msg):
    """Best-effort, synchronous, never raises — printing cannot meaningfully fail.
    `source` is the calling module so a query can filter to one alert class (e.g.
    source=dispatch_health for DISPATCHER STALLED pages)."""
    try:
        print(json.dumps({
            "event": "ntfy_publish",
            "ts": datetime.now(timezone.utc).isoformat(),
            "source": source,
            "title": title,
            "priority": priority,
            "msg": msg,
        }), file=sys.stderr, flush=True)
    except (TypeError, ValueError):
        pass
