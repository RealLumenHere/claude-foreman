# A9 — BUILD: evidence gate on ticket closure

Ticket wq#759. Campaign spec: `/home/lumen/instructions/FLEET_OPS_AUDIT_2026-08-21.md` (F8).
Shipped 2026-08-23 against `/home/lumen/bin/wq` (live tool) and
`/home/lumen/agents/work-queue/queue.db` (read-only for this ticket — see §3).

## 0. Problem (F8, as measured)

29 `done` tickets carried empty `notes`. Nothing in `wq done` required evidence of completion,
so "done" meant "an agent said done" — the same failure class as A1's FALSE_COMPLETE workers,
just discovered after the fact instead of during dispatch.

## 1. What shipped

### 1.1 The gate — `wq done` now requires a checkable artifact

`cmd_done` (in `/home/lumen/bin/wq`) refuses to close a ticket unless `--notes` names something
verifiable, or the caller passes `--no-artifact "<reason>"` on the record:

- **URL** — a PR or commit URL (`.../pulls/N` or `.../commits/N`).
- **SHA** — a 7-40 char hex token with at least one `a-f` letter (so ticket ids and dates
  never false-positive as shas).
- **Path** — a token that looks path-shaped (`/` or a file extension) and actually exists on
  disk, checked as given, relative to the caller's cwd, and relative to `/home/lumen`.
- **`--no-artifact "<reason>"`** — explicit, on the record, for genuinely artifact-less
  closures (a config toggle, a decision recorded elsewhere). Written into `notes` as
  `[NO-ARTIFACT: <reason>]` so `closure-quality` (below) can distinguish it from silence.

Refusal is a hard exit (`sys.exit(2)`), not a warning — no notes at all, or notes with no
artifact-shaped token, both refuse. This matches the ticket's own closing convention already in
use fleet-wide (`wq done <id> --notes "<PR url + one-line summary>"`, e.g. this prompt's own
closing instruction) and the fworker.py auto-close paths, which always pass either a real
`state_path` (written to disk before the call) or a `wq_show` short-circuit — verified in §3.

### 1.2 `wq closure-quality` — the trend, per lane per week

New subcommand, `--lane` and `--weeks` (default 8) filters. Buckets every `done` ticket by
`(lane, ISO week of done_at)` and classifies its `notes` with the exact same `find_artifact()`
the gate enforces, so the report and the gate can never silently disagree. Rows closed before
the gate existed show up as `unverified` — that is the audit's own baseline, not a bug in the
report.

The function (`cmd_closure_quality`) was already written in a prior attempt on this ticket but
never wired into the argument parser — `wq closure-quality` was a silent `argparse` error before
this pass. That wiring (one `sub.add_parser(...)` line) was the actual gap closed today; the
gate logic and the report logic were both already correct.

Current baseline (`wq closure-quality --weeks 6`), for the record:

```
lane       week       done  verified  no-artifact  unverified  pct-verified
build      2026-W28      2         0            0           2            0%
build      2026-W29     55        15            0          40           27%
build      2026-W32     15         1            0          14            7%
build      2026-W33     30        23            0           7           77%
build      2026-W34     47        37            6           4           79%
...
```
(full table: run `wq closure-quality`.) `build` W28→W34: 0%→79% verified — closure discipline
was already improving before the gate; the gate makes the improvement mandatory going forward
instead of optional.

Not wired in this pass: Mission Control's dashboard (`dispatcher/dashboard.py`) reads a
pre-generated JSON snapshot (`queue_depth`, `failure_rate`, `circuit_breaker`, `stuck_tickets`,
`budget`) produced by a separate generator, not live `wq` calls. Adding a `closure_quality` key
to that snapshot is a natural follow-up but is a change to a second live component outside this
ticket's DELIVER list; flagging rather than doing it opportunistically.

## 2. Backfill audit — the 30 note-less `done` tickets

F8 measured 29; the population is 30 as of 2026-08-23 (`select id,lane,done_at,notes from tasks
where status='done' and (notes is null or trim(notes)='')`) — one more ticket (`824`) closed
note-less between the audit (08-21) and this ticket being picked up. Each of the 30 was searched
for a real artifact that exists but was never linked in `notes`: a combined index of every
`reports/*.md` under `/home/lumen` (1040 files across every repo and worktree), plus `git log
--all --grep` for `wq#<id>` against `/home/lumen/foreman` (the gitdir backing every `wq*-a*`
worktree), `/home/lumen/bin`, `/home/lumen/minerva`, `/home/lumen/mission-control`,
`/home/lumen/pstack`. Every hit was opened and confirmed to actually describe the matching
ticket, not a coincidental id collision (e.g. `56` inside `wq560`).

**6 of 30 have real, unlinked evidence — verified here, notes not rewritten (see §3):**

| id | lane | artifact |
|---|---|---|
| 388 | build | `/home/lumen/agents/work-queue/reports/388_inhouse_memory_kit.md` — "wq#388 — In-house memory kit … SHIPPED" |
| 396 | build | `/home/lumen/ab_rag/rerank/eval_result_wq396.txt` + `golden_eval_wq396.py` — rerank golden-set eval results |
| 572 | memory | `/home/lumen/minerva/v4/reports/572_golden_v4.md` — "wq#572 — Golden set v4 + scheduled bench SHIPPED" |
| 580 | fleet | `/home/lumen/bin` commits `2cabece`/`66c1d65` "feat(dispatch): add a fable mode to agent-prompt for any lane" — Refs: wq#580 |
| 591 | memory | `/home/lumen/minerva/v4/reports/591_plug_awareness.md` — "wq#591 — Plug awareness: retention + unprompted findings" |
| 824 | build | `/home/lumen/foreman` commit `d5e6a96` "dispatcher: fail-soft per-lane config validation (wq#824)" |

**24 of 30 are genuinely unverified — no artifact found anywhere searched:**

56, 57, 60, 62, 63, 64, 65, 66, 67, 74, 75, 76, 79, 87, 88, 89, 101, 102, 103, 180, 293, 435,
541, 571. All but three (`79`, `435`, `541`, lane `auto`; `571`, lane `memory`) are from the
`user` lane on 2026-07-10 — the fleet's first day of operation with `wq`, before any
report-writing convention existed. That distribution is itself evidence the gap is historical
and closing (§1.2's `build` trend), not an ongoing pattern.

## 3. Why the DB and the `wq` binary were not touched beyond the gate itself

Two things were deliberately **not** done, both to stay inside this ticket's actual scope and
match established fleet convention:

1. **The 6 verified tickets' `notes` were not backfilled.** There is no `wq` command that edits
   `notes` on a closed ticket (`wq edit` does not expose `--notes`), so doing this would mean a
   raw `UPDATE` against the live production `queue.db` outside the tool that owns it — a
   different and larger risk than this ticket asked for. "Do not retroactively reopen closed
   tickets" reads as bounding this exact class of DB surgery. This report is the durable record
   of the 6 findings instead.
2. **`/home/lumen/bin/wq` was live-patched, backed up, and NOT committed to the `lumen-bin` git
   repo.** Backup taken before any edit: `/home/lumen/bin/wq.bak.20260823_043348Z_pre_wq759`.
   `python3 -m py_compile` clean. This matches wq#741's precedent exactly (same file, same repo,
   landed 2026-08-22): that repo's working tree carries ~78 dirty/untracked files from other
   in-flight fleet work with no per-task isolation, so a commit from this worktree cannot
   distinguish "mine" from "someone else's uncommitted work" without inspecting all of it — out
   of scope here, and the same hazard wq#725/wq#741 already flagged. `wq#741`'s own notes:
   *"wq schema change applied live but not committed to lumen-bin git (that repo's main is
   diverged with unrelated pre-existing drift — same hazard wq#725 flagged; noted in PR)."*

## 4. Verification performed

All four core commands, plus the new gate and report, tested against a throwaway copy of the
production DB (`WQ_DB` env override — `wq` supports this natively, so no test touched the real
queue):

- `wq claim` — unaffected, unchanged code path.
- `wq done <id> --notes "<no artifact>"` → **REFUSED, exit 2**.
- `wq done <id>` (no `--notes` at all) → **REFUSED, exit 2**.
- `wq done <id> --notes "... /home/lumen/instructions/FLEET_OPS_AUDIT_2026-08-21.md"` →
  **closed**, path resolved and confirmed to exist.
- `wq done <id> --no-artifact "purely a config toggle, nothing to verify"` → **closed**, notes
  recorded as `[NO-ARTIFACT: purely a config toggle, nothing to verify]`.
- `wq release` / `wq block <id> "<reason>"` → unaffected; `block_reason`/`blocked_at` (wq#741)
  populate correctly, `wq show` renders normally.
- `wq closure-quality --weeks N` and `--lane <lane>` → renders correctly against real
  production data (read-only query, no mutation).
- `python3 -m py_compile /home/lumen/bin/wq` → clean, before and after.

`fworker.py`'s two auto-close call sites (`harness/fworker.py:1242`, `:1261`) were read, not
just tested: both always pass either a real `state_path` (written to disk immediately before
the call, so `find_artifact`'s path check finds it) or short-circuit via `wq_show(...).get
("status") != "done"`. Neither call site regresses under the new gate.
