#!/usr/bin/env python3
"""Prometheus textfile exporter for agent incident metrics (wq#4929).

Writes to the node_exporter textfile collector directory. Run periodically
(e.g. every 60s via cron or a systemd timer) to update gauges.

Output: /var/lib/prometheus/node-exporter/foreman_agents.prom
"""

import glob
import json
import os
import time

INCIDENTS_DIR = os.environ.get(
    "AGENT_KILL_INCIDENTS_DIR",
    "/home/lumen/foreman/security/incidents",
)
PROM_DIR = os.environ.get(
    "PROM_TEXTFILE_DIR",
    "/var/lib/prometheus/node-exporter",
)
PROM_FILE = os.path.join(PROM_DIR, "foreman_agents.prom")
KILL_MARKER_GLOB = "/tmp/fworker-wq*.killed"


def collect():
    """Collect metrics and return Prometheus text format lines."""
    lines = []
    lines.append("# HELP foreman_agent_killed_total Number of agents killed by agent-kill")
    lines.append("# TYPE foreman_agent_killed_total counter")

    killed_markers = glob.glob(KILL_MARKER_GLOB)
    total_killed = len(killed_markers)
    lines.append(f"foreman_agent_killed_total {total_killed}")

    lines.append("# HELP foreman_agent_killed_active Currently killed agents (marker present)")
    lines.append("# TYPE foreman_agent_killed_active gauge")
    lines.append(f"foreman_agent_killed_active {total_killed}")

    lines.append("# HELP foreman_agent_credential_revocations_total Credential revocations from kill switch")
    lines.append("# TYPE foreman_agent_credential_revocations_total counter")
    revocations = 0
    if os.path.isdir(INCIDENTS_DIR):
        for inc_dir in glob.glob(os.path.join(INCIDENTS_DIR, "wq*")):
            chain_path = os.path.join(inc_dir, "kill-log.jsonl")
            if os.path.exists(chain_path):
                with open(chain_path) as f:
                    for raw_line in f:
                        raw_line = raw_line.strip()
                        if not raw_line:
                            continue
                        try:
                            entry = json.loads(raw_line)
                            if entry.get("event") == "kill_step":
                                detail = entry.get("detail", {})
                                if detail.get("step") == "revoke" and detail.get("result") == "revoked":
                                    revocations += 1
                        except json.JSONDecodeError:
                            pass
    lines.append(f"foreman_agent_credential_revocations_total {revocations}")

    lines.append("# HELP foreman_agent_quarantined_workspaces Quarantined agent workspaces")
    lines.append("# TYPE foreman_agent_quarantined_workspaces gauge")
    quarantined = 0
    wt_root = os.environ.get("FOREMAN_WORKTREES_ROOT", "/home/lumen/worktrees")
    if os.path.isdir(wt_root):
        for d in os.listdir(wt_root):
            q = os.path.join(wt_root, d, ".quarantined")
            if os.path.exists(q):
                quarantined += 1
    lines.append(f"foreman_agent_quarantined_workspaces {quarantined}")

    return "\n".join(lines) + "\n"


def write_metrics():
    text = collect()
    os.makedirs(PROM_DIR, exist_ok=True)
    tmp = PROM_FILE + ".tmp"
    with open(tmp, "w") as f:
        f.write(text)
    os.replace(tmp, PROM_FILE)


if __name__ == "__main__":
    write_metrics()
    print(f"Metrics written to {PROM_FILE}")
