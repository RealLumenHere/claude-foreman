# harness — T2a worker harness

wq#625, BUILD_PLAN.md §T2a. Implements
[design/T2A_WORKER_HARNESS_DESIGN.md](../design/T2A_WORKER_HARNESS_DESIGN.md), Frank-approved
via PR#2. One `fworker` supervisor process per ticket, spawning one `claude -p` child per
attempt. wq#616 (T2b) lifted the original one-at-a-time constraint: each cloud worker now
carries the durable setup-token as a static bearer (no shared-login refresh race), and
`spawner.py` fans multiple tickets out concurrency-capped + staggered — see "Concurrency"
below.

## Layout

    fworker.py              supervisor: spawn + parse + classify + wq lifecycle
    classifier.py           RELIABILITY_SPEC.md §C/§D verdict engine (SUCCESS/TURNS/
                             LAUNCH_FAILURE/LIMIT/KILLED/OOM/INDETERMINATE/AUTH/
                             DEGRADED_SUCCESS/FALSE_COMPLETE + REFUSED/BUDGET/CRASH/HANG)
    oom_forensics.py         journal-based OOM corroboration (§E, wq#648) — best-effort,
                             attached to attempt-N.json on an OOM verdict, never a
                             classification input itself
    reaper.py                independent wq lease reaper (§E, wq#647) — `wq reap` +
                             an aq row per reap, decoupled from any worker's own exit path
    repo_reconcile.py        wq#686: managed-repo divergence check/repair — resets a local
                             branch onto a PR-only remote ONLY when the trees are already
                             identical (soft, archived), refuses otherwise
    acceptance.py            ISSUE-SOLUTIONS-fable.md §1 (#17): acceptance derivation +
                             pinning, evidence replay, rework.json — see "Evidence-gated
                             done" below
    hooks/stop_gate.py       §1c Stop hook: refuses the session's stop while the pinned
                             criteria are unevidenced (capped at 3 blocks, fails open)
    concurrency.py           wq#616 (T2b): live fworker-wq* unit count + cloud slot cap
    spawner.py               wq#616 (T2b): concurrency-capped, staggered batch dispatch
    worker-settings.json    settings pack fworker.py loads (SETTINGS_FILE, override with
                             FOREMAN_SETTINGS_FILE) — replaces worker-settings-interim.json.
                             NOT ../worker-settings-general.json: that is the separate
                             blanket-`Bash` pack orchestrator-spawned fleet-triage workers
                             use, and reading it instead of this one is how wq#1107 got
                             misdiagnosed as "bare Bash is allowed, why the denials?".
                             This pack's allowlist is deliberately per-command; see
                             "Permission refusals" below.
    permission_profiles.py   wq#1258 (§2b): renders a repo's toolchain permission profile
                             at dispatch — see "Per-repo toolchain permission profiles"
                             below.
    permission-profiles/    base.json/python.json/node-ts.json/shell.json (shared allow/
                             deny fragments) + repos.d/<repo>.json (which fragments a repo
                             gets).
    guard.sh                PreToolUse fail-closed guard (§5)
    fire_gate.py             wq#717 (AUTO-5): fleet-fires manual-approval safety core
                             (FIRE_MODE.md) — plan/approval file IO, drift/timeout checks
    systemd/wq-worker@.service  wq#1182: one-worker-per-node-slot template, flock-guarded
                             (WORKER_SLOT.md)
    wq_worker_slot.sh        wq#1182: ExecStart body for wq-worker@.service
    prompts/worker.md       worker prompt template
    prompts/fire-diagnosis.md, prompts/fire-apply.md   fire-mode phase prompts (FIRE_MODE.md)
    tests/                  pytest suite (zero Anthropic quota)

Runtime paths (governor-owned or created on demand, not checked in):
`../events/<worker-id>.jsonl` (lifecycle + blocked events), `../_runs/wq<ID>/`
(attempt-N.json, prior-attempt-notes.md), `/home/lumen/worktrees/wq<ID>-a<N>/`
(per-attempt git worktrees), `../state.json` / `../STOP` (T1 governor).

## Running a ticket

    python3 harness/fworker.py run --ticket <ID> --repo <path> \
      --model claude-sonnet-5 --effort high --archetype standard-engineering

Launch via `lumen-run` so the supervisor itself survives DC-gateway reaping. RELIABILITY_SPEC.md
§E: `--timeout-stop-sec 90` (>=60) so a unit stop/recycle SIGTERMs fworker with enough grace
for its own SIGTERM->SIGKILL child-kill escalation and the CLI's SessionEnd hooks to finish
cleanly instead of racing a hard systemd SIGKILL — `router.py dispatch-cmd` already sets this:

    lumen-run start --name fworker-wq<ID> --timeout-stop-sec 90 -- \
      python3 /home/lumen/foreman/harness/fworker.py run --ticket <ID> --repo <path> \
        --model <id> --effort <name> --archetype <name>

`--model/--effort/--archetype` may be omitted (wq#617): fworker then reads the tuple the
orchestrator wrote onto the ticket (`wq` model/effort/archetype columns) or asks
`../orchestrator/router.py assign` on the spot; explicit flags always win. On SUCCESS
reconcile it calls `../orchestrator/judgment.py verify-pass --for <ID>` (best-effort;
`FOREMAN_NO_VERIFY_PASS=1` disables).

Exit codes: `0` success, `10` claim error, `11` deferred (governor/STOP gate), `12`
blocked (2 failed attempts, ticket wq-blocked), `13` limit (ticket released, resets
outside the resume window). `16`/`17`/`18` are fire-mode-only (timeout release /
diagnosis failed / apply incomplete) — see "Fire-mode" below.

## Evidence-gated done (ISSUE-SOLUTIONS-fable.md §1, issue #17)

"done" is not a speech act by the model: the model proposes, the harness replays, and
only the harness transitions the row.

1. **§1a pin at dispatch.** `do_attempt()` derives an acceptance checklist from the ticket
   (one cheap model call; a deterministic heuristic covers every failure so derivation can
   never block a dispatch) and writes `acceptance.json` twice — the truth into
   `_runs/wq<id>/`, a read-only copy into the worktree (deny-written in
   worker-settings.json, hash-checked at done time). Attempt 2+ reuses attempt 1's pin.
2. **§1b replay.** The worker's only path to done is `evidence.json` in its worktree.
   `evidence_gate()` re-runs every `proof_type: test` criterion **itself** (merged
   stdout+stderr into `_runs/wq<id>/evidence/<AC>.replay.log`) and resolves every
   `citation` against the branch's diff. The model's logged exit code is a hint, never
   proof. A rejected claim sets the row to `rework` — including reverting a `done` the
   worker wrote itself — and classifies the attempt FALSE_COMPLETE.
3. **§1c Stop hook.** `hooks/stop_gate.py` blocks the session's stop while the evidence is
   missing or structurally incomplete, printing the specific gap. Capped at 3 blocks
   (`FOREMAN_STOP_GATE_MAX_BLOCKS`), then the stop is allowed and §1b routes to rework, so
   a confused worker cannot loop inside the hook.
4. **§1d rework.** A rejection writes `_runs/wq<id>/rework.json` (per-criterion
   expected/observed/repro/output_tail); the next attempt's prompt opens with it under
   three rules — run each repro first, do not re-assert rejected claims, the same pinned
   checklist still governs. `MAX_ATTEMPTS` exhausted escalates to Frank (`wq block` +
   ntfy) rather than dispatching again.

Switches: `FOREMAN_EVIDENCE_GATE=off` disables §1 for a dispatch (rollback, and what the
pre-§1 fixtures in test_fworker_integration.py run under);
`FOREMAN_ACCEPTANCE_DERIVE=heuristic` skips the derivation model call;
`FOREMAN_ACCEPTANCE_MODEL` picks the derivation model; `FOREMAN_REPLAY_TIMEOUT_S` bounds
each replayed command. Coverage: `tests/test_evidence_gate.py`.

## Fire-mode: fleet-fires manual-approval workflow (wq#717, AUTO-5)

`--archetype fire-response` replaces the normal retry-ladder attempt with a dedicated
diagnose → hold → apply state machine for `[fleet-fires]` lane tickets (production
state changes: service restarts, config edits, secret rotations, repo unlocks) that
must never auto-apply without Frank reviewing the SPECIFIC proposed change first — see
[FIRE_MODE.md](FIRE_MODE.md) for the full workflow, approval file schema, and
documented failure modes. Core safety logic lives in `fire_gate.py` (unit-tested in
`tests/test_fire_gate.py`, zero quota, no subprocess).

`--frame-gap-timeout SEC` (§E: T_gap, default/env `FOREMAN_FRAME_GAP_S`, 120-300s range)
overrides the primary stall watchdog; `--post-result-grace SEC` (env
`FOREMAN_POST_RESULT_GRACE_S`) is the much shorter grace after a terminal result frame
before fworker force-kills a stuck-but-already-succeeded child (§C9). Both exist mainly
so tests can run them fast — production defaults are fine left alone.

## Lease reaper (wq#647, §E)

wq's own lease-expiry reclaim only runs lazily, inside a `wq claim` call for the SAME
lane — a dead fworker (host died, OOM, DC-gateway reap of something that wasn't under
lumen-run) leaves its claim dangling until someone happens to claim that lane again,
which can be hours (fq#614: three same-day drain-deaths this way). `reaper.py` is an
independent process that sweeps ALL lanes on its own schedule:

    python3 harness/reaper.py sweep            # one pass, all lanes
    lumen-run start --name foreman-reaper -- \
      python3 /home/lumen/foreman/harness/reaper.py loop --interval 120

Every reclaim posts an `aq` row (source `foreman-reaper`) so a dangling claim is never
silent again, then reverts the ticket to `open` via `wq reap` (a new lane-independent
subcommand, same reclaim semantics `wq claim` already uses for its own lane).

## Managed-repo divergence (wq#686)

Committing straight onto the local `main` of a deployed repo is unrecoverable-by-push once
the same work lands on the remote as a squashed PR commit: `git push` is refused by the
class-a-main ruleset (PR + squash + linear history) and `git pull` would merge, which the
ruleset also forbids. wq#686 caught `/home/lumen/pokemon` in exactly that state — main 7
ahead / 1 behind, for three days.

The escape hatch is a fact, not a judgement call. When the squashed remote commit already
carries the same content, `tree(main) == tree(origin/main)`, and moving the local ref
changes **zero files** — so it cannot disturb a process running out of that tree. That
equality is the deploy-parity proof.

It stops being available the moment the remote lands anything else, which is wq#975:
`/home/lumen/bin` sat 1 ahead / 4 behind on a duplicate of an already-landed one-line fix,
whole-tree equality was unreachable, and landed PRs stopped reaching the deployed path. So
there is a second, narrower proof — for every path the ahead commits touched since the
merge-base, `blob(main:path) == blob(origin/main:path)`. Paths only the remote changed are
not the local's problem. Rewinding to the merge-base still changes zero files, and leaves
the repo BEHIND_ONLY: fast-forwardable again.

    python3 harness/repo_reconcile.py status                       # default repo: /home/lumen/pokemon
    python3 harness/repo_reconcile.py reconcile --apply            # SAFE_RESET or REDUNDANT_AHEAD

Exit codes are the interface: `0` IN_SYNC, `10` SAFE_RESET (fixable here), `11`
REDUNDANT_AHEAD (ahead *and* behind, but the ahead commits duplicate landed content —
fixable here, lands on BEHIND_ONLY), `20` CONTENT_DIVERGED (needs branch -> PR -> squash,
or a Frank bypass), `30` BEHIND_ONLY (fast-forward to deploy), `2` error. `--apply`
soft-resets (never `--hard`, so uncommitted and untracked work in a live tree survives)
and archives the abandoned SHAs under an `archive/reconcile-*` tag first.

wq#686's own reconcile is preserved in `/home/lumen/pokemon` as tag
`archive/wq686-pre-reconcile-main` (df2d937, the 7 commits) — kept because a squash makes
the pre-squash SHAs unreachable, and unreachable is one `git gc` away from gone.

## Governor cache-on-429, NDJSON hardening, OOM forensics (wq#648, §B/§E, S3)

- **Governor**: `governor/foreman_usaged.py` already cached last-good `limits[]` and
  only fails closed on elapsed time (`cache_age_s`, new field on `state.json`) past
  `stale_fail_closed_seconds` — never on one bad poll in isolation, 429 included (§B:
  `/api/oauth/usage` is undocumented and 429s aggressively, #31021/#31637). A failed
  poll now also stamps `reused_cache`/`reused_cache_reason` on the state so it's
  visible which reads are live vs. reused. Fable/weekly bucket separation
  (`session`/`weekly_all`/`fable_weekly_scoped`) was audited against §B and is correct
  as built (T1) — `governor/test_governor_core.py`'s bucket tests already cover it.
- **NDJSON reader**: `fworker._reader_thread` already did byte-buffer + newline-split +
  partial-trailing-line-carry correctly (Python's `TextIOWrapper.readline()` never
  returns a line until it sees `\n` or EOF, regardless of how the child chunks its
  writes) and unknown frame types already fall through `RunState.observe()` untouched.
  Shim 14-16 (`tests/test_shim_matrix.py`) lock this in: a buffered-pipe burst under
  T_gap is NOT-A-FAILURE (#25670), a byte-split NDJSON line reassembles correctly, and
  an unrecognized frame type never crashes the parser. All 16/16 §F shim items pass.
- **systemd**: `orchestrator/router.py:dispatch_cmd()` now passes `--mem`/`--mem-high`/
  `--cpu`/`--tasks-max`/`--oom-policy stop` to `lumen-run` for every `fworker-wq<ID>`
  unit (lumen-run has supported these flags since wq#613; they just weren't wired in).
- **OOM forensics**: `oom_forensics.py` best-effort-corroborates an OOM verdict (exit
  137) against the persistent systemd journal (`journalctl -k`), attached to
  `attempt-N.json` as `oom_journal` — never a classification input, same
  corroboration-only status as `cpu_s`.

## Local rung (wq#618, T4, BUILD_PLAN.md §T4)

Trusted-route dispatch to the LiteLLM gateway (`deepseek-v4-flash`, `qwen36-local`;
`nephthys-qwen3-14b` is fallback-brain only, never a wq worker) — zero Anthropic quota,
and **governor-exempt by design**: `governor/governor_core.py:is_local_model()` skips
session/weekly floor checks and live rate_limit_event blocks for these two model ids
(STOP is the one gate it still shares with cloud dispatch).

`fworker.is_local_model(model)` drives everything else:
- `build_env()` sets `ANTHROPIC_BASE_URL=http://localhost:4000` +
  `ANTHROPIC_AUTH_TOKEN=<scoped foreman key>` +
  `CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY=1` **only** for a local-pool model's own
  child env — never merged back into fworker's process env, so it can't leak into a
  later cloud dispatch on the same host. Cloud dispatch unconditionally pops all four
  gateway vars first, even if inherited. The key lives at
  `/home/lumen/.secrets/foreman-litellm.token` (0600, same convention as the other
  `*.token` files there) — a LiteLLM virtual key scoped to `models: [deepseek-v4-flash,
  qwen36-local]` with a `max_budget`, minted separately from and never the LiteLLM
  master key.
- `local_rung_gate()` restricts local-pool dispatch to `mechanical-edit` /
  `probe-build` — the only two archetypes models.yaml's routing table ever carries an
  `@local` rung for. Checked pre-claim in `cmd_run()` (same DEFERRED contract as
  `deps_unmet`/the governor gate — never touches the claim) and again per-attempt in
  `do_attempt()`. Defense-in-depth: the router itself never produces a mismatched
  (model, archetype) pair; this only matters against an explicit `--model`/`--archetype`
  override.
- `LOCAL_ARCHETYPE_MAX_TURNS` caps local dispatch below `ARCHETYPE_MAX_TURNS`'s cloud
  defaults for the same two archetypes (FOREMAN_RESEARCH §9: "cap local workers'
  --max-turns lower, they loop more").
- The mandatory Sonnet-5 verify-pass gate before land is already T3's job
  (`orchestrator/judgment.py:verify_required()`, `models.yaml verify_pass.
  local_author_always`) — `file_verify_pass()` fires it on every SUCCESS reconcile
  regardless of author model, so a local-rung ticket always gets a fresh sonnet-5@high
  verify-pass ticket filed against it. No direct-to-main path exists for any rung; every
  worker still lands via `lumen-pr` (branch → PR → probe → board).

`tests/test_local_rung.py` covers all of the above at zero quota, including a full
`fake_claude` dispatch on `deepseek-v4-flash` that asserts the CHILD process actually
received the gateway env (not just `build_env()`'s return value) and that a `[verify]
wq#<id>` ticket lands authored `claude-sonnet-5@high`.

## Concurrency (wq#616, T2b)

FOREMAN_RESEARCH.md §8: concurrent `claude -p` processes sharing one login race to
refresh `~/.claude/.credentials.json` at access-token expiry (~8h); the server rotates
the refresh token, so every session but the winner 401s ("auth cascade", 3 GH issues).
T2b closes this by construction rather than by careful scheduling:

- **Static bearer.** `fworker.build_env()` reads the durable setup-token (wq#615;
  path only — `FOREMAN_OAUTH_TOKEN_FILE`, default `/home/lumen/.secrets/claude-oat-worker.token`,
  0600) fresh on every attempt and sets `CLAUDE_CODE_OAUTH_TOKEN` in the child's env,
  scrubbing `ANTHROPIC_API_KEY`/`ANTHROPIC_BASE_URL`/`ANTHROPIC_AUTH_TOKEN` same as
  before. No refresh cycle, no shared-file contention — each worker's auth is
  independent. A missing/unreadable token file logs a WARN and falls back to the
  shared login (old exposure) rather than failing the dispatch; `attempt-N.json`
  records `oauth_token_used` so a run's auth path is visible without ever touching the
  token value itself. The token is treated as a secret throughout: `guard.sh` and
  `worker-settings.json` both deny Read/Bash access to `/home/lumen/.secrets/*`, so a
  worker's own tools can't exfiltrate it.
- **Concurrency cap.** `concurrency.py` counts live `fworker-wq*` `lumen-run` units
  (not wq claims — a claim can outlive a dead process; that staleness is reaper.py's
  job, wq#647, on its own schedule). §8's doctrine: start 2, hard ceiling 3 cloud
  workers (local rung is T4, uncounted here, bounded by Lumen CPU instead).
- **Staggered batch dispatch.** `spawner.py dispatch --repo PATH --ticket ID [--ticket
  ID ...]` launches a batch of ready tickets through `router.dispatch_cmd`, waiting for
  a free concurrency slot before each spawn (never exceeds `--ceiling`, default 3) and
  sleeping a randomized 2-3s between spawns (§8 point 4: defensive against shared
  `.claude.json`/history writes at worker startup). Re-checks the T1 governor
  immediately before each spawn so a denied ticket never spins up a systemd unit that
  fworker's own gate would just DEFER anyway — that per-ticket gate still runs inside
  fworker regardless; this is the layer above deciding launch order/pacing, not a
  replacement for it. A skipped ticket (governor deny / no free slot within
  `--slot-wait-max`) stays open in wq for the next pass, not treated as a hard error.

## Build-time discovery vs the design (read before touching --max-turns)

wq#616: the design doc's §3 note ("T2a runs ... ONE cloud worker at a time, fworker
refuses to start if another fworker-wq* lumen-run unit is RUNNING") describes a
constraint that was never actually implemented in `fworker.py` — there was no such
refusal check. T2b replaces that placeholder with the real mechanism (`concurrency.py`
+ `spawner.py` above) instead of retrofitting the placeholder itself.

CORRECTED (wq#646, RELIABILITY_SPEC.md §A): claude CLI 2.1.233 did **not** drop
`--max-turns` — that earlier belief, from a probe against 2.1.220, was wrong.
`fworker.py` passes `--max-turns`; the CLI errors on ceiling with a real
`result:error_max_turns` frame (PROBE_RESULTS.md live-confirmed), which is the
evidence RELIABILITY_SPEC.md §C2 requires for a TURNS verdict ("never legal without a
result frame"). The supervisor also counts `assistant` stream-json frames itself
(`classifier.RunState.turns`) and SIGTERMs the child on a backstop ceiling a few turns
above `--max-turns` (`fworker.TURN_BACKSTOP_BUFFER`) — defense-in-depth only; if that
backstop has to fire, no result frame arrives and the attempt classifies KILLED, not
TURNS. See the module docstrings in `fworker.py` and `classifier.py`.

`claude agents --json` (confirmed working, no TTY needed) is used only as an advisory
liveness signal during `--resume` reconciliation (design §8) — never the control channel.

## Per-repo toolchain permission profiles (wq#1258, ISSUE-SOLUTIONS-fable.md §2b)

`worker-settings.json`'s allowlist used to be one flat list serving every dispatched
repo — pokemon/mailbot (TS), foreman (python), etc. — regardless of toolchain, which is
exactly how PERMISSION_LOOP happened twice (ISSUE-LOG.md #14): a python-shaped allowlist
missing `npm`/`npx`/pipe targets on a TS repo. `permission_profiles.py` replaces the
single static list with a union of shared toolchain fragments, chosen per repo:

    permission-profiles/base.json        inspection verbs, git, lumen_busy, wq/lumen-pr CLIs
    permission-profiles/python.json      python3, pytest, pip, uv, ruff, sqlite3
    permission-profiles/node-ts.json     node, npm, npx, pnpm, tsc, vitest, jest
    permission-profiles/shell.json       bash scripts/*, shellcheck
    permission-profiles/repos.d/<repo>.json   {"profiles": [...], "extra_allow": [...], "verbs": [...]}

`fworker.resolve_worker_settings(repo, ticket_id)` (called from both `do_attempt()` and
`run_fire_child()` in place of the old bare `SETTINGS_FILE` constant) renders
`union(profiles) + extra_allow + Bash(<repo>/<verb>)` into a content-addressed file under
the ticket's `_runs/wq<ID>/` dir — hooks and `defaultMode` pass through from
`worker-settings.json` unchanged, only `permissions.allow`/`deny` are replaced. A repo
with no `repos.d/<repo>.json` entry yet keeps getting the static `worker-settings.json`
verbatim (`digest is None`), so migrating repos in is additive, never a big-bang cutover.
An allowlist gap discovered in one node repo is now fixed once in `node-ts.json` and
every node repo picks it up on its next dispatch — closed by profile, not by discovery.

Currently declared: `foreman` (python+shell), `pokemon` (node-ts+shell), `mailbot`
(node-ts+python). `harness-preflight` (ISSUE-SOLUTIONS-fable.md §2c, not built yet) is
the intended dispatch-time gate that proves a repo's rendered profile has no gaps before
a wave runs against it, and the place a render-hash-on-the-wq-row stamp belongs; §2b
only renders and logs the hash (`resolve_worker_settings` logs
`rendered permission profile <hash> -> <path>`), it doesn't gate dispatch on it.

## Permission refusals and PERMISSION_LOOP (wq#1107)

Two gates sit in front of a worker's Bash calls, and neither is the guard.sh deny-list:

1. `worker-settings.json`'s `permissions.allow` is a **per-command allowlist**
   (`Bash(git *)`, `Bash(ps *)`, …), not a blanket `Bash` grant. Anything not listed gets
   Claude Code's `This command requires approval`. Every segment of a `|`/`&&`/`;` chain
   is matched separately, so one unlisted segment fails the whole line with the compound
   variant `...the following parts require approval: <cmd>`.
2. The session's directory bound (worktree + every `--add-dir`) refuses reads outside it
   with `was blocked. For security, Claude Code may only ...`, whatever the allowlist says.

Headless has no one to approve, so both come back as tool errors. A worker that retries
one three times trips action_gate's loop breaker, and before this ticket the SIGTERM that
followed made it exit 143 -> `KILLED` -> `INFRA_CRASH`: a settings gap charged to the
model, requeued into the identical wall, and counted by the dispatcher's circuit breaker
(2026-08-29: 8/9 attempts, breaker tripped, `DISPATCH_DISARM` set).

Now `LoopTracker` records `trip_kind = "PERMISSION"` when the repeated error matches
`action_gate.PERMISSION_DENIAL_RE`, `fworker.py` overrides the verdict to
`classifier.PERMISSION_LOOP` (keeping `KILLED` in `raw_classification`), and
`classify_block_reason()` buckets it as `HARNESS_DEFECT` — outside
`circuit_breaker.CATASTROPHIC_CLASSES`, so a config regression can no longer spend the
fleet's dead-man's switch, and outside self_heal's release path, because releasing a
ticket blocked on our own config just re-runs it into the same refusal.

**When a `HARNESS_DEFECT` block appears: widen the allowlist (or the `--add-dir` set),
land that, then `wq release` the ticket.** The exact refused command is in
`../_runs/wq<ID>/loop-handoff-attempt-<N>.md`.

## Tests

    cd harness && python3 -m pytest tests/ -v

377 cases, including: guard.sh corpus (every deny class + quoting/`bash -c` evasions, lifecycle
event logging, fail-closed on malformed/empty/off-host input), the classifier's full
decision table including the turn-ceiling case, settings-pack lint (acceptEdits, no
bypassPermissions anywhere in the harness or in the retrofitted gh-lane-worker,
docker ps/logs allow vs docker-star deny, credential-path denies), per-repo permission
profile rendering (`test_permission_profiles.py`: union/dedup, unconfigured-repo
fallback, verbs/extra_allow, hash determinism), the STOP/can-dispatch governor gate,
and four end-to-end fworker runs against a fake `claude` shim
(`tests/fake_claude.py`) covering success + worktree isolation, a denied verb actually
blocked at the hook during a real spawn, a synthetic rejected rate-limit frame driving
the real LIMIT path (release, not block), and resume-after-LIMIT reusing the worktree.

`probe_wq625.py` (`/home/lumen/bin/probe_wq625.py`) wraps this suite plus the file-layout
and gh-lane-worker-retrofit checks into the ticket's probe gate; it's the thing CI/a
human runs, not the pytest invocation directly.

## What's deliberately not exercised here (quota-costing, follow-up)

Design §12 item 5/6: one real sonnet-5@low (or haiku) ticket through fworker end to end,
and one gh-lane-worker smoke on a trivial live github-lane ticket. Both cost live Max-plan
quota; this build's probe gate is zero-quota by design (matches the ticket's two named
adversarial criteria — denied-verb-blocked-at-hook, LIMIT!=CRASH — plus the full harness
suite). Schedule the quota-costing smoke off-peak when convenient; nothing in the design
depends on it to land T2a.

wq#616 (T2b) same pattern: its "3 concurrent real tickets, ideally across a token-expiry
boundary → no auth cascade" verify line is quota-costing and needs a multi-hour window
to actually cross an expiry boundary, so it's a follow-up smoke rather than a blocking
gate here — this build's own probe gate is the zero-quota concurrency.py/spawner.py/
token-injection suite (tests/test_concurrency.py, tests/test_spawner.py,
tests/test_token_injection.py) plus the fworker/guard/settings regression suite, all
passing. wq#615 already live-verified the token itself works via
`CLAUDE_CODE_OAUTH_TOKEN` on a real one-shot; what's untested live is `fworker.py`
actually wiring it into a real spawn under concurrency. Schedule 3 real tickets through
`spawner.py dispatch` off-peak (ideally spanning >8h to cross the access-token expiry
window) once there's a live batch to dispatch.

## Known gaps vs the full design (explicit, not silent)

- Restart reconciliation (§8) trusts wq status + `_runs/` + an advisory `claude agents
  --json` liveness check; it does not yet re-attach WATCH-ONLY to a live orphaned child
  process by pid/cmdline. A reaped fworker's lease simply expires and the ticket returns
  to the queue loudly (existing wq machinery), which is safe but not the fully-optimized
  path the design describes.
- OTEL env vars are wired into every child's env (§10) but not independently probed here
  beyond the confirmed-live ingest URL already in the design doc; verifying a metric
  actually lands on Bes VictoriaMetrics is part of the quota-costing smoke above.
