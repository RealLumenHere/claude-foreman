# wq#3309 — episode spool: terminal state for entries whose identity can never authenticate again

Status: DESIGN (this doc is the wq#3309 deliverable; implementation is a follow-up
ticket in `lumen/minerva`, filed from this ticket's close).

## Problem (verified live, 2026-10-01)

`lumen-episode-spool-replay.timer` (wq#3173) drains `~/.episode_spool/` every 15 min
by re-POSTing each entry through `lumen_episode._memoryd_commit()` under the entry's
original `WQ_AGENT`. The live spool held 8 entries; 6 of 8 can never land:

```
20261001T031856…-5c924500.json  agent=wall-5-f539f2            attempts=32  reject: agent 'wall-5-f539f2' is not in the wq session registry
20261001T031905…-0b7e94c1.json  agent=wall-5-f539f2            attempts=31  (same)
20261001T095805…-c2bc71ff.json  agent=orch-ewf-rebuild-c0ae0e  attempts=5   (same)
20261001T100202…-7d5c967c.json  agent=mega-d-0961f0            attempts=4   (same)
20261001T100657…-7a33f71a.json  agent=orch-ewf-rebuild-c0ae0e  attempts=4   (same)
20261001T101129…-8f4ac0a9.json  agent=megac2-d7372d            attempts=4   reject: agent writes must carry source_ref=wq#N …
```

All three "registry" agents have no `agent_sessions` row and no `retired_sessions`
row — they were purged by `wq`'s stale-session sweep (`bin/wq:952`, `DELETE FROM
agent_sessions WHERE last_seen < ?`), not formally retired. Either way the identity
is gone: session ids carry a random hex suffix minted fresh per session
(`bin/wq:1321` loops until unused), so a purged id never re-registers. The replayer
(`lumen_episode_spool_replay.py:79`) only stops at `MAX_ATTEMPTS=200` (~50 h at
15-min cadence) or `MAX_AGE_DAYS=14`, so each such entry burns ~200 futile POSTs and
~200 `still-rejected` log lines, then lands in `expired/` — a state that reads as
"lost" when the content was never at risk.

Nothing is lost because `lumen_episode.commit()` (minerva `lumen_episode.py:239-255`)
spools *and then* falls back to `mm.write_note_verified()` — the content is already
in the local archival store (archival_notes + audit row + recall_fts, db-verified)
before the first replay ever runs. The spool's only job is to late-land the
*governed memoryd audit row*; for a dead identity that job is impossible by design.

## CLASS

A spool entry has no terminal state for "the originating identity can no longer
authenticate." The only exits are `landed` (needs an accept that cannot happen) and
`expired` (time/attempt exhaustion, semantically "gave up, cause unknown"). Standard
queue doctrine (prior-art: DLQ/poison-message literature — classify failures
retryable vs permanent; permanent failures stop immediately and route to a terminal
store with metadata) says the permanent class must be detected and exited eagerly,
not aged out.

## Reject taxonomy (from `v4/memoryd/daemon.py:resolve_identity`, 79-159)

| # | reject reason | terminal? | why |
|---|---|---|---|
| R1 | `agent %r is not in the wq session registry` (daemon.py:141) | **yes**, once the id is absent from `agent_sessions` and past a grace window | row deleted on release/purge; ids never re-minted (`bin/wq:1321`); `retired_sessions` row (bin/wq:505-511) is an even stronger explicit marker when present |
| R2 | `agent does not hold a live claim on wq#N` (daemon.py:148) | no while the agent is registered | the session can still claim/re-claim; flips to R1-terminal when the session goes away |
| R3 | `agent writes must carry source_ref=wq#N` (daemon.py:158) | **yes, immediately** | replay re-POSTs the identical payload; nothing about a retry adds a source_ref (also flagged in reports/wq3195 §secondary) |
| R4 | fworker: `does not hold the claim on wq#N` (daemon.py:138) | **yes** iff `tasks.claimed_by != agent` | the claim RECEIPT (claimed_by, which survives close — kernel.py:327) has been overwritten by a re-claim; it will never name this fworker again. While claimed_by still matches, R4 can't occur. |
| — | network error / 5xx / non-403 | no | ordinary transient; today's retry behavior is correct |

## Decision: terminal state = `recovered/` (option a), NOT service-identity re-attribution (option b)

Option (b) — replay under a service identity with an audit field — is rejected:

1. The service identity is **read-only by contract** (daemon.py:103-104: "service
   identity is read-only; writes need a held wq claim (I-7)"). Granting the replayer
   a write-capable service credential creates an attribution-laundering path: any
   payload that reaches the spool gets promoted into the governed audit trail under
   an identity that never held the claim. That inverts the exact invariant the gate
   exists to hold.
2. The payoff is nil. The only thing a late memoryd accept would add is the governed
   audit row — and the local fallback already wrote an audit row at commit time
   (`invariant=wq1634-fail-open` or `wq2901-memoryd-reject`, lumen_episode.py:231-254).
   Forging a late "accept" would make the audit trail *less* truthful, not more.

Option (a): classify-at-replay, verify the content is locally durable, then move the
entry to `spool/recovered/` with a recovery record and stop retrying.

## Design

### 1. Terminality pre-check in `replay_one()` (lumen_episode_spool_replay.py)

Before POSTing, classify the entry:

```python
GRACE_HOURS = 1          # dodge any registration race on fresh entries

def _identity_terminal(entry):
    """(terminal: bool, why: str). Fail-open to 'retry' on any DB error --
    misclassifying toward retry is the status quo and loses nothing."""
    agent = entry['agent']; src = (entry.get('source_ref') or '').strip()
    fw = FWORKER_IDENTITY_RE.match(agent)
    if fw:
        # R4: receipt check, same read kernel.ticket_claimant() does (read-only
        # sqlite on queue.db). Terminal only when claimed_by names someone else.
        claimant = _ticket_claimant(fw.group(1))
        return (claimant is not None and claimant != agent,
                'claim receipt now names %s' % claimant)
    if not src.startswith('wq#'):
        return True, 'no source_ref; retry can never add one (R3)'     # R3
    if _age_days(entry) * 24 < GRACE_HOURS:
        return False, 'inside grace window'
    if _session_retired(agent):                                         # R1
        return True, 'session id absent from agent_sessions (purged/retired)'
    return False, 'agent registered or registry unreadable'
```

`_session_retired()` / `_ticket_claimant()` are read-only sqlite queries against
`queue.db`, byte-for-byte the same SELECTs as `kernel.agent_registered()` /
`kernel.ticket_claimant()` (v4/memoryd/kernel.py:303-342), plus a
`retired_sessions` lookup. This is **not** a second copy of the accept gate (the
wq#3173 docstring's drift concern): an accept still only ever happens through
`le._memoryd_commit()` → daemon. The pre-check only decides *whether a future
accept is possible*, and every failure mode degrades to "keep retrying".

### 2. Recovery = verify-then-move, never assume

Local note ids are deterministic: `write_note_verified()` computes
`nid = note_id(kind, title, content)` (minerva_memory.py:212-232) from exactly the
three fields the spool entry carries. So the replayer can *prove* "content is in the
local archival store" without any spool-format migration:

```python
local_nid = mm.note_id(entry['kind'], entry['title'], entry['content'])
local_ok  = mm.note_exists(local_nid)        # read-only SELECT on archival_notes
```

- terminal + `local_ok` → append `{recovered_at, recovery: 'local-only',
  local_nid, final_reason, attempts}` to the entry, `os.replace()` it into
  `spool/recovered/` (same atomic move+0o700-mkdir shape as the existing
  `expired/` branch, replay:79-83), outcome `recovered-local`.
- terminal + NOT `local_ok` (possible: `_spool_rejected` runs *before* the
  fallback write, which can itself die — lumen_episode.py:244 vs :249) → the spool
  holds the only copy. The replayer performs the local write itself via
  `mm.write_note_verified(..., audit={'detail': 'spool-replay local recovery; '
  'original agent <id> unreplayable: <reason>', 'invariant': 'wq3309-recovered-local'})`
  — the ticket's "re-attribute with an audit field", but through the existing
  local fallback writer, not a new daemon privilege — then move to `recovered/`.
- terminal classification errors → fall through to today's retry path unchanged.

Every recovery also writes one audit row with `invariant='wq3309-recovered-local'`
(mirrors the wq#2901 breadcrumb pattern) so one SELECT counts recoveries, and the
summary line gains `recovered=N`. Log noise ends because the file leaves the spool
on its first post-retirement replay instead of its 200th.

### 3. Spool-time stamp (lumen_episode.py, small)

In `commit()`, capture `_spool_rejected()`'s returned filename; after the fallback
`write_note_verified()` succeeds, stamp `local_nid` into the spool file. Replay
trusts-but-reverifies the stamp; legacy entries recompute via `note_id()` as above.

### 4. Retry-budget trim (secondary)

With terminal classes exiting eagerly, `MAX_ATTEMPTS` no longer has to double as the
dead-identity backstop. Drop 200 → 96 (~24 h at 15-min cadence) for the genuinely
transient remainder; keep `MAX_AGE_DAYS=14`. `expired/` now means only "transient
failure that never cleared", which is the alertable condition it should be.

### 5. Tests (minerva `tests/test_episode_spool.py` additions)

1. purged-registry agent, content present locally → `recovered-local`, file in
   `recovered/`, audit row `invariant=wq3309-recovered-local`, **no POST made**.
2. purged agent, content absent locally → local write performed with
   original-agent audit detail, then recovered (spool is never the only copy lost).
3. no `source_ref`, non-fworker → terminal at attempts=0 (no 200-attempt burn).
4. **can-fail probe (wq#2630)**: agent still registered in a fixture queue.db →
   entry is NOT recovered, outcome `still-rejected` — drawn from outside the
   fix's target population, so the probe can falsify over-eager recovery.
5. fworker id with `claimed_by` intact → retried; with `claimed_by` re-claimed by
   another id → recovered.
6. entry younger than `GRACE_HOURS` with unregistered agent → retried, not recovered.
7. queue.db unreadable → retried (fail-open to retry).

### Non-goals

- No daemon.py / kernel.py changes; the gate is correct and untouched.
- No hand-deletion or migration of today's 8 live entries: the first timer run
  after the implementation lands recovers them through the same audited path.
- No spool-format version bump: new fields are additive, legacy entries replay.

## Follow-up

Implementation ticket to file in the `lumen/minerva` repo (this foreman branch is
docs-only): "implement design/wq3309_spool_terminal_state.md — terminal state +
recovered/ for episode spool replay", referencing this doc and the test list above.
