# wq#1227 — verify-pass: wq#1223 mailbot per-thread task dedupe

**Verdict: CONFIRM** — the shipped fix suppresses the ack+enqueue on a second task-shaped
message in a thread with an open pending task, falls through to a real generated reply, and
never touches the existing pending row. Verified by live run against the shipped commit, not
just by reading the diff.

Commit: `d81b8ac` on `feat/wq1223-mailbot-task-router-re-acks-and-re-enque-a1` in
`/home/lumen/mailbot` (branched from wq1222 tip `35dd91c`). No origin remote on this repo
(repo-wide, matches wq1222's precedent) — no PR to link.

## What the diff does

`hasPendingTask(env, threadId)` (src/worker.js:375) queries `tasks` for an open
`status='pending'` row for the thread. In the router (:551), `taskCategory` is computed as
`rawTaskCategory && !hasPendingTask` — i.e. suppressed to `null` when a pending row already
exists — so the message falls through to `generateReply()` instead of `buildAck()` +
`enqueueTask()`. `enqueueTask()` is only ever called when `taskCategory` is truthy, so an
existing pending row is never touched. Adds `migrations/0005` (index on
`tasks(thread_id, status)` to serve the new lookup) and fixes `mail_tasks.py`'s
`answered_at` to write a `Z`-suffixed timestamp instead of `+00:00`.

## Verification performed

1. **Read the diff cold**: `git show d81b8ac` in `/home/lumen/mailbot` (worktree
   `feat/wq1223-...` already checked out elsewhere as `wq1223-a1`, outside my allowed dirs,
   so I read via `git show <sha>:<path>`). Traced `rawTaskCategory` → `hasPendingTask` →
   `taskCategory` → the ack/enqueue branches (worker.js:543-642) and confirmed the shape
   matches option (a) from wq#1223's ticket (suppress ack+enqueue, fall through to
   `generateReply()`) and that `enqueueTask()` is unreachable when a pending row exists.
2. **Schema check**: `migrations/0003_tasks.sql`'s `tasks` table has `thread_id`/`status`
   columns matching `hasPendingTask`'s query; `0005` is the correct next migration number in
   sequence (0001-0004 already existed at this commit).
3. **`node --check`** on the commit's `src/worker.js`: clean.
4. **Live end-to-end run** (this is what the prior verify attempt on this ticket skipped —
   orchestrator flagged it FALSE_COMPLETE for not demonstrating the fix): created a detached
   worktree at `d81b8ac` (`git worktree add --detach .../mailbot-verify d81b8ac`, separate
   from the pre-existing `wq1223-a1` worktree and from the main `/home/lumen/mailbot`
   checkout, which stayed untouched throughout — confirmed via `git status --short` before
   and after), applied all 5 migrations to a fresh local D1 (`wrangler d1 migrations apply
   MAIL --local`), copied `.dev.vars` (DeepSeek key) without ever printing it, ran
   `wrangler dev --local` against real network egress (confirmed reachable:
   `curl https://api.deepseek.com` → 401, i.e. reachable, just unauthenticated without a
   key), and sent two synthetic emails matching test_v2.sh's shipped fixture 11:
   - `<n7@example.com>` "quick question" / "Can you call me back..." (task-shaped, no prior
     thread) → ack template sent, one `tasks` row inserted (`category: callback,
     status: pending`).
   - `<n11@example.com>` In-Reply-To `<n7@example.com>`, "just following up on that -- any
     update on the callback?" (task-shaped nudge, same thread) → log emitted `task dedupe:
     pending task already open for thread t_421646d1bc1ef02a7bb3a3d6 - falling through to
     generated reply`; DeepSeek was called (`DeepSeek finish_reason: stop content_len: 683`)
     and the actual outbound `.eml` is a real conversational reply acknowledging the pending
     status ("The owner hasn't gotten back to me with a slot for you... I'll ping him
     again."), not the ack template.
   - Queried D1 directly after both sends: `SELECT * FROM tasks WHERE thread_id=...` returns
     **exactly one row**, `created_at` matching the first message only — confirms the
     existing pending row was never overwritten or duplicated.
5. **Adversarial checks**: confirmed `enqueueTask()` has no other call site than the one
   gated by `taskCategory` (grep), so dedupe suppression structurally cannot leave a
   duplicate row. Confirmed `hasPendingTask` fails open (`false`) on a D1 error, same
   direction as the pre-existing `loadHistory` — worst case on infra failure is the
   pre-fix duplicate-row behavior, not a silently dropped inbound message.

## Not a contradiction, noted for completeness

Two task-shaped messages arriving concurrently in the same new thread (no pending row yet)
could both read `hasPendingTask() === false` and both enqueue -- a TOCTOU race. Not
realistic for this system: Cloudflare email triggers for one Worker are processed one
`email()` invocation at a time, not truly concurrently, and the ticket's acceptance shape
didn't ask for transactional dedupe. Not flagging as a defect.

Cleanup: stopped the background `wrangler dev`, removed the temporary detached worktree
(`git worktree remove --force`), confirmed `/home/lumen/mailbot`'s pre-existing unrelated
uncommitted changes (someone else's in-progress work on `main`) were untouched throughout.
