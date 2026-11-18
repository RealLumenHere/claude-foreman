# Agent Kill Switch + Credential Revocation — Incident Response Spec

TICKET: wq#4929

## 0. Problem

A compromised, misbehaving, or runaway fleet agent must be stoppable within seconds,
even when it is unresponsive or producing malicious tool calls. Today the fleet has:

- **DISPATCH_DISARM** — stops new dispatches fleet-wide (circuit_breaker.py)
- **SIGTERM/SIGKILL** — kills a running fworker child (fworker._kill_proc_tree)
- **token-engine ticket-end** — revokes minted tokens for a ticket (guard.sh lifecycle end)
- **runaway.py** — detects stuck-loop tickets and prevents redispatch
- **action_gate.py / guard.sh** — blocks dangerous tool calls per-session

What is missing: a **single command** that does all eight incident-response steps for a
specific agent, atomically, leaving a tamper-evident audit trail.

## 1. Emergency Control Plane — `agent-kill`

A single CLI tool, `security/agent_kill.py`, invokable as:

```
python3 security/agent_kill.py --ticket <id> [--reason <text>] [--dry-run]
```

### 1.1 Eight-step sequence (all steps are idempotent)

| Step | Action | Mechanism | Failure mode |
|---|---|---|---|
| 1. Stop agent process | SIGTERM → SIGKILL the fworker process tree | `_kill_proc_tree` via pgid from `/tmp/fworker-wq<id>.pid` | Process already dead = success |
| 2. Revoke provider credentials | `te_client.py --verb ticket-end` | token-engine daemon revokes all minted tokens | Daemon down = flag for reap_all on restart |
| 3. Invalidate MCP sessions | Write kill marker + SIGHUP to claude child | `kill-marker` file checked by guard.sh on next tool call | No live session = no-op |
| 4. Cancel queued work | `wq block <id> --notes "KILLED: <reason>"` | wq state machine; mortician won't auto-release | Already blocked/done = idempotent |
| 5. Release resource leases | `slock release-all --holder fworker-wq<id>` | slock's lease store; also release spawn reservation | No leases held = no-op |
| 6. Prevent restart | Write `/tmp/fworker-wq<id>.killed` marker | fworker checks marker before claiming; dispatcher skips | Marker already present = no-op |
| 7. Preserve audit records | Snapshot `_runs/wq<id>/` + events + worktree state | Copy to `security/incidents/wq<id>-<ts>/` | Dir already snapshotted = skip |
| 8. Quarantine workspace | `chmod 000` the worktree + write `.quarantined` marker | Prevents any reads/writes; reversible with `chmod 755` | Already quarantined = no-op |

Every step logs a JSON record to `security/incidents/wq<id>-<ts>/kill-log.jsonl`
before and after execution (intent + outcome), forming the incident timeline.

### 1.2 Kill marker protocol

The kill marker at `/tmp/fworker-wq<id>.killed` is a JSON file:

```json
{
  "ticket_id": 4929,
  "killed_at": "2026-11-18T10:30:00Z",
  "killed_by": "agent-kill",
  "reason": "Compromised MCP session producing unauthorized tool calls",
  "steps_completed": ["stop", "revoke", "invalidate_mcp", "cancel", "release", "prevent_restart", "preserve", "quarantine"]
}
```

**Three consumers check it:**
1. `fworker.py cmd_run()` — refuses to claim if marker exists
2. `foreman_dispatcher.py` candidate loop — skips ticket if marker exists
3. `dispatch_janitor.py` — never auto-releases a killed ticket

### 1.3 Dry-run mode

`--dry-run` executes steps 1–8 as read-only checks, printing what each step
*would* do and whether the preconditions are met. No state is changed.

## 2. AEGIS-Pattern Signed Audit Log

Based on the Ed25519 hash-chain pattern evaluated in wq#4859 (SysKnife) and the
AEGIS tool-call audit signer concept from the ticket:

### 2.1 Design: `security/audit_chain.py`

Each audit entry is a JSON object with:

```json
{
  "seq": 1,
  "ts": "2026-11-18T10:30:00.123456Z",
  "prev_hash": "0000000000000000000000000000000000000000000000000000000000000000",
  "worker_id": "fworker-wq4929",
  "ticket_id": 4929,
  "event": "kill_step",
  "step": "stop",
  "detail": {"pid": 12345, "signal": "SIGTERM", "result": "exited"},
  "principal": {"uid": 1000, "pid": 54321}
}
```

The chain is:
1. **SHA-256 of the entry** (canonical JSON, sorted keys, no whitespace)
2. **Ed25519 signature** over `prev_hash || entry_hash`
3. **prev_hash** = hash of the previous entry (genesis = 64 zero bytes)

This gives:
- **Integrity**: any modification breaks the chain
- **Non-repudiation**: the signing key proves which process wrote the entry
- **Ordering**: sequence numbers + prev_hash enforce total order
- **Offline verification**: `audit_chain.py verify <log-path>` checks the full chain

### 2.2 Key management

- One Ed25519 keypair per fleet node, generated at first use
- Private key: `/var/lib/agent-audit/signing.key` (0400, root-owned)
- Public key: `/var/lib/agent-audit/signing.pub` (0444, world-readable for verification)
- Key rotation: new key appends a `key_rotation` entry signed by the OLD key,
  then all subsequent entries use the new key. The public key file is a JSON array
  of `{pub, valid_from, valid_until}` for verification across rotations.

### 2.3 Integration with existing event infrastructure

The signed audit chain does NOT replace `events/<worker>.jsonl` (guard.sh's
best-effort operational log). It wraps the **incident-response actions only**:
kill steps, credential revocations, quarantine operations, and manual overrides.
Operational telemetry stays in the existing event pipeline — adding signing overhead
to every tool call would be disproportionate to the threat model.

## 3. Observability — Grafana Integration

### 3.1 Agent telemetry structured for the existing stack

The fleet already has Grafana. Rather than adopting Langfuse or Arize Phoenix (which
would add a new data store and UI), feed structured agent metrics into the existing
pipeline:

**Metrics (Prometheus text format, scraped by existing node_exporter textfile collector):**
- `foreman_agent_active{ticket_id, worker_id, model}` — gauge, 1 while running
- `foreman_agent_turns_total{ticket_id}` — counter
- `foreman_agent_wall_clock_seconds{ticket_id}` — gauge
- `foreman_agent_killed_total{reason}` — counter of kill-switch invocations
- `foreman_agent_credential_revocations_total` — counter

**Textfile location:** `/var/lib/prometheus/node-exporter/foreman_agents.prom`

**Dashboard:** `security/grafana/agent-incident-dashboard.json` — a provisioned
Grafana dashboard showing active agents, recent kills, and credential revocation
status.

### 3.2 Why not Langfuse/Phoenix

Both are excellent for LLM observability in a development workflow. For this fleet:
- The fleet already runs Grafana with Prometheus; adding another data store adds ops burden
- Langfuse needs Postgres + ClickHouse; Phoenix needs its own collector
- The kill-switch telemetry is infrastructure metrics, not LLM trace data
- If LLM-level tracing is needed later, it can feed into the existing Grafana via
  OTLP → Prometheus remote-write without a separate UI

## 4. Fleet integration points

### 4.1 fworker.py changes

Add to `cmd_run()` before the claim:
```python
killed_marker = f"/tmp/fworker-wq{ticket_id}.killed"
if os.path.exists(killed_marker):
    log(f"wq#{ticket_id} has been killed (marker at {killed_marker}); refusing to claim")
    return EXIT_BLOCKED
```

### 4.2 foreman_dispatcher.py changes

Add a gate in `gates.py`:
```python
class KilledTicketGate(Gate):
    def check(self, g):
        marker = f"/tmp/fworker-wq{g.tid}.killed"
        if os.path.exists(marker):
            return Fail("killed", "ticket has been killed by agent-kill",
                        terminal=True, parkable=True)
        return PASS
```

### 4.3 guard.sh changes

Add kill-marker check in the PreToolUse path:
```bash
KILL_MARKER="/tmp/fworker-wq${FOREMAN_TICKET_ID}.killed"
if [ -f "$KILL_MARKER" ]; then
    echo "guard.sh: ticket killed — blocking all tool calls" >&2
    exit 2
fi
```

## 5. Recovery

After investigation, to un-kill an agent:

```
python3 security/agent_kill.py --unkill --ticket <id> --reason <text>
```

This:
1. Removes the kill marker
2. Restores worktree permissions (chmod 755)
3. Logs the unkill to the signed audit chain
4. Does NOT unblock the wq ticket (manual `wq unblock` required — deliberate friction)

## 6. Liveness test

```
test -f /home/lumen/foreman/security/agent-kill-switch-spec.md
```

The spec file at the path the ticket names. The implementation files:
- `security/agent_kill.py` — the kill switch CLI
- `security/audit_chain.py` — Ed25519-signed hash-chain audit log
- `security/tests/test_agent_kill.py` — unit tests
- `security/tests/test_audit_chain.py` — chain integrity tests
