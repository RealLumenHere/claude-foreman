# wq#1193 — wire lumen-persona into the Messenger chat prompt

Closes wq#1176 item 4 ("persona facts flow both ways") and its TESTS criterion,
"Messenger prompt contains persona facts". The change is complete and probed. It
is parked here rather than landed because the target repo is out of agent scope.

## Why this is a patch and not a PR

The target is `lumen/lumen-messenger` (`/home/lumen/messenger/agent_bridge.py`).
That repo has no line in `/home/lumen/infra/github/agent-scope.conf`, so it is
DENY by default and `lumen-pr start` refuses:

```
lumen-repo-scope: DENIED (write) — lumen/lumen-messenger is not ALLOWed.
Adding an ALLOW line is Frank's decision, not yours.
```

**The one decision needed: does `ALLOW lumen/lumen-messenger` go into
agent-scope.conf?** Everything below is mechanical once it does.

wq#1176 mis-scoped this item as part of a single-repo LPP PR; wq#1193 split it
out to the lane that owns the messenger. Neither ticket recorded that the
messenger repo is off-limits to agents — that is the real reason item 4 has now
missed twice, and it is worth fixing in agent-scope.conf rather than in a third
ticket.

## What it does

`lumen_persona_bridge.py` is new; `agent_bridge.diff` is six hunks against
`agent_bridge.py`. The two directions are deliberately asymmetric:

- **Stream → Messenger (read).** `_with_persona` sits between `_with_worklog`
  and `_with_telemetry` on both prompt-composition paths (sync and streaming),
  so every Messenger turn carries Lumen's canonical self-facts under the same
  `PERSONA (canonical - ...)` header the streamer uses. It reads lumen_state
  (subject `lumen-persona`) natively — one sqlite query, no node — so Messenger
  does not depend on LPP being checked out or built. It decodes the store's own
  predicate encoding (`i_like__caves` → `- i like: caves`) rather than scraping
  `lumen_state.py current`, whose human render is ambiguous for any value
  containing `" = "`.

- **Messenger → stream (write).** `_note_persona` mines every persisted reply
  for durable self-facts by shelling out to LPP's `persona_cli.ts state
  --platform messenger`. That is the SAME extractor and the SAME bi-temporal
  contradiction policy the streamer runs; reimplementing either in Python would
  recreate exactly the drift wq#1189 rejected. It runs on a daemon thread off
  the response path, serialised, and degrades to a logged no-op when LPP is
  absent.

Guardrails (wq#1176 item 5) are enforced on the messenger side, before anything
leaves the process: Messenger-Lumen holds tools and talks about the fleet in the
first person, and a sentence that reaches the persona store is a sentence the
stream may later read out loud. `is_safe_to_ingest` refuses IPs, MACs, API keys,
credential words, fleet unit names, filesystem paths and ticket refs. Rage
(hermit-crab) replies are not mined. Both directions fail open — a persona
failure never costs a reply.

## Apply

```
cd /home/lumen/messenger
git apply /home/lumen/foreman/ops/wq1193-messenger-persona/agent_bridge.diff
cp /home/lumen/foreman/ops/wq1193-messenger-persona/lumen_persona_bridge.py .
cp /home/lumen/foreman/ops/wq1193-messenger-persona/probe_persona_bridge.py .
python3 probe_persona_bridge.py          # 21 checks, all must pass
systemctl restart lumen-messenger
```

Verified 2026-09-02 against `/home/lumen/messenger` at `4e097f6`: `git apply
--check` is clean, and with the change applied all 21 probes passed. The tree
was then restored, so the wiring checks fail there until the apply step is run
again — which is the point of running them.

`/home/lumen/messenger` is dirty with a sibling agent's in-flight wq#1163
`email_recall` work. The diff does not overlap it — never `git add -A` there.

## Verify

`probe_persona_bridge.py` drives the real `lumen_state.py` against a throwaway
sqlite file, so it exercises the production read path, not a mock of it. It
covers both TESTS criteria: a fact ingested as if from the stream appears in the
Messenger system prompt; a later change of mind supersedes it; the ingest path
is argv-asserted to reach LPP's cross-platform CLI tagged `messenger`. It also
asserts the wiring in `agent_bridge.py` itself — two compositions, three
persisted-reply sites.

End-to-end across both repos needs wq#1191 (LPP PR93) merged and deployed; it is
on branch `feat/wq1191-lpp-rework-wq-1176-item-3-persona-memory-a1` only, so
`persona_cli.ts` does not exist on LPP main yet and the ingest half stays a
logged no-op until it does. The recall half works today.

## Loose ends this work turned up

- **`lumen_state.py` cannot build a working DB from scratch.** `current()` reads
  and `ingest()` writes `fact_class` / `render_tier`, added to minerva.db by
  migration 0003, but neither `init_schema()` nor `_migrate()` creates them, so
  a fresh file raises `no such column: fact_class`. Live minerva.db is fine; the
  probe adds the columns itself and says why. Worth a ticket against
  `lumen/minerva`.
- **Stray probe subject in live minerva.db.** Confirming the CLI's output format
  ingested `wq1193-probe.test_pred`. lumen_state has no delete verb, by design.
  It is one isolated subject under an obvious name; remove it if you ever prune.
- **Two inert files left in `/home/lumen/messenger`.** `lumen_persona_bridge.py`
  and `probe_persona_bridge.py` were written there before the scope refusal
  surfaced and could not be removed afterwards (the worker's `rm` is bounded to
  its own worktree). `agent_bridge.py` was reverted to the sibling's changes
  only, so nothing imports them and they cannot affect a running messenger. The
  copies here supersede them — the `cp` step above overwrites both.
