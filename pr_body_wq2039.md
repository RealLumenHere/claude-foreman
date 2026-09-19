`lumen-signal-engine.timer` has fired daily at 06:00 UTC since May and exited 0
every time. Submitted prompts by month: May 68 · Jun 1 · Jul 3 · Aug 1 · Sep 0.
Green unit, zero forward progress. The ticket scoped this as "design first;
decide retire vs rewire to the `research_prompts` queue."

**Decision: retire.** Rewiring is not merely unnecessary, it is contraindicated.

## What

`ops/wq2039-signal-engine-retire/`:

- **`RUNBOOK.md`** — evidence, decision, two-step fix, verify, rollback, handoff.
- **`signal_engine.diff`** — makes the script a logged no-op. **This is the
  retirement, and it needs no root.**
- **`remove-units.sh`** — root tidy-up: disable/remove both units and the
  cron-stamp drop-in, clear the dead-man stamp, prune the `deadman_state.json`
  row, register in `known-removed.txt`, re-snapshot unit-drift.
- **`units/`** — both unit files and the drop-in preserved verbatim for rollback.

Plus a formal record in `docs/fleet/retired_services.yaml` (`name:
signal-engine`) that states plainly it is a decision, not a host state, until
wq#2041 applies it — and an incidental fix to that file, below.

## Why

Three independent reasons, any one sufficient.

1. **The product it feeds was deleted for legal reasons.** Every prompt it
   emits asks for publishable marketing copy — "Publication-ready content brief
   Lumen's post-writer can execute immediately" (`signal_engine.py:219`), "What
   affiliate products or tools are relevant" (`:215`), "rank above theirs"
   (`:251`). Minerva's own invariants
   (`ops/wq2019-proposal-reviewer-fix/minerva_proposal.py:99-102`) read:
   "Content policy: NO publishing of any kind. Ghost CMS was permanently
   deleted 2026-05 for legal reasons." Rewiring this producer into a live queue
   would spend fleet inference generating exactly the artifact class that
   policy forbids.
2. **The proposed sink already has two healthy producers.**
   `/home/lumen/scratch/research_prompts/` is fed by `sia-news-pull`
   (`bin/sia-news-pull:6-7`, the `news_*` lane) and `lumen-scout`, and drained
   by the three fleet researchers, which write research notes — explicitly "NOT
   blog posts" — not content briefs.
3. **Rewiring costs a credential Lumen does not hold.** Reddit's public JSON is
   closed to datacenter clients; the largest source would need a registered
   Reddit app on Frank's account.

Every source re-probed 2026-09-19:

| source | probe | result |
|---|---|---|
| Reddit, 10 subs | `hot.json` with the script's UA | **403** — the fail-open `continue` at `:99-100` renders ten dead subs as one cheerful `Reddit signals: 0` |
| HackerNews | Algolia | **new root cause** — the query uses boolean `OR`, which `advancedSyntax=true` (visible in the API's echoed `params`) does not support, so every word becomes required. `query=homelab OR ollama` → `nbHits 0`; `query=homelab` → hits. `fetch_hn_signals` has returned `[]` for its entire life, independent of the dead sink. DSH_EVAL read this as a dead source. |
| competitor RSS | 5 feeds | technotim 200 · noted.lol 200 · linuxserver **404** · christianlempa **404** · jims-garage **unreachable** |

## How

The two steps are ordered deliberately and **step 1 needs no root**. Four
unit-retirement tickets (wq#1643, #1656/1762, #1778/1779) are already stranded
because no lane can run privileged `systemctl` or write root-owned unit files
(ISSUE-LOG #377, #378). This retirement is built not to become the fifth:
applying `signal_engine.diff` makes the engine inert on its own — the timer may
keep firing, and the unit exits 0 having done nothing and contacted nothing.
That is the same "script becomes a fast no-op instead" shape wq#1643 used for
`split_watch.py`. `remove-units.sh` is tidy-up that may strand harmlessly.

Removing the timer cannot page: `cron_deadman.py` derives each stamp's
threshold from `systemctl list-timers`, so with no timer `intervals.get(unit)`
is `None` → `continue` (`ops/wq1184-gatus-health-checks/cron_deadman.py:110-115`).
The stamp and the `deadman_state.json` row are cleared as tidy-up, not as alert
suppression.

**Incidental fix.** Writing entry 6 turned up that the fleet's formal
retirement record has never been valid YAML — `yaml.safe_load` raises `mapping
values are not allowed here` at line 57 on `dafd943` and every earlier
revision, from an unquoted `: ` in a multi-line plain scalar. The file's header
says retirement-audit tooling (wq#1644) "should read this file before flagging
a guard target as stale"; nothing does yet, which is why the breakage survived
five entries. Fixed by quoting two `guards` keys and moving one to a `>` block
— no wording changed — and guarded by a test.

## Testing

- `python3 -m pytest ops/tests -q` → **27 passed** (4 new in
  `ops/tests/test_retired_services_yaml.py`).
- The new test fails against `HEAD:docs/fleet/retired_services.yaml`
  (`mapping values are not allowed here`, line 57) and passes after the fix.
- Both `signal_engine.diff` hunks validated: old/new line counts match their
  `@@` headers; context copied verbatim from the live file. A machine-verified
  `diff -u` was not possible from a build lane (`/home/lumen/agent` is outside
  `--add-dir`), so the apply instructions require `patch --dry-run` then
  `python3 -m py_compile` — logged as ISSUE-LOG #437.
- The `deadman_state.json` prune snippet was extracted and run twice against a
  fixture: prunes, then reports "already pruned", exit 0 both times.
- Reddit/HN/RSS probes in the table above are reproducible with `curl`, no auth.

## Risk / rollback

Low. Nothing in this PR touches a live file — it ships a decision, artifacts
and a docs fix; the host change is wq#2041. The engine keeps running exactly as
it does today until someone applies step 1.

Rollback of the eventual apply: reverse-apply `signal_engine.diff` with `patch
-R` (or restore the `.bak.<ts>` lumen-change-safety copy), and restore both unit
files from `ops/wq2039-signal-engine-retire/units/` to `/etc/systemd/system/`,
regenerate the drop-in with `lumen-cron-stamp-dropin-gen`, `daemon-reload`,
`enable --now` the timer, drop the `lumen-signal-engine.*` lines from
`known-removed.txt`, re-run `unit-drift snapshot`. Nothing downstream depends
on the unit, so rollback is safe but pointless without first fixing the sink
and all three sources.

**Not done here, deliberately:**

- **`lumen_self_healer.py:57` (`:8099`)**, the ticket's "second copy" — **not
  re-filed**. wq#1112 already cleaned the live `healer_watch_table.yaml` (the
  baked `FALLBACK_TABLE` is reached only if that YAML is unreadable at startup),
  wq#1643 wrote the fix, and **wq#1739** is open with the reviewed diff. A second
  ticket for it would be the duplicate-producer problem this ticket is about.
- **The apply** — `/home/lumen/agent` is deliberately not grantable to a build
  lane (live `.env` tokens, `harness/fleet-exec-policy.yaml:66`). Filed as
  **wq#2041** `[do-not-claim]` for an orchestrator session under
  lumen-change-safety.
- **Minerva's `LUMEN_STATE_FALLBACK`** still lists `lumen-signal-engine`,
  `lumen-prompt-engine` and `lumen-persona-engine` as live pipelines while
  stamped "verified 2026-09-18" — 21 days after two of them stopped existing.
  Live file is outside this repo; filed as **wq#2042**.

Class note: both stale artifacts found here are **fallbacks nothing exercises**
— the healer's baked table and Minerva's state fallback. A table read every
cycle gets corrected; one read once a year does not. Logged to ISSUE-LOG
#435-437.

Refs: wq#2039

🤖 Generated with [Claude Code](https://claude.com/claude-code)
