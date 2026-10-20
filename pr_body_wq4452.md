## What

wq#4452 is the opus-5@high judgment call on wq#4451's CONTRADICT verdict against wq#4450
(`[wq-integrity]` cleanup of session `cleanup-s-19026e`'s mass-close, PR #2330). wq#4451 found
that wq#4450's 12-line `selfheal.yaml` note and its 4 reopens / 2 correct non-reopens are all
sound, but that its *premise* — having found the complete same-second mass-close population —
was false: wq#3274 (`[hw] Seshat zombie: P6 draws 27W but no LAN/tailnet after power cycle`) sat
inside the cited `done_at` window with the identical "Mac fleet retired - no action needed"
boilerplate, `reopened_count=0`, never touched.

Landed here:

- `reports/wq4452_judgment_wq4450_seshat_3274.md` — the ruling, both queries, the `lumen_state`
  evidence, and the reasoning for option (a) over option (b).
- **wq#3274 reopened** (queue.db state, not in this diff): `done → blocked`,
  `reopened_count 0 → 1` at `2026-10-20 07:16:52`, `gate_type=frank_decision`;
  `block_reason`/`notes` carry the erroneous-close record plus `lumen_state seshat.hardware =
  Seneca PRO925430 / GIGABYTE MTGU3CS-SD / i3-1115G4` (not a Mac — the same evidence wq#4450
  used for wq#1691), `seshat.remediation_hold` (2026-09-18, "Do NOT wake/remediate") and
  `seshat.power_state = off — Frank powered down physically` (2026-10-19). Same treatment
  wq#4450 gave wq#1341/wq#1691: a Frank-gated physical task.
- `kb/ISSUE-LOG.md` row 1492 corrected in place (stays RESOLVED — the remediation is now
  actually complete), plus new row **1495** for the reusable rule.

## Why

The escalation offered (a) patch the sweep in place, or (b) require a fuller re-check of how
wq#4450 built its worklist. Ruling: **(a)**, with option (b)'s question answered inline rather
than redispatched.

wq#4451's method diagnosis is correct and is adopted: wq#4450's worklist came from wq#3252's
dependency neighbourhood (the wq#1341 path), not from the `done_at` window its own title names —
which is exactly why it contained 4422/4439 (outside the window) and omitted 3274 (inside it).
wq#4450's own resolution notes concede 4422/4439 fall outside the window without noticing that
this falsifies its stated derivation.

But the remedy for a bad derivation is to run the stated predicate once, not to re-implement a
diff whose every other claim a verifier already confirmed against primary evidence. Blast radius
is bounded: `select id from tasks where deps like '%3274%'` is empty, so the miss un-gated
nothing (unlike wq#1341 → wq#3252). The ticket also explicitly forbids re-implementing wq#4450
wholesale.

## How

1. Re-derived the population from the predicate wq#4450's ticket cites, read-only against
   `queue.db` — `done_at` in `2026-10-20 06:38:00`–`06:38:20`: exactly 7 rows — 1691, 2076,
   2842, **3274**, 4178, 4415, 1341. wq#3274 confirmed `status=done`, `reopened_count=0`,
   `claimed_by=None`, verbatim boilerplate.
2. Cross-checked the false premise against live state (`lumen_state current seshat`):
   Seneca/Gigabyte hardware, `remediation_hold`, `power_state=off`, `role = Nextcloud +
   Collabora` (the live-service impact wq#3274's own detail names).
3. Reopened wq#3274 via `wq block` + `wq edit --gate-type frank_decision`, mirroring the
   wq#1341/wq#1691 treatment.
4. Corrected ISSUE-LOG row 1492 with `Edit` (existing row), and filed the new lesson row via
   `issue_log_append.py` (allocated 1495 under its lock, per wq#3174).

## Testing

**The falsifiable probe (wq#2630):** the fix's scope parameter is the 20-second `done_at`
window, so the acceptance probe deliberately drops that window entirely and scans every ticket
the session could have touched:

```sql
select id,status,done_at,claimed_by from tasks
 where notes like '%Mac fleet retired%' or notes like '%cleanup-s-19026e%'
    or claimed_by = 'cleanup-s-19026e'
```

10 rows. This probe *could* have failed — it was free to surface an 8th victim outside the
window, which would have forced option (b). It did not: the 3 extra rows are wq#4215 (a real,
fully-evidenced LiteLLM Cerebras-disable close with config backups and a 37-model
`/v1/models` check), wq#4422 and wq#4439 (substantive completion evidence — wq#4450's decision
to leave them closed stands), and wq#4432 is a legitimate `blocked` park on hapi-1 bring-up.
The population is therefore closed at 7, and that is the re-check option (b) asked for.

Post-change state verified by direct read: `(3274, 'blocked', 'frank_decision', 1,
'2026-10-20 07:16:52', '2026-10-20 07:16:52', '2026-10-20 06:38:03')` for
`(id, status, gate_type, reopened_count, reopened_at, blocked_at, done_at)`.

## Risk / rollback

Low. This diff adds one report file and changes no code — no selfheal, dispatch, or gating
behaviour is touched. The behavioural change is one queue row moving `done → blocked`, which
re-exposes a genuine Frank-gated physical task; it cannot cause work to be dispatched, because
`gate_type=frank_decision` plus the two pre-existing `[NO-PROBE: physical task: box unreachable,
needs hands on console]` markers hold it. (`wq block` emits an advisory `liveness NO_TAG`
warning for any Frank-gate with no `Liveness:` line; wq#1341 carries the identical NO-PROBE
shape — advisory, not enforced at block, wq#1998.)

Rollback is `wq done 3274` with the prior notes, plus reverting the two ISSUE-LOG edits.
Note for anyone re-querying this window: `wq block` does not clear `done_at` (`wq:2626-2631`),
so all 7 rows keep `done_at` inside `06:38:01-19` even after reopen — use
`status`/`reopened_count` as the still-mis-closed discriminator, never `done_at`.

Refs: wq#4452

🤖 Generated with [Claude Code](https://claude.com/claude-code)
