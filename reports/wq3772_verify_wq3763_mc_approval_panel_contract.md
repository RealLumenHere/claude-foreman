# Verify wq#3772 — wq#3763 MC approval panel data/write contract

Target: PR #1769 (http://100.121.17.5:3000/lumen/claude-foreman/pulls/1769), merged as
`9ebcbe22` into `b6f7a895`. Single-file diff: `research/MC_APPROVAL_PANEL_CONTRACT.md` (+83).
Author: claude-sonnet-5. Verifier: claude-sonnet-5@high, fresh instance, archetype 8.

## Method

No code/tests ship in this PR — it's a research/data-contract doc. "Run its probe" here
means: (1) execute the SQL query it specifies against the live `queue.db`, (2) check every
`file:line` citation against the actual source at HEAD, (3) check every design-doc/report
cross-reference it quotes.

## Checks performed, all confirmed

- **SQL query** (`SELECT id, title, detail, decision_prompt, created_at FROM tasks WHERE
  gate_type='frank_decision' AND status='pending_review' AND title LIKE 'research: %' ORDER
  BY id;`) ran clean against `/home/lumen/agents/work-queue/queue.db` (0 rows now — no bridge
  proposal is currently pending). Confirmed every referenced column exists via
  `PRAGMA table_info(tasks)`.
- **The `title LIKE 'research: %'` filter is load-bearing, not redundant**: live data has 7
  rows at `gate_type='frank_decision' AND status='pending_review'` right now (wq#3042,
  3457, 3677, 3723, 3749, 3750, 3751) and none of them are bridge-created — they're
  FRANK-GATE/physical/sentinel tickets. Without the title filter the panel would show
  Frank's other decision queues mixed into the research-proposal panel. Same for the
  `frank_gated` sample (wq#1341, 1747, 2106, 2287...) — all physical/token gates, zero
  bridge rows. This empirically validates the doc's central claim in §1.
- **`research/bridge.py:49`** — `TITLE_PREFIX = "research: "`. Exact.
- **`research/bridge.py:185-201`** — lines 186-187 are the `wq add --frank-gated` call,
  line 201 is the `wq decide` call, same `submit()` invocation. Matches.
- **`research/bridge.py:203-204`** — `if d.returncode: out["warning"] = ...`. Confirms the
  doc's claim that a `wq decide` failure leaves the row at `frank_gated` with no
  `decision_prompt` (not actionable by `wq answer`, which refuses at `wq:3417-3419` without
  a `decision_prompt`). Exact.
- **`research/bridge.py:208-245`** (`reject()`) and **`:258-261`** (reject subparser) — exact
  line match. `reject()` writes via `ledger.record` (→ `rejections` table,
  `research/ledger.py:31-34`) then `wq done --no-artifact`.
- **`research/ledger.py:19`** — `CLASS_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")`. Exact.
- **`/home/lumen/bin/wq:3355`** (`cmd_decide`) — exact def line; body (3391, 3396) confirms
  `frank_gated` → `pending_review` with `decision_prompt`/`orch_recommendation` set.
- **`/home/lumen/bin/wq:3402-3426`** (`cmd_answer`) — exact: refuses unless
  `status='pending_review'` (3414-3416) and `decision_prompt` is set (3417-3419); writes
  `frank_response`/`response_ts` (3424); status column untouched. Confirmed MC's only
  legitimate write path is `wq answer`, never direct `tasks` mutation.
- **`/home/lumen/bin/wq:3465-3471`** (reject branch of `cmd_review`) — exact: sets
  `status='frank_gated'`, clears `frank_response`/`response_ts`, writes no ledger row.
  Confirms the doc's warning that `wq review reject` is the wrong terminal path for a
  research rejection.
- **Design doc cross-ref** (`design/wq3755_research_pipeline_v2.md` §4 step 3, lines
  133-138) — "Evidence: foreman/research/evidence/<id>.json pointer + the originating
  trigger" matches `ticket_detail()` (`bridge.py:126-139`) verbatim.
- **`reports/opsaudit_a13_flow_metrics.md` §"Mission Control panel"** (lines 146-156) —
  quoted text present verbatim; MC app source genuinely outside this worktree's reach (`ls
  /home/lumen/mission-control` was refused by the sandbox from this session, corroborating
  the scope claim live rather than just by citation).
- **`orchestrator/router.py:116`** — `"mission-control": "/home/lumen/mission-control"` in
  `LANE_REPO`. Exact. Follow-up ticket wq#3771 (`lane=mission-control`, deps wq#3760) is
  filed and claimed, consistent with the doc's §4 scope line.

## Minor nit (non-blocking)

`cmd_review`'s `def cmd_review(a):` is at `wq:3431`, not `3429` as cited for the accept-path
range (`wq:3429-3463`) — lines 3429-3430 are actually the tail of `cmd_answer`. A 2-line
citation offset; the described behavior (accept: `pending_review`→`open`) is correct and the
reject-path citation (`3465-3471`) is exact. Not a factual error, not worth a redispatch.

## Verdict

**CONFIRMED.** Every substantive claim — the `frank_gated`-vs-`pending_review` steady-state
correction (the doc's main contribution, since wq#3763's own ticket detail assumed
`frank_gated` rows), the query, the rendering fields, and both write paths (approve via `wq
review accept`, reject via `bridge.py reject`, explicitly not `wq review reject`) — checks
out against live code and live queue.db data. No contradiction; no escalation to opus-5@high
needed.
