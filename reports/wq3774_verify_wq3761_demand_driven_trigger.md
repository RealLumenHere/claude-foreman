# wq#3774 — verify-pass on wq#3761 (research v2 3/3: demand-driven trigger service)

PR: lumen/claude-foreman#1771, commit fe120553 in this worktree. Author:
claude-sonnet-5 · Verifier: claude-sonnet-5@high (fresh instance, wq#3774).

## Claim under test

wq#3761 ships `research/trigger.py` (+ tests + systemd units + INSTALL.md) per
design/wq3755_research_pipeline_v2.md §2+§5: poll wq needs-tags
(`research(<question>)`) and aq alerts owned by `research`, formulate a brief
(constraint block from the rejection ledger + question + context + target
env), dispatch to the LiteLLM gateway via the router's archetype table, bounce
once on a draft with no runnable smoke test then drop it, otherwise submit to
the sandbox gate's inbox. §7 item 3's acceptance probe: "a `research(...)`
needs-tag on a fixture ticket yields a brief containing the seeded ledger's
constraint lines."

## What I checked

1. **Read the diff cold** (`git show fe120553`): `trigger.py` (355 lines),
   `tests/test_trigger.py` (205 lines, 12 cases), the two systemd units, and
   the INSTALL.md staged-install addendum. Cross-checked every imported
   symbol against its real definition — `ledger.connect`/`constraint_block`/
   `CONSTRAINT_ROWS`, `sandbox_gate.INBOX`/`ID_RE`/`IMAGES`/`load_proposal`,
   `harness/needs.py`'s `parse()`/`_TAG_RE`, `orchestrator/router.py`'s
   `load_manifest`/`select`/`ManifestError`/`Rung` — all match current
   signatures, nothing stubbed or renamed out from under the diff.
2. **Ran the full test suite**: `python3 -m pytest research/tests/test_trigger.py -v`
   → 12/12 passed, including `test_brief_contains_seeded_ledger_constraint_lines`,
   which is the design's own §7 item 3 acceptance probe verbatim (seeds two
   ledger rows, confirms the brief's `DO NOT re-propose:` lines precede
   `Question:`). The probe can fail — it would fail if `constraint_block`
   placement or `ledger.record` wiring regressed, since the assertion is on
   exact string positions, not presence alone.
3. **Tried to break it adversarially — `scan --dry-run` vs. the "served"
   definition.** `trigger.py`'s own docstring (lines 10-12) and
   `unserved_wq_questions`/`unserved_aq_alerts` (trigger.py:96-138) define
   "unserved" with no DB column: a `research(...)` tag counts as served **the
   moment its brief file exists on disk** — `os.path.exists(briefs_dir/<id>.md)`
   is the only check, nothing checks the sandbox gate, the bridge, or whether
   dispatch to the gateway ever happened.

   `trigger_one` (trigger.py:291-314) writes that brief file
   *unconditionally*, before the `if dry_run:` early-return (trigger.py:296-301):
   ```
   text = brief_text(...)
   path = write_brief(brief_id, text, briefs_dir)   # <- real, permanent file
   out = {...}
   if dry_run:
       return {**out, "verdict": "would_dispatch"}   # <- stops here, nothing dispatched
   ```
   `scan()` (trigger.py:317-321) passes its own `dry_run` straight through to
   every input it finds. The CLI exposes `--dry-run` identically on both
   `scan` and `ask` (trigger.py:343, help text: "write the brief, skip
   dispatch") with no caveat that for `scan` this has a side effect beyond
   the current invocation.

   For `ask`, this is harmless: every manual/replay invocation mints a fresh
   `ask-<timestamp>-<uuid>` id (trigger.py:326-327), so a dry run never
   collides with a future real one. For `scan`, the id is **stable and
   content-derived** (`wq{tid}` / `aq{aid}`) — the same id a later real scan
   would compute for the same still-open ticket/alert.

   Reproduced live (`python3 -I` against a throwaway `wq` queue.db, script
   kept at `/tmp/fworker-wq3774/probe_dryrun_scan.py`, not in the repo):
   - Added a fixture ticket with `Needs: research(does this leak a served
     tag on dry-run?)`.
   - `trigger.scan(db, None, briefs, pending, dry_run=True)` →
     `[{'brief_id': 'wq1', ..., 'verdict': 'would_dispatch'}]` — brief file
     written to `briefs/wq1.md`.
   - Immediately ran the **real** scan with a `fake_chat` that always returns
     a valid runnable proposal: `trigger.scan(db, None, briefs, pending,
     dry_run=False, chat=fake_chat)` → `[]`. Zero inputs found. No proposal
     written. `pending/` never created.

   The ticket's `research(...)` demand is now permanently unreachable by
   `scan` — not bounced, not dropped-with-reason, not logged anywhere as
   skipped. The only sign anything happened is a brief file on disk whose
   content ends in "would_dispatch" rhetoric it never fulfilled. Nothing in
   `unserved_wq_questions`, the bridge design (§4), or the weekly-cap
   accounting (§6) ever revisits a tag once its brief file exists, so there
   is no self-healing path: the question silently drops out of the "three
   ways research starts" guarantee the design doc states is the whole point
   of v2 (§0: "nothing upstream asks whether anyone needs the research" was
   v1's failure mode; this reintroduces a narrower version of the same thing
   for exactly the research questions an operator pauses to inspect).

   This is not a hypothetical misuse. `INSTALL.md`'s own "Live acceptance"
   section for the trigger unit (research/systemd/INSTALL.md:39-47)
   demonstrates `trigger.py ask "..." --dry-run` as *the* sanctioned way to
   smoke-test the service before enabling its timer — training exactly the
   instinct ("use `--dry-run` to see what trigger.py would do before it's
   live") that is destructive the one time it's pointed at `scan` instead of
   `ask`. No test in `test_trigger.py` exercises `scan(..., dry_run=True)` at
   all; the only dry-run coverage (`test_dry_run_writes_brief_but_does_not_dispatch`)
   goes through `trigger.ask`, which is structurally immune to this because
   of its random id.
4. **Checked the installed unit is not directly exposed.**
   `lumen-research-trigger.service` (research/systemd/lumen-research-trigger.service:17)
   runs `trigger.py scan` with no `--dry-run` — the timer-driven production
   path does not hit this. The risk is entirely in the documented/encouraged
   manual-inspection path, which is still a real operational surface (any
   on-call session debugging "why hasn't my `research(...)` tag fired"
   would reach for `--dry-run` first).
5. Logged the root cause and repro as `kb/ISSUE-LOG.md` row 1272 (status
   `open` — a live defect in shipped code).

## Verdict

**CONTRADICT.** The design's §7 item 3 acceptance probe holds — the brief
format and constraint-block injection are correct and well-tested. But the
service's own "served" rule (brief-file-exists, the agreed substitute for a
schema migration) is not dry-run-safe for the `scan` path: `trigger_one`
writes the real, permanently-checked brief file before branching on
`dry_run`, so `trigger.py scan --dry-run` — the exact inspection action the
shipped INSTALL.md trains operators to reach for — silently and irreversibly
consumes every currently-unserved wq/aq research demand signal it sees,
without ever dispatching, bouncing, or recording a drop reason for any of
them. This directly undermines the ticket's and design §0's central
guarantee: that every one of the three demand signals eventually reaches
research. Contradiction is a judgment trigger for opus-5@high per protocol;
a plausible fix shape is to key "served" on a non-dry-run write only (e.g.
write to a `.dryrun` staging path under `dry_run`, or branch before
`write_brief`), but that re-implementation decision belongs to the judgment
pass, not this verify-pass.

## Non-blocking observations (not part of the contradiction)

- `target_env()` (trigger.py:144-156) hardcodes `node="lumen"` for every
  brief regardless of which ticket/alert/manual context triggered it. The
  docstring defends this explicitly (the sandbox gate only ever runs on
  `lumen`), so it's a documented scope choice, not a defect — flagged only
  because it's the one place the design's "read from fleet state, never
  assumed" language (§2) could look unsatisfied at a glance.
- `submit_to_sandbox`'s id-sanitization fallback (trigger.py:274-281) always
  overwrites `prop["id"]` with the sanitized/fallback value even when the
  model's original id was already `ID_RE`-valid; harmless (round-trips to
  the same string) but worth noting since it means `prop["id"]` is never
  trusted verbatim even on the happy path.
