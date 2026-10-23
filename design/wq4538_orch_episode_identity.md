# wq#4538 — FIX CLASS: orchestrator episode commits always rejected by memoryd I-7

Status: DESIGN + DECISION (this doc is the wq#4538 deliverable; implementation is a
follow-up ticket in `lumen/minerva`, same split as wq#3309 → wq#3315). Decision made
under instinct 3 (full autonomy): **S2 + QW3. MW2 is rejected.**

## Problem (verified live, 2026-10-23, this session)

memoryd's identity gate I-7 (`v4/memoryd/daemon.py:140-168`, session branch) accepts a
write only when `source_ref` is a single `wq#N` whose live lease (`kernel.agent_claims`)
or claim receipt (`kernel.ticket_claimant`, the wq#3352 fallback) names the committing
agent. Orchestrator sessions never claim anything — they register (`wq whoami --register
orch-<slug>`, forced at `--core` by wq#1127), dispatch fworkers, close tickets, then
commit a session episode citing the tickets they shepherded. Every such commit rejects:

```
sqlite3 minerva.db 'select substr(detail,1,70),count(*) from memory_audit
                    where action="reject" and ts>datetime("now","-7 days")
                    group by 1 order by 2 desc limit 6'
identity: agent does not hold a live claim on wq#4469|314
identity: agent does not hold a live claim on wq#4419|194
identity: agent does not hold a live claim on wq#4455,wq#4456|97   ← list form
identity: agent does not hold a live claim on wq#4433|97
identity: agent does not hold a live claim on wq#3873|97
identity: agent does not hold a live claim on wq#3470|76
-- total: 1,325 rows / 7 days, every one with agent_id='' (anonymous)
```

Cross-checked against queue.db: wq#4419/4433/4469 have `claimed_by=fworker-wq<N>` (the
fworker, never the orch), wq#3873 has `claimed_by=NULL` (closed without ever being
claimed), wq#3470 is claimed by a *different* orch session than the committer. So the
wq#3352 receipt fallback can never fire for these — by construction, not by bug. The
spool replays each entry every 15 min (142 recovered-local, 14 expired, ~5 in flight);
`wq#4455,wq#4456` additionally shows the list form, where `tid = src[3:]` =
`"4455,wq#4456"` and every kernel lookup misses. The episode content itself is safe
(`lumen_episode.py commit()` falls back to `mm.write_note_verified()` locally,
`lumen_episode.py:239-255`); what is lost is the governed audit row and the agent
attribution, plus ~1,300 junk reject rows/week and a replay timer that hammers memoryd
with POSTs that cannot ever land.

## CLASS

The gate keys attribution exclusively on *claims*; the orchestrator role produces
governable work (dispatch decisions, triage, session synthesis) without ever holding
one. Every identity class that commits episodes needs a claim-shaped provenance object,
or its writes are structurally unauditable.

## Options considered

### MW2 — widen the gate: accept same-agent closed-within-24h + parse ranges/lists. REJECTED.

1. **The data MW2 needs does not exist.** queue.db `tasks` has no `done_by`/`closed_by`
   column. `wq done` (`bin/wq:2640-2643`) writes only `done_at` and leaves `claimed_by`
   untouched (it keeps naming the fworker); the one path that does write `claimed_by` on
   close is the wq#1478 liveness auto-clear (`bin/wq:1520-1522`), which stamps the
   *liveness caller*, not the worker. "Closed by the same agent" is therefore
   unverifiable from any existing column — MW2 silently grows into a wq schema change
   (add + backfill + populate `done_by`) plus a daemon change plus client changes. The
   audit's 2–3 h estimate was written against a column that was assumed, not verified.
2. **Close is weaker provenance than claim.** `claimed_by` is written only by `wq claim`
   (the property wq#2901 built the receipt check on, `v4/memoryd/kernel.py:327-341`).
   `wq done` can be run by any registered session on any ticket — wq#1116 is the
   recorded incident of session A closing session B's ticket. Accepting close-provenance
   lets an agent acquire write-attribution in a lane by closing someone else's work:
   attribution laundering through the exact invariant I-7 exists to hold.
3. **Ranges/lists make lane derivation ambiguous.** The lane is read out of the ticket
   precisely so "a lane cannot spell its own name" (`daemon.py:80-82`). A multi-ticket
   source_ref spanning lanes has no single honest answer.

### S2 — synthetic session claim at boot. ACCEPTED.

Give every orchestrator session one real claim: a session ticket, filed and claimed in
the same `--core` block that already gates on registration (wq#1127). The gate is not
touched — I-7 stays exactly as strict as today, which is also why the ticket's
test-to-failure requirement holds by construction *and* stays testable.

## Design

### 1. QW3 first (independent, stops the bleeding): replay gives up after 3 × does-not-hold

`lumen_episode_spool_replay.py` `_identity_terminal()` (line 106) gains one clause —
R2-terminal, slotting into the wq#3309 taxonomy after the R3 check:

```python
    # R2→terminal (wq#4538): post-wq#3352 the daemon already consults the claim
    # receipt before emitting "does not hold", so a does-not-hold that survives 3
    # replays (~45 min, outlasting any claim/registration race) means the receipt
    # names someone else or nobody — a state no future replay of the identical
    # payload can change. Covers both the session branch ("a live claim on") and
    # the fworker branch ("the claim on") message forms.
    if (int(entry.get('attempts', 0)) >= 3
            and str(entry.get('last_reason', '')).startswith('reject: agent does not hold')):
        return True, 'does-not-hold ×%d; receipt will never name this agent (R2-terminal)' % entry['attempts']
```

`last_reason` is exactly this string by construction: the daemon's 403 body reason is
re-prefixed `'%s: %s' % (status, reason)` → `reject: agent does not hold …`
(`lumen_episode.py:220-231`), and `replay_one()` stamps it into the entry
(`lumen_episode_spool_replay.py:240-241`). Recovery then flows through the existing
wq#3309 verify-then-move machinery (`_recover()`, line 151): local copy proven or
written, file → `spool/recovered/`, one `wq3309-recovered-local` audit row, no POST
ever again. Expected effect: ~1,300 reject rows/week → ~40 (3 per new entry), and the
current backlog drains on the first timer run after deploy.

### 2. S2 prerequisite, LANDED ON THIS BRANCH: the `session` lane

`wq add` refuses a lane with no `dispatcher/config.json` entry (wq#912, `bin/wq:1628`),
and refuses enabled lanes with no `LANE_REPO` row (wq#2139, `bin/wq:233-252` — the
refusal set is `enabled - mapped - human`, where `enabled` requires
`p.get("enabled") is True`). A lane entry with `"enabled": false` therefore passes
both gates while guaranteeing the dispatcher never spawns a worker on a session
ticket (`foreman_dispatcher.py` load_config keeps disabled lanes verbatim and skips
them). This branch adds exactly that entry: `dispatcher/config.json` `lanes.session`
(`"enabled": false`, with a `_note` carrying this rationale). Verified: the edited
config parses, `load_config()`'s per-lane validation emits no warning for it, and it
stays out of the wq#2139 enabled-set. Note wq reads this config COMMITTED at git HEAD
of `/home/lumen/foreman` (wq#772) — the lane exists for `wq add` only after this PR
lands and the deployed checkout is fast-forwarded (rollout step 0).

### 3. S2: session ticket at boot (claude_context.sh --core)

Immediately after the wq#1127 REGISTERED check in
`/home/lumen/minerva/context/claude_context.sh` (untracked live file; change ships as
an ops diff, same vehicle as `ops/wq1127-core-boot-identity-guard/`); verified shapes:
`wq claim` takes `--id N --lane session` (`bin/wq:3982`), reuse-before-add reads
queue.db read-only the same way kernel.py does:

```sh
    # wq#4538 S2: one real claim per orchestrator session, so every episode commit
    # carries provenance I-7 can verify. The receipt (claimed_by) survives close,
    # so even post-session spool replays land (wq#3352). Reuse before add: a
    # re-booted session re-claims/reuses its own open ticket instead of leaking one.
    session_tid=$(sqlite3 "file:$QUEUE_DB?mode=ro" \
        "SELECT id FROM tasks WHERE lane='session' AND claimed_by='$session_id' \
         AND status='claimed' ORDER BY id DESC LIMIT 1" 2>/dev/null)
    if [ -z "$session_tid" ]; then
        /home/lumen/bin/wq add --lane session \
            --title "session: $session_id $(date -u +%F)" \
            --detail "synthetic session ticket (wq#4538 S2): claim anchor for episode commits" \
            --no-probe "session anchor; closes at handoff with the session summary"
        session_tid=$(sqlite3 "file:$QUEUE_DB?mode=ro" \
            "SELECT id FROM tasks WHERE lane='session' AND title LIKE 'session: $session_id%' \
             ORDER BY id DESC LIMIT 1")
        /home/lumen/bin/wq claim --id "$session_tid" --lane session
    fi
    export WQ_SESSION_TICKET="wq#$session_tid"
    echo "===== SESSION TICKET: wq#$session_tid (episode commits: source_ref=wq#$session_tid) ====="
```

(`wq add` has no id-only output mode, hence the read-back; `--no-probe` satisfies the
wq#1676 liveness on-ramp for a ticket whose deliverable is the session summary
itself.) Lifecycle: the session closes
its own ticket at handoff (`wq done` with the session summary as notes) or leaves it
to the stale-claim sweep — either way the claim *receipt* keeps naming this session
id, so late spool replays still authenticate (wq#3352, `daemon.py:148-157`).

### 4. S2 client half: lumen_episode.py normalises the ref, keeps the truth

In `commit()` (`lumen_episode.py:239`), before `_memoryd_commit`:

```python
    # wq#4538: an orch cites the tickets it shepherded, but provenance I-7 can
    # verify is its own session ticket. Keep the shepherded refs in content/tags
    # (searchable), commit under the claim actually held. No held anchor → today's
    # behavior unchanged (reject → local fallback → spool).
    anchor = os.environ.get('WQ_SESSION_TICKET', '')
    ref = (session_ref or '').strip()
    if anchor and ref and ref != anchor and not _held_single_ref(ref):
        content += '\n## Tickets\n%s\n' % ref
        tags = list(tags or []) + ['tickets:' + ref]
        session_ref = anchor
```

`_held_single_ref()` = `re.fullmatch(r'wq#\d+', ref)` and
`kernel`-equivalent read-only queue.db check `claimed_by == agent` — i.e. an fworker
or a session committing against its own genuinely-claimed ticket is left alone;
only the orphan ranges/lists/other-agent refs are folded into content. Nothing is
dropped: the range text the orch wrote is preserved verbatim where recall/FTS finds it.

## Test to failure (ticket requirement; wq#2630 can-fail probes)

minerva `v4/memoryd/tests/` + `tests/test_episode_spool.py` additions:

1. **other-agent ticket must still reject** (the ticket's named probe): registered
   session commits `source_ref=wq#N` where queue.db fixture has
   `claimed_by=fworker-wqN, status=done` → reject, exactly today's message. Drawn from
   outside S2's target population (it exercises the gate S2 deliberately does not
   touch), so it can falsify a gate regression smuggled in with the client changes.
2. session commits with `source_ref=<own synthetic ticket>`, claim live → accept, lane
   = `session`.
3. same, after `wq done` on the synthetic ticket → accept via receipt fallback
   (wq#3352 path, `daemon.py:156`).
4. QW3: spool entry, non-fworker agent, `attempts=3`,
   `last_reason='reject: agent does not hold a live claim on wq#4455,wq#4456'` →
   `recovered-local`, file in `recovered/`, no POST.
5. QW3 can-fail: identical entry but `attempts=2` → still-rejected (retried), NOT
   recovered — falsifies over-eager recovery.
6. QW3 can-fail: `attempts=5` but `last_reason='commit HTTP 503'` → retried, NOT
   recovered (transient class untouched).
7. normalisation: `WQ_SESSION_TICKET` set, `session_ref='wq#4470-4474'` → POST body
   has `source_ref=<anchor>`, content gains `## Tickets wq#4470-4474`; with
   `session_ref` a single ticket the caller's fixture queue.db shows they claimed →
   body unchanged (can-fail: fworker path must not be rewritten).

## Non-goals

- No change to `resolve_identity()` or `kernel.py` — the gate is correct; the callers
  were starved of claims (S2) and the replayer of a terminal state (QW3).
- No `done_by` column, no range parsing in the daemon (MW2 rejected above).
- No migration of the current spool backlog: QW3's first timer run recovers it through
  the audited path.

## Rollout order

0. This PR: `session` lane entry (disabled) in `dispatcher/config.json` + this doc;
   fast-forward the deployed `/home/lumen/foreman` checkout so wq's HEAD-read sees it.
1. QW3 (replay terminal state) — lumen/minerva PR, deploy, trigger
   `lumen-episode-spool-replay.service` once: backlog drains, reject noise stops.
2. S2 client half (`lumen_episode.py`) — same PR as QW3 or next.
3. S2 boot half — ops diff to `claude_context.sh` (foreman `ops/wq4538-session-claim/`),
   applied via the attended boot-script path.
4. Watch one week: `select count(*) from memory_audit where action='reject' and
   ts>datetime('now','-7 days')` — expect <50; spool `recovered/` absorbs the backlog.

## Follow-up

Implementation ticket filed in the work queue at this ticket's close (this foreman
branch is docs-only, per wq#3309 precedent): "implement
design/wq4538_orch_episode_identity.md — QW3 replay R2-terminal + S2 session claim
anchor", referencing the test list above.
