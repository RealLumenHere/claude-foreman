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
    evals/worker-quality/   wq#1300: promptfoo suite scoring the done-claim critic's
                             false-complete rate + real rework-convergence stats — see
                             its own README.md

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

Tickets routed to an opus/sonnet/haiku model can run on the `sdk` runtime
(`harness/fworker_sdk.py`, which spends the plan's monthly API credit) while the credit
lasts; otherwise they fall back to the `claude` CLI. See
[SDK runtime: spending the plan's monthly API credit (wq#5211)](#sdk-runtime-spending-the-plans-monthly-api-credit-wq5211)
below.

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

**Dispatch rebuild step 4 (wq#2128), `charging.py`.** The frame-gap window is split:
`FOREMAN_FRAME_GAP_S` (240) now applies only after the first model frame (system/init does
not count); before it the first-frame budget rules — cloud 240, local `FOREMAN_FIRST_FRAME_S`
(default 1500, passed per backend by the dispatcher). `--frame-gap-timeout` still means one
single window. Every `attempt-N.json` carries `charged_to` (`ticket|rung|harness|budget`) and
`failure_cause` (rule table: `research/DISPATCH_FIX_RESEARCH.md` 2.2). A `rung` charge (local
HANG with no turns, LiteLLM 5xx/timeout/cooldown, backend unreachable, ctx overflow) releases
the ticket to `open`, is not counted toward `MAX_ATTEMPTS`, and appends the backend
(`FOREMAN_BACKEND`, else the model id) to `dispatcher/runtime/backend_cooldown.json` — 15 min,
doubling to 2 h; if the ticket still ends blocked its reason starts `RUNG_FAULT:`. Local-rung
`mechanical-edit`/`probe-build` tickets use the slim `prompts/local_<archetype>.md` (static
prefix first) and are refused as a harness fault (uncharged) when the rendered prompt exceeds
0.6 x `FOREMAN_SERVED_CTX` (default 32768) at chars/3.5 tokens. Rollback lever:
`FOREMAN_CHARGE_MODE=legacy` turns all of it off.

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
(node-ts+python).

### Where to add an allow rule (wq#1376 — read this before editing an allowlist)

**Profiles are authoritative, not additive.** For a repo with a `repos.d/<repo>.json`
entry, `permission_profiles.py:84` *replaces* `worker-settings.json`'s allow list with
`union(declared profiles) + extra_allow + verbs`. It does **not** union
`worker-settings.json` in — doing so would re-import the flat allowlist that profiles
exist to replace, and every repo's pack would be identical again (ISSUE-LOG #14).

So:

| the rule should reach | put it in |
| --- | --- |
| every dispatched repo | `permission-profiles/base.json` |
| one toolchain | `permission-profiles/{python,node-ts,shell}.json` |
| one repo | `repos.d/<repo>.json` `extra_allow` / `verbs` |
| only repos with **no** `repos.d/` entry | `worker-settings.json` |

A rule added only to `worker-settings.json` is **inert** for `pokemon`, `mailbot` and
`foreman`. That is what burned wq#1323 and wq#1347: `Bash(systemctl restart
lumen-messenger)` landed at `worker-settings.json:57` and three successive workers still
got `This command requires approval`. `test_permission_profiles.py::
test_worker_settings_allow_rules_all_exist_in_some_profile` now fails when a rule lands
in the dead file.

## Evidence-gated done (wq#1289, ISSUE-SOLUTIONS-fable.md §1, ISSUE-LOG #17)

`done` is a state transition the harness performs after it has observed the evidence
itself, not a claim the model makes. `acceptance.py` holds the logic; `fworker.py` and
`hooks/stop_gate.py` are the two wiring points.

    dispatch     pin_acceptance()  derives criteria from the ticket and writes
                 acceptance.json into the worktree, pinning doc+hash in _runs/wq<ID>/
                 (which the worker's settings do not reach). The prompt gains the
                 done-contract; settings deny Write/Edit on acceptance.json.
    during       hooks/stop_gate.py (Stop hook) refuses to let the session end while
                 evidence.json is missing an entry for a pinned criterion. Capped at 3
                 blocks per session — it is a nudge, not a wall, and fails OPEN.
    after exit   verify_done() re-runs every `proof_type: test` criterion itself and
                 re-checks every `citation` against `git diff`. A claimed exit code is a
                 hint; the harness's own observation decides.
    on failure   rework.json (failed criteria, repro commands, the harness's own replay
                 output tail) lands in the run dir, the row is reverted out of `done` and
                 re-claimed, and the next attempt renders the rework block at the TOP of
                 the prompt: re-run the repros first, do not re-assert the rejected claim.
                 At `attempt >= FOREMAN_EVIDENCE_MAX_ATTEMPTS` (3) it escalates to Frank
                 (`wq block`) instead of dispatching again.

Criteria are derived deterministically from the ticket text (commands the ticket names
become `test` criteria; a code ticket always gets one "cite file:line inside the diff"
criterion). Set `FOREMAN_ACCEPTANCE_MODEL=<model>` to add the spec's cheap model
derivation pass in front of it — its output is validated and any criterion the harness
cannot replay is dropped, so a hallucinated command costs a criterion, not a stuck gate.
Replay is argv-only against an allowlist of test-runner binaries (`acceptance.replayable`)
— ticket text is not a trusted script. `FOREMAN_EVIDENCE_GATE=0` disables the whole
mechanism without a code change.

Self-check, same replay, no state transition:

    python3 harness/fworker.py verify-evidence --ticket <id> --worktree <path>

Tickets whose deliverable is a document rather than a diff (`verify-pass`,
`rca-investigation`, `fire-response`, research) derive no criteria and stay ungated.

## harness-preflight + dispatch stamp gate (wq#1259, ISSUE-SOLUTIONS-fable.md §2c)

Two layers, both in `harness_preflight.py`, driven by `./harness-preflight <repo>`
(or `--all`):

1. `static_lint()` — offline, zero quota. Each repo's `preflight_cmds`
   (`repos.d/<repo>.json`) plus every `verbs` entry plus one repo-agnostic worst-case
   compound (`INSPECTION_PIPE_PROBE`, proving the base profile's pipe targets —
   `tail`/`grep`/`wc` — are covered) get split on `&&`/`||`/`;`/`|`, the same way
   Claude Code's permission layer splits a compound command, and every segment is
   checked against the repo's rendered allow/deny rules. A missing rule fails
   immediately, naming the exact command and segment.
2. `run_live_proof()` — the truth. Only reached once static_lint is clean: spawns one
   throwaway headless `claude -p` session in the repo under the rendered settings and
   scans its stream-json for `action_gate.is_permission_denial()` hits (the same
   "requires approval" / "blocked. For security" signal PERMISSION_LOOP classification
   already keys off, wq#1107). Zero denials -> `write_stamp()` writes
   `runtime/preflight/<repo>.ok` with `hash(rendered_settings + preflight_cmds)`.

`dispatch_gate_check(repo)` is the third piece — cheap, offline, called by
`foreman_dispatcher.py`'s spawn loop (stage 9b, right before `spawner.dispatch_one`).
It recomputes the same hash from the *current* rendered settings/preflight_cmds and
refuses unless it matches the stamp on disk — a settings or `preflight_cmds` change
since the last proof reads as "no stamp" until `harness-preflight` runs again. A repo
with no `repos.d/<repo>.json` is not gated (same additive-migration boundary §2b draws).

**Auto-reprove on a stale/missing stamp (wq#1372).** A `dispatch_gate_check` refusal no
longer just means `log.skip(..., "preflight", "no_fresh_stamp", ...)` and `continue`
forever — `handle_stale_preflight()` (`foreman_dispatcher.py`) re-runs
`harness_preflight.preflight(repo)` inline, right there in the dispatcher's own
(unsandboxed, host-context) process:
- Clean re-prove -> fresh stamp written, `log.emit("info", stage="preflight",
  reason="auto_reproved", ...)`, and the ticket falls through to spawn in the *same*
  pass — no more waiting on a human to notice and re-run `harness-preflight` by hand.
- Re-prove still fails (denials survive the settings change) -> an `ntfy` alert fires
  (`"preflight auto-reprove failed for <repo>: ..."`) instead of a silent skip, and the
  skip reason becomes `reprove_failed` instead of `no_fresh_stamp`.
- Either way, an in-flight re-prove spawns a real throwaway `claude -p` live-proof
  session (not free), so repeat attempts for the same repo are cooldown-gated
  (`PREFLIGHT_REPROVE_COOLDOWN_H`, state.json's `preflight_ntfy` ledger) — a
  persistently broken profile alerts once per cooldown window, not once per pass.

**Structural limitation, not a bug:** `run_live_proof()`'s `claude` invocation is not on
any worker permission profile's allow list, so `harness-preflight` cannot run its live
layer from inside a dispatched (sandboxed) worker session — the nested call gets
refused by the exact allowlist the tool exists to validate (ISSUE-LOG.md #50). It has to
run from host/dispatcher context. Until `harness-preflight --all` has been run once from
there, `foreman`/`pokemon`/`mailbot` dispatch will pause on `no_fresh_stamp` — visible
and recoverable (the skip reason names the fix), not a crash.

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

## Aider runtime (wq#4289)

`fworker run --runtime aider` (or `FOREMAN_RUNTIME=aider` in the dispatcher env) runs a
ticket through aider on the local openai/lumen/code model instead of `claude -p`. Aider
only edits files; fworker then stages the changed paths, runs
`lumen-pr commit`/`open`/`land`, and closes the ticket with the resulting PR url. Aider
exits 0 even on backend errors, so a run that changed no files must be treated as a
failure, not a success. The backend URL and key come from
`/etc/lumen-aider-fworker.env`. The default runtime remains `claude`.

## SDK runtime: spending the plan's monthly API credit (wq#5211)

Max/Ultra plans carry a monthly Claude API credit (Ultra $200, Max $100; it expires each
billing cycle with no rollover). It pays for Messages API / Agent SDK calls made with a key
from the Console org linked to the plan. It does **not** pay for Claude Code: Anthropic tags
Claude Code traffic by its `anthropic-beta: claude-code-*` header, and the same key that
serves a plain Messages call returns `credit_balance_too_low` once that header is on
(measured 2026-11-21). So `claude -p` on an API key cannot spend the credit, and a
non-Claude-Code client is required.

`--runtime sdk` (or auto-selection, below) runs **`fworker_sdk.py`** in place of `claude -p`:

- **Same argv, same stream-json.** fworker builds the usual `build_claude_cmd()` argv and
  swaps argv[0] (`run_child_sdk`). fworker_sdk emits Claude Code's frames (system/init,
  assistant, user tool_result, result), so `run_child()` supervises it unchanged:
  watchdogs, mask screening, heartbeats, kill-tree, RunState, exit-gate, verify_done,
  park/resume.
- **Same rails.** The `--settings` hooks run with Claude Code's hook contract (guard.sh and
  action_gate PreToolUse, stop_gate Stop, lifecycle start/end), and `permissions.allow/deny`
  is enforced Claude-Code style: deny wins, every segment of a compound Bash command must be
  allowed, and acceptEdits only covers cwd + `--add-dir`. Tools: Bash, Read, Write, Edit,
  Grep, Glob. git, fleet-exec and wq go through Bash under the same rules. MCP
  (`--mcp-config`) is not available.
- **Transport.** anthropic SDK -> LiteLLM `/v1/messages` with the scoped `foreman-apicredit`
  virtual key (`/home/lumen/.secrets/foreman-apicredit.token`; models
  anthropic/claude-opus-5-5 + haiku-5-5; $190 LiteLLM max_budget backstop). The child scrubs
  the key from every tool subprocess's env. Rolling prompt-cache breakpoints, adaptive
  thinking with `output_config.effort` from `--effort`, older tool output elided past
  `FWORKER_SDK_COMPACT_AT` tokens.
- **Sessions.** `_runs/sdk-sessions/<sid>.json` (+ a Claude-Code-format `.jsonl` transcript
  the Stop hook reads). SDK session ids start `sdk-`, so a resume never goes to the CLI, and
  a Claude Code session is never resumed on the SDK.

### Ledger and selection: `api_credit.py`

There is no balance API, so `api_credit.py` is the ledger. State is in
`/home/lumen/foreman/api-credit/` (config.json, state.json, ledger.jsonl).

- Every API call is priced from the platform pricing table (`PRICES`) and appended to the
  ledger. Cross-check against LiteLLM's own key spend: `api_credit.py status`.
- Cycles start on `anchor_day` (13th). `grant_schedule` gives the grant per cycle: Ultra
  $200 until the 2026-12-13 downgrade, Max $100 after. Available while
  spend < grant - `reserve_usd` and the cycle isn't flagged exhausted. ntfy fires at 50/80/95%.
- `credit_balance_too_low`, a LiteLLM budget error, or the local cap flags the cycle
  exhausted. The worker stops (exit 3, `API_CREDIT_EXHAUSTED`). fworker releases the ticket
  **uncharged** (`handle_api_credit_exhausted`, EXIT_RUNG_FAULT), and the next dispatch runs
  it on the CLI. A new cycle id clears the flag on its own.
- `choose_runtime()` is the single SDK-vs-CLI decision, made by fworker at claim time
  (`select_runtime`) and by auto-dispatch. In order: explicit `--runtime claude|aider` wins;
  a resume stays with the session's runtime; `[no-sdk]` / `[sdk]` title markers; config
  `enabled`/`auto_select`; the routed model must match `eligible_model_re` (opus/sonnet/haiku;
  Fable stays on the CLI); fire-response is excluded; credits must be available. An explicit
  `--runtime sdk` with no credits left falls back to the CLI.
- **Missing or unreadable config = disabled.** Tests run against a scratch ledger
  (`_isolate_api_credit` in the repo conftest), which fails any test that grows the live ledger.

```bash
python3 /home/lumen/foreman/harness/api_credit.py status            # spend, cap, LiteLLM cross-check
python3 /home/lumen/foreman/harness/api_credit.py choose <ticket>   # what fworker would pick
python3 /home/lumen/foreman/harness/api_credit.py set-prior <usd>   # console spend the ledger never saw
python3 /home/lumen/foreman/harness/api_credit.py mark-exhausted "<why>" | clear-exhausted
# off switch: "auto_select": false (explicit --runtime sdk only) or "enabled": false in config.json
```
