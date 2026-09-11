# Ticket lifecycle + closure-quality audit (wq#1668)

Read-only audit, 2026-09-10. Sources: `/home/lumen/bin/wq` (2537 lines), `queue.db`
(read-only), `foreman/harness/{fworker,wq_close,liveness_clear,liveness,liveness_sweep}.py`,
`runtime/liveness_{state.json,audit.jsonl}`, the GitHub and Gitea APIs. Sampler script:
`/home/lumen/scratch/wq1668_classify.py` -> `wq1668_sample.json` (re-runnable, touches nothing).

Trigger: tonight's cleanup found tickets marked done with nothing landed, and Frank-gates
whose premise had cleared days earlier (#1356, #1480, #1518) still sitting open.

## 0. Findings, ranked

| # | finding | evidence | class |
|---|---------|----------|-------|
| F1 | The merge-state gate (wq#899) is DEAD for every github.com PR. `_forge_token()` reads only `$GITHUB_TOKEN`; that env var was stripped from `agent/.env` (wq#1480) and fworker never exported it. github -> `unknown` -> fail-open -> close accepted. | 13 of 19 open LPP PRs (#86-#100, #105, #108) are cited as `done` evidence on 13 tickets; none carry `[UNMERGED-PR]`. `lumen-pr` already reads `.secrets/github-frank.token`; wq's comment "same search order lumen-pr uses" is false. | silent fail-open on a fact the fleet can check |
| F2 | The liveness gate covers only tickets that carry a `Liveness:` line or a hand-written ledger binding. Coverage today: 5 of 79 gated/blocked/user tickets. #1356/#1480/#1518 were never probed (absent from `liveness_state.json`). | `liveness_state.json` = 18 tickets; ledger = 17 bindings; 67 blocked (3 with Liveness), 4 frank_gated (0), 8 user-open (2). | gate with no on-ramp |
| F3 | `wq done` verifies that the *notes* name something that exists, not that the *ticket's* deliverable exists. A worker can cite its own report and close a ticket whose named output was never produced. | `find_artifact()` scans notes only; `detail` is never consulted. | wrong subject under test |
| F4 | Path evidence is resolved relative to the closer's cwd (its worktree) and stored as typed. After the worktree is deleted the citation no longer resolves. | 10/419 sampled closes are unverifiable from `/home/lumen` today; all 10 resolved at close time inside a since-removed worktree. | evidence not durable |
| F5 | Recurring detectors file a fresh ticket per fire. `[repo-sweep] bin: BEHIND_ONLY` was filed and closed 9 times in 9 days; `foreman: BEHIND_ONLY` 6 times. `wq add`'s dup guard only sees open rows. | 21/419 sampled closes are title-duplicates, all `[repo-sweep]`. | alert modelled as a task |
| F6 | `block_reason` can say the gate is met and the row stays blocked. Nothing re-reads block reasons. | #306: "GATE MET (verified live, not assumed)". #124/#940 wait on tickets that are done. | free-text state |
| F7 | Subsystem locks are coarse and mode-blind: a read-only analysis ticket declaring `foreman` cannot be claimed while any other foreman ticket is held. | This ticket: claim REFUSED, `foreman held by work-1667` (another read-only walkthrough). Same class as the #569/#571 `minerva-db` block. | lock granularity |

## 1. How a ticket moves, and what `done` actually checks

States (`tasks.status`): `open -> claimed -> done`, with `blocked`, `frank_gated ->
pending_review -> open` (wq#662 decide/answer/review), `rejected`, `cancelled`, plus
`hold` (soft, not a claim). Counts now: done 1481, open 101, blocked 67, claimed 5,
frank_gated 4, rejected 5, cancelled 1.

`claim` (`cmd_claim`): atomic `BEGIN IMMEDIATE`; refuses if deps unmet, hold held by
another, or any declared subsystem is locked by another holder (`acquire_subs`). Lease
240m, `heartbeat` extends, expiry reaps back to open. `--id` applies every guard, no
`--force` (wq#582).

`done` (`cmd_done` -> `_finish`) checks, in order:

1. **Evidence shape** (wq#759): `find_artifact(notes)` must find a PR/commit URL, a
   7-40 char hex sha with at least one a-f, or a path-shaped token that `os.path.exists()`
   as given, relative to cwd, or relative to `/home/lumen`. Else refuse rc=2, unless
   `--no-artifact "<reason>"` is given (stamped `[NO-ARTIFACT: ...]`).
2. **Merge state** (wq#899), only when the artifact is a `/pull/` URL:
   `pr_merge_state()` asks the forge; `merged` or head-on-main = accept, `open` = refuse
   rc=3 unless `--unmerged "<reason>"`, `unknown` = **warn and accept**. `unknown`
   includes "no forge token".
3. **Owner** (`owner_ok`): caller must hold the claim.
4. **Re-close guard** (wq#1116): a second `done` on a done row is refused rc=4 unless
   `--reclose`.
5. **Fence** (wq#1158): stale-fence writes match no row.

What `done` does NOT check: that any file named in `detail` exists; that a sha is
reachable in any repo (shape only; 1/25 sampled shas resolves nowhere, #1113); that a
cited path is inside a repo rather than a scratch worktree; that the PR the notes cite
is the PR for *this* ticket.

fworker closes through `wq_done()` (`fworker.py:526`) and the orchestrator through
`wq_close.py`, both via the same `wq done`; there is no second write path that skips
the gate. `liveness-close` is the one other path to `done`, restricted to the
`liveness-clear` identity.

## 2. Sample: 419 tickets closed `done` since 2026-09-01

Classifier = the same `find_artifact`/`pr_merge_state` code wq runs, executed from
`/home/lumen` with a working GitHub token so `unknown` cannot hide anything.

| class | n | verdict |
|-------|---|---------|
| PR URL, forge says merged | 176 | real-complete |
| commit sha (shape) | 133 | real-complete as far as wq can tell; 24/25 sampled resolve in a local repo |
| path exists from /home/lumen | 39 | real-complete |
| path resolved only inside a deleted worktree | 10 | real work, evidence now dead (F4) |
| PR URL, forge says **open**, no `[UNMERGED-PR]` tag | **13** | **false-complete** (F1) — all github.com, all LPP |
| PR URL open, `[UNMERGED-PR]` on the record | 6 | closed on purpose, on the record (Gitea; the gate works there) |
| `[NO-ARTIFACT]` liveness auto-clear | 10 | real (gate cleared the premise) |
| `[NO-ARTIFACT]` "already done / verify-pass / config on box" | 21 | plausible; unverifiable by construction |
| title-duplicate of another done row | 21 | duplicate (F5), all `[repo-sweep]` |
| reopened_count > 0 | 13 | rework, tracked correctly (wq#841) |

False-complete rate on the sample: 13/419 = 3.1% overall, but 13/13 = 100% of closes
that cited an open github.com PR. The 13 (#511, #1090, #1176-#1180, #1190-#1192,
#1195-#1196, #1563, #1578) are the LPP roadmap batch closed 2026-09-02 plus two this
week; the work is on branches, not on main. A clean checkout of lumen-plays-pokemon
has none of it. (30-day view: 1055 done, 94 no-artifact, 9 unmerged-tagged, 22
reopened, 11 ex-Frank-gates.)

## 3. Why resolved Frank-gates do not close

The liveness gate (wq#1478) is built correctly and is running (`lumen-liveness-clear.timer`
every 30 min, `--apply --write-bindings`; 10 closes and 17 bindings in its audit log). It
closes a ticket only when it can *name the probe* that proves the premise, and it learns
probes from exactly two places: a `Liveness: <probe>(...)` line in `detail`, or a
`bindings:` entry in the hand-kept `liveness_ledger.yaml`. Neither is populated by the
thing that creates gates. `wq add --frank-gated` runs `creation_check`, which only *warns*
`NO_TAG` when the line is missing. `wq block` accepts free text and writes nothing the
probe can read. So the population the sweep can act on is whoever remembered to write a
tag: 5 of 79 today.

The three named cases had probe-expressible premises and no tag:

- #1356 "mailbot tree dirty" -> `file_contains`/a git-clean probe on `/home/lumen/mailbot`
- #1480 "rotate PAT" -> `cred_alive(/home/lumen/.secrets/github-frank.token, https://api.github.com/user)` (already in the ledger as a credential record, never bound to the ticket)
- #1518 "mint R2 token + bucket" -> `path_exists(.secrets/cf-r2.env)` + `file_contains(mailbot/wrangler.toml, r2_buckets)`

All three were closed by hand on 2026-09-09 during the cleanup; two at the same second.
That is the manual sweep the gate exists to replace.

Secondary: the sweep already iterates `blocked` rows (`OPEN_STATUSES` includes it), but
`wq block` writes the premise as `block_reason` prose (#306 literally says "GATE MET"),
so those rows are invisible to the probes; and `liveness-close`'s only transition is
`-> done`, which is the wrong exit for a block that cleared (the work is still owed).

### Auto-sweep design

Goal: every gate carries a machine-checkable precondition at creation, and the existing
sweep closes it the half-hour it clears. No new daemon; extend what runs.

1. **Make the tag mandatory at the on-ramp.** `wq add --frank-gated`, `wq block`, and
   `wq decide` refuse (not warn) unless the ticket has a `Liveness:` line or the call
   passes `--liveness "<probe>(...)"`, or `--no-probe "<why no deterministic check exists>"`
   on the record (same shape as `--no-artifact`). Physical tasks get `--no-probe physical`
   and stay Frank's. Roll out warn-only for 7 days via `FOREMAN_LIVENESS_CLEAR=warn`, then
   flip.
2. **Derive probes where the text already says them.** Extend `creation_check` to
   propose a tag from patterns it can parse: `blocked on wq#N` -> `ticket_done(N)`;
   a `pull/N` URL -> `pr_merged(owner/repo, N)`; a `.secrets/*.token` path ->
   `cred_alive(path, verify_url)` from the ledger's credential record; an absolute path
   -> `path_exists`. Print the proposal; `--accept-probe` writes it. Backfill: run the
   same deriver over the 79 open gates once (`sweep --write-bindings --derive`),
   report, then apply.
3. **Add the two missing probes**: `git_clean(<repo>)` (covers #1356's class) and
   `wq_field(id, column, value)` (covers "decide-ticket done" chains like #124/#940).
4. **Right exit for a cleared block.** `liveness-close` gains `--to open`: a `blocked`
   row whose tag clears goes `blocked -> open` (claimable, locks untouched, `[liveness]`
   note stamped), not `done`, unless the ticket's own deliverable was the precondition.
5. **Age alarm for tagless gates.** Any frank_gated/blocked row older than 7 days with no
   tag and no `--no-probe` -> one keyed aq row (`liveness`, `untagged-gate-<id>`), so the
   backlog is visible without paging.

## 4. Ranked fix list

| pri | fix | where | size | class it kills |
|-----|-----|-------|------|----------------|
| P0 | **Token lookup parity.** `_forge_token('github.com')` reads `.secrets/github-frank.token` then `github-lumen-classic.token` then `$GITHUB_TOKEN`, same order as `lumen-pr`. Add a self-test: `wq done --dry-run` on a known-open PR must refuse. Then re-adjudicate the 13 LPP closes: reopen (`wq release`) or re-close with `--unmerged` on the record. | `bin/wq:_forge_token` | 20 lines + 13 ticket actions | F1 |
| P0 | **Fail-open must be visible.** When `pr_merge_state` returns `unknown` for "no forge token", stamp `[UNVERIFIED-PR: no token for host]` into notes and open one keyed aq row. Infra trouble may still pass; it may not pass silently. | `bin/wq:cmd_done` | 10 lines | F1 (recurrence) |
| P1 | **Mandatory `Liveness:`/`--no-probe` at the gate on-ramp + probe derivation + backfill of the 79.** (§3 items 1-3) | `bin/wq:cmd_add/cmd_block/cmd_decide`, `harness/liveness_clear.py`, `liveness.py` | 1 ticket, ~200 lines | F2, F6 |
| P1 | **Sweep `blocked` rows; add `git_clean` + `wq_field` probes.** (§3 items 3-4) | `harness/liveness_sweep.py`, `liveness.py` | ~80 lines | F2, F6 |
| P1 | **Canonicalize evidence at close.** `cmd_done` rewrites the accepted path token to the absolute path it resolved to; if that path is under `scratch/worktrees/`, refuse unless the same relative path exists on `origin/main` of that worktree's repo (the deliverable must be *landed*, not just present in a branch). | `bin/wq:cmd_done` | 30 lines | F4, half of F3 |
| P2 | **Deliverable check from `detail`.** At `add`, an optional `--deliverable <path>` (or auto-extract the first `foreman/research/*.md`-style path from detail). At `done`, if set, it must exist on main. Tickets like this one name their output; make the queue hold them to it. | `bin/wq` (new column `deliverable`) | 40 lines + migration | F3 |
| P2 | **Recurring detectors -> aq, not wq.** `repo-sweep`, and any timer that files a titled ticket per fire, writes a keyed aq row (`count++`) and files a wq ticket only via the existing aq escalation rule (count>=3, unresolved). | `bin/lumen-repo-sweep` (or its config) | small | F5 |
| P2 | **Read-only subsystem mode.** `subsystems: foreman:ro` acquires a shared lock: many readers, one writer, writer excluded while readers hold. Analysis/audit archetypes default to `:ro`. | `bin/wq:acquire_subs` + `wq add` | ~60 lines | F7 |
| P3 | **Sha evidence resolves somewhere.** `find_artifact` sha branch: `git cat-file -e` across cwd, `/home/lumen/*`, `projects/*`; unresolvable sha -> treated like a missing path. | `bin/wq` | 15 lines | tail of F3 |

### Landed

wq#1673 (lumen-bin PR#89, `7eaa059`) took the P1 canonicalize+durability fix, the P2
deliverable column and the P3 sha check together. `done` rewrites an accepted path token
to the absolute path `find_artifact` stat()ed; a path that resolves only inside a **linked
worktree** and is absent from `origin/main`/`main` is refused (exit 6, override
`--unlanded "<reason>"` -> `[UNLANDED: ...]`); `wq add --deliverable <path>` binds a ticket
to its named output and `done` refuses (exit 7) unless that file is on main, which
`--no-artifact` does not excuse; a sha citation must resolve via `git cat-file` in the
caller's repo or a checkout under `/home/lumen`. The durability test is "is this a linked
worktree" rather than the `scratch/worktrees/` prefix named above — the same invariant, and
it also covers `/home/lumen/worktrees/`, where the fworker BUILD lane runs and where all 10
dead citations in F4 were written. Fail-open-on-infrastructure is preserved throughout.
Evidence: `bin/tests/test_wq_evidence_durability.py` (11 cases), full bin suite 283 passed.

wq#1674 took the P2 recurring-detectors fix. `harness/repo_sweep.py` no longer calls
`wq add` at all: each non-`IN_SYNC` verdict becomes a keyed aq row
(`source='repo-sweep'`, `key='{repo}:{VERDICT}'`), so the 4-hourly re-fire is `count++`
on one row instead of a fresh title. The sweep resolves that row the moment the repo is
back `IN_SYNC` (and resolves a stale row when the verdict changes), which is what scopes
`count` to one incident and lets a *later* divergence honestly earn a new ticket. A third
rule in `bin/aq_escalate.py` (the wq#965 responder, alongside the probe and dispatcher-stall
rules) is now the only thing that files the ticket, at `count>=3` — ~12h of persistence at
the sweep cadence — with one default-priority notification, not an urgent page. The row
carries the wq title/detail verbatim, so the `repo_hint` read-back the dispatcher's `auto`
lane depends on (wq#1094) survives the extra hop; a round-trip test pins that. The location
guessed above (`bin/lumen-repo-sweep`) does not exist: `lumen-repo-sweep.service` runs
`harness/repo_sweep.py` directly. Evidence: `harness/tests/test_repo_sweep.py` 18 passed,
`bin/tests/test_aq_escalate.py` all checks passed. The audit's other F5 candidates were
checked and left alone: `ops/wq1252-lumen-public-guardian/guardian.py` varies its title per
observation and gates on its own heal ladder, and all 21 sampled duplicates were
`[repo-sweep]`.

## 5. What the sample does not say

- "Real-complete" here means the queue's own evidence checks out from a clean vantage.
  It does not prove the merged PR did what the ticket asked; that is the acceptance
  gate's job (wq#1260 series), out of scope.
- 21 `[NO-ARTIFACT]` "verify-pass / config-on-box" closes are unverifiable by design.
  The fix is not a stricter gate; it is P2's deliverable column so a verify ticket names
  the verdict file it must write.
- No ticket was found closed by a caller that did not hold it (wq#1116 fix holds).
