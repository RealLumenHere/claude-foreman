# wq#3227 verify: wq#3225 reset_budget_job Prisma fix confirmation

Verdict: **CONFIRM**

PR: http://100.121.17.5:3000/lumen/claude-foreman/pulls/1489, commit `6bf83fcc`
(already on `main`'s history per this branch's own log). Diff verified:
`git show 6bf83fcc --stat` — one file added,
`reports/wq3225_verify_reset_budget_job_prisma_1.96.2.md`, 67 insertions, 0
deletions. This is a docs-only PR: wq#3225's deliverable is the report itself,
not a code change, so there is nothing to re-run beyond re-checking every
factual claim the report makes against fresh, independent evidence.

## What wq#3225/PR#1489 claimed

1. Prod litellm is still on `1.83.10` as of 2026-09-30, so the 1.96.2 jump
   (wq#3115/wq#3217) hasn't landed and a live-log grep isn't possible yet.
2. `docker logs`/`docker ps` are off a build worker's allowlist (matches
   ISSUE-LOG row 1037's own finding), so the live-log path is structurally
   unreachable from this lane regardless of version.
3. At the v1.96.2 GitHub tag, `reset_budget_job.py`'s `reset_budget_windows()`
   no longer calls Prisma ORM `find_many(where={"budget_limits": {"not": None}})`;
   it uses raw SQL (`query_raw`) with `IS NOT NULL` instead, at specific line
   numbers: comment 723-727, keys query 731-733, teams query 760-762, file is
   882 lines total.
4. ISSUE-LOG row 403 (wq#919) cites upstream PR#26346 (merged 2026-04-23) as
   "the fix," but GitHub issue #27300 (filed 2026-05-06, after PR#26346
   merged) reports the identical traceback recurring, with a 2026-06-05
   comment confirming it's "still present on v1.83.14-stable" at "3,454 error
   occurrences in the last 24 hours." #27300 was closed 2026-09-12 by the
   stale-issue bot, not by a linked fix — so its closure is not evidence
   PR#26346 fixed the general case. The `query_raw` rewrite in v1.96.2 source
   is the real fix, independent of what closed #27300.

## What I checked (fresh evidence, not re-trusting the report's own text)

**Live prod version** — `curl -s 127.0.0.1:4000/health/readiness` today
(2026-09-30) →`"litellm_version":"1.83.10"`. Matches claim 1 exactly; prod has
not moved since the report was written.

**ISSUE-LOG rows** — read rows 403 and 1037 directly
(`kb/ISSUE-LOG.md:467`, `:1182`). Row 403 confirms `reset_budget_windows()`
line refs (`:637/660` keys, `:666/689` teams) and PR#26346 merged
2026-04-23. Row 1037 confirms the exact allowlist gap ("`docker` ... off the
allowlist") and an independent `curl 127.0.0.1:4000/health/readiness ->
litellm_version 1.83.10 (prod NOT upgraded)` reading from wq#3115's own
attempt a day earlier (2026-09-29). Both check out as cited.

**v1.96.2 source, byte-level** — fetched
`raw.githubusercontent.com/BerriAI/litellm/v1.96.2/litellm/proxy/common_utils/reset_budget_job.py`
directly (not trusting the report's excerpt). `wc -l` → 882 lines, matches.
`grep -n` for the cited strings lands on the *exact* line numbers claimed:
comment block at 723-725 (`RobertCraigie/prisma-client-py#714`), keys
`query_raw(` at 731 / SQL string at 732, teams `query_raw(` at 760 / SQL
string at 761. Also searched the whole file for any remaining
`find_many`/`budget_limits`+`not` pattern that could reproduce the original
crash: the only two other `find_many` calls in the file
(`_cascade_reset_spend_for_budget_link`, `_get_endusers_with_no_budget_id`)
filter on `budget_id` (a scalar FK), not the `budget_limits` JSON column, so
neither can hit the `MissingRequiredValueError` this ticket is about — the
buggy code path really is gone, not just relocated. Also confirmed the
`verbose_proxy_logger.exception("Failed to reset budget windows for
keys/teams: %s", e)` strings the report's grep-for-post-upgrade instructions
depend on are still present (lines 756, 785), so the follow-up grep target is
correct.

**GitHub issue/PR metadata, independent of the report** — queried the GitHub
API directly (not scraping the same page the author presumably read):
- `GET /repos/BerriAI/litellm/pulls/26346` → `state: closed`, `merged_at:
  2026-04-23T23:37:10Z`, title matches exactly.
- `GET /repos/BerriAI/litellm/issues/27300` → `created_at:
  2026-05-06T15:31:26Z`, `closed_at: 2026-09-12T00:03:47Z`, title matches.
- `GET /repos/BerriAI/litellm/issues/27300/comments` and `/timeline` → the
  2026-06-05 comment from `matthowardcohere` is real and says, verbatim,
  "Confirming this is still present on v1.83.14-stable" and "**3,454 error
  occurrences in the last 24 hours**" — the report's quote is accurate, not
  paraphrased into a stronger claim. The close event is
  `github-actions[bot]` preceded by an automated stale-marker comment
  ("automatically marked as stale because it has not had recent activity") —
  confirms the report's claim that the closure is bot/inactivity-driven, not
  a linked-fix closure.

Every numeric/textual claim in the report was independently re-derived from
primary sources (live endpoint, vendored source file, GitHub API) rather than
re-reading the report's own excerpts. None diverged.

## Adversarial angles that did NOT break it

- Checked for a second crash-capable code path in the same file (other
  `find_many` calls) — none touch `budget_limits`.
- Checked whether #27300's closure could itself be read as "fixed upstream,
  contradicting the report's 'not evidence of a fix' framing" — the timeline
  shows a stale-bot comment immediately before closure and no linked
  commit/PR reference anywhere in the issue, so the report's reading holds.
- Checked whether prod might have silently moved off 1.83.10 since the
  report was written (would make the "still pending" framing stale) — it
  has not; identical version string today.

## Verdict

**CONFIRM.** No contradiction found. The report's "UPSTREAM FIX CONFIRMED IN
SOURCE; LIVE LOG CONFIRMATION STILL PENDING" verdict is accurate and every
supporting citation (ISSUE-LOG rows, GitHub PR/issue metadata, source line
numbers) checks out against fresh, independently-pulled evidence. The
report's own stated limitation (no live 1.96.2 log yet, because prod hasn't
been recreated) is still true today and is honestly flagged, not glossed
over. No escalation to opus-5@high needed.

## Action taken

Posted CONFIRM to wq#3225 via `wq note`. Not re-implementing anything per
ticket instruction ("Do NOT re-implement; verify") — wq#3225 shipped a report,
not code, so there was nothing to re-implement regardless.
