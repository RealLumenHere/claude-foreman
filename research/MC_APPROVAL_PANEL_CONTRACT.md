# MC approval panel — data contract (wq#3763)

Mission Control's application source lives outside this worktree's allowed scope (same
boundary `reports/opsaudit_a13_flow_metrics.md` §"Mission Control panel" hit for
`/api/fleet`: "this ticket proves the data and the query, not the MC route"). What this
ticket ships for MC to consume, unblocked and ready — the query, the status semantics, and
the write path, each grounded against the code that currently enforces them:

## 1. Which rows are a research proposal awaiting Frank

`research/bridge.py:submit()` calls `wq add --frank-gated` (status=`frank_gated`,
`gate_type='frank_decision'`) and then immediately `wq decide` in the same call
(`research/bridge.py:185-201`), which advances the row to `pending_review`
(`/home/lumen/bin/wq:3355` `cmd_decide`) before `submit()` returns. So in the steady state
the row MC must show Frank is already `pending_review`, not `frank_gated` — the ticket's
"frank_gated rows" describes the family by its `gate_type`, not the literal status a panel
query should filter on. A row can transiently sit in `frank_gated` with no
`decision_prompt` only if `wq decide` itself failed (`research/bridge.py:203-204` logs a
warning and leaves it); that case isn't actionable by `wq answer` yet and should render as
"awaiting retry," not as an approve/reject row.

Bridge-created rows are distinguished from other `gate_type='frank_decision'` tickets (e.g.
physical-task gates) by the title prefix `research/bridge.py:49` `TITLE_PREFIX = "research: "`.

Query:

```sql
SELECT id, title, detail, decision_prompt, created_at
FROM tasks
WHERE gate_type = 'frank_decision'
  AND status = 'pending_review'
  AND title LIKE 'research: %'
ORDER BY id;
```

(Read-only, same WAL discipline as every other queue reader — `queue.db` at
`/home/lumen/agents/work-queue/queue.db`.)

## 2. Rendering

- `title` minus the `research: ` prefix — the proposal one-liner.
- `decision_prompt` — the one question Frank must answer (set by `wq decide --prompt`).
- `detail` — proposal text + an `Evidence: foreman/research/evidence/<id>.json` pointer +
  the originating trigger, per `design/wq3755_research_pipeline_v2.md` §4 step 3.

## 3. Writing Frank's verdict

MC never updates `tasks` directly. Frank's free-text verdict is recorded with:

```
/home/lumen/bin/wq answer <id> "<free text>"
```

`cmd_answer` (`/home/lumen/bin/wq:3402-3426`) requires `status='pending_review'` and a
non-null `decision_prompt`; it writes `frank_response`/`response_ts` and leaves status at
`pending_review` — it never flips the ticket by itself.

Then, depending on the verdict text:

- **Approve** → `wq review <id> accept [--reason "..."]`. `cmd_review`
  (`/home/lumen/bin/wq:3429-3463`) moves `pending_review` → `open`, clearing the gate; the
  ticket is now claimable in the normal build lane (design doc §4 step 5).
- **Reject** → do **not** call `wq review <id> reject` — that path is generic
  "ORCH disputes Frank's answer, ask again" (`/home/lumen/bin/wq:3465-3471`: it clears
  `frank_response` and reopens `frank_gated`, it does **not** touch the rejection ledger).
  The terminal, ledger-writing rejection for a research proposal is:

  ```
  python3 research/bridge.py reject <id> --class <slug> [--reason "..."]
  ```

  `research/bridge.py:258-261` (`reject` subparser) / `:208-245` (`reject()`): writes a row
  to the `rejections` table (so the next brief's constraint block contains it, wq#3760) and
  closes the ticket via `wq done --no-artifact`. `--reason` defaults to the ticket's
  `frank_response` already recorded by `wq answer`, so MC only has to pass `--class`.
  `--class` must match `research/ledger.py:19` `CLASS_RE = ^[a-z0-9][a-z0-9_-]{0,31}$`
  (e.g. `duplicate`, `out-of-scope`, `already-solved`, `wrong-arch`).

## 4. Scope line

This file is the data/write contract only. Wiring an MC route/UI to it is MC-repo work,
same boundary A13 recorded for `/api/fleet` — dispatched as a separate ticket in the
`mission-control` lane (`orchestrator/router.py:116`) rather than built in this worktree.
