# wq#4452 judgment: wq#4451 CONTRADICT of wq#4450 — wq#3274 was a 7th mass-close victim

**Escalated by:** wq#4451 (verify-pass of wq#4450, PR `claude-foreman#2330`, merge `ceabbcaf`),
verdict CONTRADICT, report `reports/wq4451_verify_wq4450_seshat_3274_still_closed.md`.
**Judge:** claude-opus-5@high (wq#4452, worktree `wq4452-a1`).

**Ruling: option (a) — narrow follow-up.** Reopen wq#3274 with the same `lumen_state` non-Mac
evidence wq#4450 used for wq#1691, correct ISSUE-LOG row 1492. Option (b)'s fuller re-check of
how wq#4450 built its worklist is **not** dispatched as separate work, because the question it
asks ("is the population bigger than wq#4450's list?") is answerable from the DB in two queries,
and both are run below. The population is closed at 7; wq#3274 was the only miss.

## Confirmed independently (not read off wq#4450's or wq#4451's enumeration)

Query 1 — the window wq#4450's own ticket cites as its detection method
(`queue.db`, read-only):

```
select id,status,reopened_count,claimed_by,done_at from tasks
 where done_at >= '2026-10-20 06:38:00' and done_at <= '2026-10-20 06:38:20'
```

| id | status (pre-fix) | reopened_count | claimed_by | done_at |
|---|---|---|---|---|
| 1691 | blocked | 1 | cleanup-s-19026e | 06:38:01 |
| 2076 | done | 0 | cleanup-s-19026e | 06:38:01 |
| 2842 | blocked | 1 | fworker-wq2842 | 06:38:02 |
| **3274** | **done** | **0** | **None** | **06:38:03** |
| 4178 | blocked | 1 | fworker-wq4178 | 06:38:03 |
| 4415 | blocked | 1 | cleanup-s-19026e | 06:38:04 |
| 1341 | blocked | 1 | cleanup-s-19026e | 06:38:19 |

7 rows. wq#4451's count reproduces exactly. wq#3274 (`[hw] Seshat zombie: P6 draws 27W but no
LAN/tailnet after power cycle 2026-09-30 19:10 PDT`) carries the verbatim boilerplate the other
victims carry: `Mac fleet retired - no action needed. Closed per Frank brief 2026-10-19
(cleanup-s-19026e); ... [FRANK-DIRECTED: ... close any Mac-related ticket as Mac fleet retired]`.

The false premise is the same one wq#4450 itself refuted to reopen wq#1691 —
`lumen_state current seshat`:

- `seshat.hardware = Seneca PRO925430, sn 2420062, board GIGABYTE MTGU3CS-SD sn LK7U6101947,
  AMI BIOS F2; i3-1115G4 2C/4T` — not MacPro6,1, not any Mac.
- `seshat.remediation_hold = HOLD 2026-09-18: Frank taking seshat offline to work on the SATA
  drive. Do NOT wake/remediate.`
- `seshat.power_state = off — Frank powered down physically (since 2026-10-19)`
- `seshat.role = Nextcloud + Collabora` — the live-service impact wq#3274's own detail names.

Query 2 — **the probe that could have failed** (acceptance-probe doctrine, wq#2630): the fix's
scope parameter is the 20-second `done_at` window, so this one drops the window entirely and
scans every ticket the session touched, in or out of it:

```
select id,status,done_at,claimed_by from tasks
 where notes like '%Mac fleet retired%' or notes like '%cleanup-s-19026e%'
    or claimed_by = 'cleanup-s-19026e'
```

10 rows. Three fall outside the window and are **not** victims, on inspection of their notes:
wq#4422 (06:26:59) and wq#4439 (06:34:57) carry substantive completion evidence — wq#4450's
decision to leave them closed stands; wq#4215 (06:29:19) is a real, fully-evidenced LiteLLM
Cerebras-disable close (config backups, docker restart, `/v1/models` 37-model check); wq#4432 is
a legitimate PARK on hapi-1 bring-up, correctly still `blocked`. The remaining 7 are query 1's
rows. **No further victim exists**, in or out of the cited window. That is the re-check option
(b) asked for, and it closes clean.

## Why (a) and not (b)

- wq#4451's diagnosis of the **method** gap is correct and I adopt it: wq#4450's title cites the
  `done_at` same-second window as how the population was found, but its worklist
  (1691/2842/4178/4415/**4422/4439**) contains two tickets that are not in that window and omits
  one that is — a list that cannot have come from the query it claims. It came from wq#3252's
  dependency neighbourhood, the same path that surfaced wq#1341. wq#4450's own resolution notes
  later concede 4422/4439 fall outside the window, without noticing that this falsifies the
  stated derivation.
- But the **remedy** for a bad derivation is to run the stated predicate once, not to re-dispatch
  the sweep. Queries 1 and 2 are that run, total cost ~2 minutes. Re-implementing wq#4450 would
  re-litigate 4 reopens and 2 non-reopens that wq#4451 already confirmed sound against the
  primary evidence, and the prompt explicitly forbids a wholesale re-implementation.
- Blast radius is bounded: `select id from tasks where deps like '%3274%'` is empty, so the miss
  un-gated nothing, unlike wq#1341 → wq#3252.

## What landed

1. **wq#3274 reopened** — `wq block 3274 "<reason>"`: `status done → blocked`,
   `reopened_count 0 → 1` (`reopened_at`/`blocked_at` `2026-10-20 07:16:52`), `block_reason` and
   `notes` both carry the erroneous-close record plus the `lumen_state` hardware/hold/power
   evidence; then `wq edit 3274 --gate-type frank_decision`. Same treatment wq#4450 gave
   wq#1341/wq#1691: a Frank-gated physical task, held by `remediation_hold`, not a Mac.
   The two pre-existing `[NO-PROBE: physical task: box unreachable, needs hands on console]`
   markers in its detail stand (`wq block` emits the advisory `liveness NO_TAG` warning for any
   Frank-gate without a `Liveness:` line; wq#1341 carries the identical NO-PROBE shape — not
   enforced at block, wq#1998).
2. **ISSUE-LOG row 1492 corrected** in place (status stays RESOLVED — the remediation is now
   actually complete) with the wq#4451/wq#4452 clause: wq#3274 as the 7th victim, the two
   queries above, and the note that wq#4450's list came from wq#3252's dep chain rather than the
   window it cites.
3. **ISSUE-LOG row 1495** (new) for the reusable lesson: a sweep whose ticket names a detection
   predicate must enumerate from that predicate, and a verifier must re-derive the population
   from it rather than audit the author's list.

## Note for future queries on this window

`wq block` does not clear `done_at` (`wq` line 2626-2631 sets `status`/`notes`/`block_reason`/
`blocked_at` only). All 7 rows therefore keep `done_at` in `06:38:01-19` even after reopen, so
query 1 stays reproducible — the discriminator for "still mis-closed" is
`status`/`reopened_count`, never `done_at`.
