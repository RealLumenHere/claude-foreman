# EE_WORKSPACE_MEMORY_PILOT -- can ee 0.18.0 be foreman's workspace memory? (wq#4871)

Status: RESEARCH ONLY (Frank: no installation). Nothing was copied, installed,
initialised or hooked. `.ee/` does NOT exist in /home/lumen/foreman after this
session; the ticket's liveness probe (`test -d /home/lumen/foreman/.ee`) is
therefore intentionally NOT satisfied -- the pilot itself is re-filed as gated
follow-up tickets (section 8).
Author: eval-foreman-patterns-b39338 (claude.ai orchestrator, 2026-11-05).
Parent: scratch/research/eidetic_engine_evaluation.md (wq#4854) section 5 step 2.

## 0. Verified on Lumen, 2026-11-05 (read-only; `--help` and `--print` only)

| fact | value |
|---|---|
| binary | /tmp/fworker-wq4854/dl/ee, 140,045,400 bytes, mode 0700, sha256 a94a59159efe69fac8dc18e417f2c4289c90d8ffdcdae1ed374e4975d439c5c0 (binary; the a8c78867... in wq#4854 is the TARBALL hash -- both must be pinned) |
| binary location risk | /tmp is volatile. A tmp-cleaner or power cycle loses it; re-download = new supply-chain event. The eval's /scratch/research-tmp/wq4854/ copy holds the ee WORKSPACE, not the binary |
| foreman root collision check | NO AGENTS.md, NO CLAUDE.md, NO .ee/ in /home/lumen/foreman; .gitignore has NO .ee entry. `ee init` would create AGENTS.md + CLAUDE.md in the repo root -> they would be untracked files inside a git repo that fworkers commit from. Must be handled before init (section 4.1) |
| fworker settings | fworkers run claude with `--settings harness/worker-settings.json` (fworker.py:104, SETTINGS_FILE). Hooks today: PreToolUse guard.sh + action_gate.py; SessionStart/Stop/SessionEnd guard.sh lifecycle; Stop stop_gate.py |
| user settings | /home/lumen/.claude/settings.json keys = env, theme, teammateMode, agentPushNotifEnabled -- NO hooks. This is what `ee hook claude-code --install` would edit by default |
| cwd of a fworker turn | the per-ticket worktree /home/lumen/worktrees/wqNNNN-aN, NOT /home/lumen/foreman |
| `ee hook claude-code --print` | writes nothing (default). Generated plan: 4 hooks (section 2). Every generated command runs with `cwd = hook payload cwd` and `--workspace "."` |
| `ee import agentsmd --apply` | "trust class capped at agent_assertion" (its own --help). The eval saw `ee remember` rules rank as human_explicit/authoritative |
| `ee pack --format hook` | exists (format enum: human,json,toon,jsonl,compact,hook,markdown,binary,mermaid); `--workspace`, `--max-tokens`, `--task-path`, `--use-daemon`, `--candidate-pool`, `--task-key` all present |
| `ee orient <TASK>` | "read-only orientation bundle for an agent starting a task"; this, not `pack`, is what the generated SessionStart hook calls |
| foreman _runs | 3,260 ticket dirs under /home/lumen/foreman/_runs, each with attempt-N.json, failure.json, loop-handoff-*.md -- the raw material for both the failure-memory seed and the outcome metric |

## 1. The ticket's plan vs what the tool actually does

The ticket assumed `ee hook claude-code` wires `pack --format hook` on
SessionStart/PreToolUse. It does not. Three mismatches change the pilot:

1. **Wrong settings file.** `--install` targets ~/.claude/settings.json.
   fworkers never read it (they pass --settings worker-settings.json). The
   hook must land in harness/worker-settings.json, which is a TRACKED file in
   the foreman repo -> it is a PR, not an `ee hook --install`. `--settings-path`
   exists but still means a tool writes into a tracked file outside git review.
2. **Wrong workspace.** Every generated hook runs `ee ... --workspace .` with
   cwd = the worktree. The worktree has no `.ee/` (gitignored, and must stay
   so). ee fails open: the hook emits nothing, the pilot measures nothing, and
   a 2-week A/B would "prove" ee has no effect. The hook MUST pin
   `--workspace /home/lumen/foreman` (or an env equivalent, section 4.3).
3. **Wrong slice.** The generated SessionStart hook calls `ee orient <task>`
   (primer + fast pack, budget 1600 tok); the PreToolUse hook calls
   `ee recall --path <edited file>` on Edit|Write only; a Stop hook runs
   `capture suggest`; a Bash hook journals non-zero exits. None of these is
   the "What NOT to do" failure slice for the TICKET TITLE that wq#4854
   section 3 found valuable. That needs a custom SessionStart hook:
   `ee pack "<ticket title>" --workspace /home/lumen/foreman --format hook
   --max-tokens 1500 --task-key wq<N>`.

Net: use ee's generator as a reference for the hook envelope (it handles
fail-open, per-session dedupe via .ee/hook-state, budgets, timeouts of 9-10 s)
but write foreman's own hook script, `harness/hooks/ee_pack.py`, committed and
reviewed like stop_gate.py.

## 2. What the generated hooks would do if installed as-is (from --print)

| hook | event | command | foreman concern |
|---|---|---|---|
| ee-ambient-session-orient | SessionStart | `ee orient <task> --workspace . --use-daemon --include-primer --fast --candidate-pool 20 --format hook --max-tokens ~1470` (timeout 9 s) | `--use-daemon` = a warm per-workspace daemon process; with N concurrent fworkers that is a new long-lived process on Lumen nobody provisioned. Falls back in-process if absent (acceptable for the pilot: do NOT run the daemon) |
| ee-ambient-pre-edit-recall | PreToolUse Edit/Write/MultiEdit/NotebookEdit | `ee recall --path ... --budget-tokens 400` (timeout 9 s) | adds up to 9 s per edit; fworker turns are budgeted (TURN_BACKSTOP). Skip for the pilot; title-level pack once per session is the hypothesis under test |
| ee-ambient-bash-journal | PostToolUse Bash non-zero | `ee journal append ... --source hook --kind command_failure` (timeout 10 s) | WRITES to the shared ee.db from every concurrent fworker. SQLite single-writer + 10 s timeout = silent drops under load, and it would also journal gate-refused commands as "failures". Skip for the pilot |
| ee-ambient-session-capture-suggest | Stop | `ee capture suggest --from-recent --max 2 --json` | read-only suggestions, nothing stored; harmless but noise for a non-interactive worker. Skip |
State dir: `<cwd>/.ee/hook-state/` -> inside the WORKTREE -> would be committed
by a careless fworker unless .ee/ is gitignored in every worktree (it is a
repo-level .gitignore entry, so one PR covers all worktrees).

## 3. Standing-orders rules: how they get in, and with what trust

SESSION-BRIEFING section 4 holds 26 STANDING ORDERS today (generated from
claude_memory.md on every --core boot). Two routes into ee:

| route | trust class ee assigns | fit |
|---|---|---|
| generate AGENTS.md -> `ee import agentsmd --apply` -> `curate apply` | capped at **agent_assertion** (ee's own --help) | WRONG for standing orders: they are Frank's words. Ranked below human_explicit rules, below imported CASS evidence in the pack's procedural slice |
| `ee remember "<order>" --level procedural --kind rule --confidence 0.95` once per order | human_explicit / authoritative (observed in wq#4854 section 3) | RIGHT, but `remember` is not idempotent: re-running the seed duplicates rules |
Design: `harness/ee_seed_rules.py` reads SESSION-BRIEFING section 4, computes
a stable key per order (sha256 of the normalised text), keeps a seed ledger
`.ee/seed-ledger.json` {key: memory_id}, and only `remember`s unseen keys;
removed orders are `ee memory expire`d. Runs once per --core boot alongside
session-briefing, so ee's rule set tracks claude_memory.md instead of
forking from it. Nothing writes back from ee to claude_memory.md or
lumen_state.py (ticket rule: do NOT touch lumen_state.py / episode gate).

Failure memories (the "What NOT to do" slice) come from two sources:
- `ee import cass --workspace /home/lumen/foreman --limit 150` (the eval's
  path; 83 s, 916 MB RSS, 81 MB db -- run detached, never in a hook).
- Better signal, not in the ticket: foreman/_runs/wq*/failure.json +
  loop-handoff-*.md already classify every failed attempt
  (PERMISSION_LOOP, KILLED, CAPABILITY_DENIED, with the offending command).
  A `harness/ee_seed_failures.py` that turns each failure.json into
  `ee remember --level episodic --kind failure` with the ticket title as
  context gives the pack exact-title matches for retried tickets -- which is
  precisely the cohort the metric cares about (section 6).

## 4. Pilot design (what the follow-up tickets implement, in order)

### 4.1 Pre-conditions (one PR, no runtime effect)
- `.gitignore` += `.ee/` and `.ee/**` at the foreman root.
- Decide AGENTS.md/CLAUDE.md: `ee init` writes both. Options: (a) let it
  write them, then `git rm --cached` + gitignore -- fragile, fworkers may
  still read a root CLAUDE.md as instructions; (b) run `ee init` in the
  pinned workspace and immediately delete the two generated files, keeping
  only `.ee/` (ee does not need them to pack -- verify in phase 1 with
  `ee doctor`); (c) keep CLAUDE.md as a reviewed, committed file that says
  "memory lives in ee; read the pack" -- the eval noted `ee export agentsmd`
  exists for exactly this. Recommend (b) for the pilot, (c) only if the
  pilot wins.
- Binary: copy /tmp/fworker-wq4854/dl/ee -> /home/lumen/bin/ee (0755), record
  sha256 (section 0) + tarball sha in lumen_state (`ee binary_sha256`,
  `ee version 0.18.0`, `ee update_policy pinned-never-auto`), re-verify sha
  before every pilot phase. New executable on Lumen = Frank-visible step.

### 4.2 Workspace init (detached, once)
`ee init --workspace /home/lumen/foreman`, apply 4.1(b), `ee doctor`, then the
two seeders (3) and `ee import cass --limit 150` detached via systemd-run,
output to /home/lumen/kb/logs/ee-seed.log. Expect .ee/ ~100 MB on the ROOT
LV (foreman is on root; 119 G free per claude_memory). `--database` is
half-supported (index stays under workspace/.ee) so the db cannot be moved to
/scratch; acceptable at this size, re-check if it passes 1 G.

### 4.3 Hook (one PR, behind a flag, default OFF)
`harness/hooks/ee_pack.py`, registered in worker-settings.json under
SessionStart only. Behaviour:
- exit 0 immediately unless `FOREMAN_EE_HOOK=1` in the env fworker passes to
  the child (fworker already builds the child env; add the var from a config
  key, default off).
- read ticket id + title from the fworker env (fworker exports its ticket
  context to hooks today for guard.sh -- reuse the same variables; verify
  names in phase 1), run
  `/home/lumen/bin/ee pack "<title>" --workspace /home/lumen/foreman
  --format hook --max-tokens 1500 --candidate-pool 20 --task-key wq<N>`
  with `timeout 9`, EE_AMBIENT_CONTEXT_STATE_DIR pointed at
  `_runs/wq<N>/a<M>/ee-state/` (never the worktree).
- write the full pack JSON to `_runs/wq<N>/a<M>/ee_pack.json` and a one-line
  record {matched_failure_memory: bool, n_rules, n_failures, ms} -- this is
  the cohort label for section 6.
- on any error/timeout: log, emit nothing, exit 0 (fail open). The pack can
  never block a worker.
- NO journal/recall/capture hooks in the pilot (section 2).

### 4.4 Rollout
Week 0: flag off for everyone; seeders run; `ee pack` smoke-tested from the
orchestrator on 10 recent failed ticket titles, results pasted into the pilot
ticket (does the failure slice actually surface the matching failure.json?).
If fewer than 5/10 match, stop: the hypothesis has no mechanism.
Weeks 1-2: `FOREMAN_EE_HOOK=1` for tickets with ODD ids only (deterministic
A/B, no scheduler change). Even ids = control.
Week 3: read the metric; decide keep / widen / remove. Removal = flip the
flag and delete `.ee/`; nothing else in the fleet depends on it.

## 5. Risks found (beyond wq#4854 section 7)

| risk | severity | mitigation |
|---|---|---|
| hook runs with cwd=worktree and `--workspace .` -> silently empty | HIGH (invalidates the pilot) | custom hook pins --workspace (4.3) |
| `--install` edits a tracked file / the wrong file | MED | never use --install; PR the hook |
| concurrent fworkers writing ee.db (journal hook, daemon) | MED | read-only hooks only; no daemon; seeders are the only writers and run detached, serialised |
| 9 s SessionStart latency per attempt, 140 MB binary cold-start | LOW | one call per session; measure ms in the pack record |
| binary in /tmp | MED | pin + sha in state; copy to /home/lumen/bin as a gated step |
| ee release cadence (4 minors in 5 weeks, response envelope already v2) | MED | pinned; the hook parses only `command,ambientContext` fields; any schema drift = empty pack, not a crash |
| AGENTS.md/CLAUDE.md written into the repo root | MED | 4.1(b); re-check after every `ee init` |
| .ee/ on root LV | LOW | ~100 MB; re-check at 1 G |
| license rider (OpenAI/Anthropic restricted parties) | LOW for fleet use (wq#4854 section 4) | packs are data; never upload binary/source; keep it off any Anthropic-controlled host |
| confound: the hold-state work (2026-10-28 research, wq#4758) is changing PERMISSION_LOOP rates at the same time | HIGH for the metric | A/B by ticket parity inside the same window, not before/after |

## 6. Metric (what "it worked" means)

Population: fworker attempts started in the 2-week window.
Cohort A: odd ticket ids (hook on). Cohort B: even ids (hook off).
Per attempt, from _runs/wq<N>/attempt-<M>.json + failure.json:
outcome in {SUCCESS, PERMISSION_LOOP, KILLED, CAPABILITY_DENIED, other}.
Per A-attempt, from ee_pack.json record: matched_failure_memory (bool).
Report (`harness/ee_pilot_report.py`, read-only over _runs + queue.db):
1. PERMISSION_LOOP+KILLED rate, A vs B, with counts (expect ~100-200
   attempts per cohort at the current 60 open / 4,600 done pace -- enough
   for a large effect, not a small one; say so in the report).
2. Within A: rate for matched vs unmatched packs. The mechanism claim is
   "matched < unmatched"; if matched packs do not help, more memory will not
   either.
3. Median hook latency; count of hook timeouts/errors (must be ~0).
Decision rule proposed to Frank: keep if (1) shows at least a one-third
relative reduction AND (2) agrees in direction; otherwise remove.

## 7. Verdict

GO for a bounded pilot, with the ticket's plan corrected: custom
SessionStart-only hook pinned to the foreman workspace behind a default-off
flag; rules seeded by an idempotent `ee remember` ledger (not agentsmd
import); failure memories seeded from _runs failure.json (not only CASS);
A/B by ticket parity; nothing in lumen_state.py / lumen_episode.py touched.
The pilot is reversible in one flag flip plus one directory delete.
Everything that executes a new binary, writes a tracked file, or creates
.ee/ in the repo is a gated follow-up ticket, not this one.

## 8. Follow-up tickets (filed by this session; see wq notes on #4871 for ids)
P1 [FRANK GATE] pin + install ee binary to /home/lumen/bin, .gitignore .ee/,
   `ee init` in /home/lumen/foreman with AGENTS.md/CLAUDE.md handling 4.1(b),
   record sha/version/policy in lumen_state.
P2 seeders: ee_seed_rules.py (standing orders, idempotent ledger) and
   ee_seed_failures.py (_runs failure.json -> failure memories); detached
   cass import; 10-title smoke test with pasted results (week 0 gate).
P3 hook: harness/hooks/ee_pack.py + worker-settings.json SessionStart entry
   behind FOREMAN_EE_HOOK (default off), pack record under _runs; PR.
P4 run + report: 2-week odd/even A/B, ee_pilot_report.py, decision to Frank.
