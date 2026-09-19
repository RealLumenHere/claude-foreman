# wq#2039 — lumen-signal-engine: retire (not rewire)

`lumen-signal-engine.timer` has fired daily at 06:00 UTC since May and exited 0
every time. Submitted prompts by month: May 68 · Jun 1 · Jul 3 · Aug 1 · Sep 0.
Green unit, zero forward progress — the dead-green shape from
`research/DSH_EVAL.md` §7.

This ticket was scoped "design first; decide retire vs rewire to the
`research_prompts` queue." The decision is **retire**. Rewiring is not merely
unnecessary, it is contraindicated — see §Decision.

## Evidence

All re-verified 2026-09-19 from this lane unless noted.

### The sink is gone

`signal_engine.py:20` posts every prompt to `http://localhost:8099/vip` with a
static token (`:21`). `lumen-vip-api` was retired 2026-08-28 with the 8-unit
research mill; unit files parked in `/etc/systemd/system/DISABLED-research-mill.bak/`
(`docs/fleet/retired_services.yaml` → `name: research-mill`, ticket wq#1112,
found by wq#1099). `submit_vip()` (`:77-88`) swallows the resulting
`ConnectionRefusedError` into `log(f"  VIP error: {e}")` and returns False, so
the unit still exits 0.

### Every source is dead or broken

| source | code | probe 2026-09-19 | verdict |
|---|---|---|---|
| Reddit, 10 subs | `signal_engine.py:92-129` | `GET /r/selfhosted/hot.json` with the script's own UA → **403** | dead — Reddit requires OAuth for non-browser clients |
| HackerNews | `:133-169` | Algolia query string `homelab OR self-hosted OR selfhosted OR proxmox OR ollama` → `nbHits: 0`; the single term `homelab` → hits; `ollama` → hits | **broken query**, not a dead API |
| competitor RSS, 5 feeds | `:29-35`, `:173-198` | technotim 200 · noted.lol 200 · linuxserver **404** · christianlempa **404** · jims-garage **000** (no connection) | 2/5 alive |

The HN row is a root cause DSH_EVAL did not have. Algolia applies
`advancedSyntax=true` (visible in the API's echoed `params`), whose grammar has
no boolean `OR` — the bare word is treated as a required term, so the query can
only match a story titled with every one of those words. `fetch_hn_signals` has
therefore returned `[]` since it was written, independent of the sink. Isolated
reproduction, no auth needed:

```
curl -s 'https://hn.algolia.com/api/v1/search?query=homelab%20OR%20ollama&tags=story&hitsPerPage=3'  # nbHits 0
curl -s 'https://hn.algolia.com/api/v1/search?query=homelab&tags=story&hitsPerPage=3'               # hits
```

Reddit's 403 is fail-open by construction: `:99-100` `continue`s on any non-200
without logging, so ten dead subreddits render as one cheerful
`Reddit signals: 0` line.

## Decision — RETIRE

Three independent reasons, any one sufficient.

**1. The product it feeds was deleted for legal reasons.** Every prompt this
engine emits demands publishable marketing copy: "Publication-ready content
brief Lumen's post-writer can execute immediately" (`:219`), "What affiliate
products or tools are relevant" (`:215`), "What affiliate or monetization angle
did they miss" (`:252`), "rank above theirs" (`:251`). The standing content
policy, carried in Minerva's own invariants
(`ops/wq2019-proposal-reviewer-fix/minerva_proposal.py:99-102`), reads: "Content
policy: NO publishing of any kind. Ghost CMS was permanently deleted 2026-05 for
legal reasons. Research outputs MUST become integration proposals, never
published content." Rewiring this producer into a live queue would spend fleet
inference generating exactly the artifact class that policy forbids. That alone
settles retire-vs-rewire.

**2. The proposed sink is already served by two healthy producers.**
`/home/lumen/scratch/research_prompts/` is drained by
`lumen-fleet-{cerebras,mistral,openrouter}`
(`lumen_fleet_researcher.py:57-60`), which write research notes — explicitly
"NOT blog posts" (`minerva_proposal.py:116`) — to `/home/lumen/scratch/research/`.
It is already fed by `sia-news-pull` (`bin/sia-news-pull:6-7`, the `news_*`
lane, which caught dsh on launch day) and by `lumen-scout`
(`lumen_scout_graph.py`, ≤30 gap-analysis prompts/day, timer active —
`research/TARGETED_CRAWL_SOURCE_RANKING.md:58`). A third producer with a 403'd
primary source, a query-broken secondary and 2/5 live feeds adds nothing those
two do not already do better.

**3. Rewiring costs a credential Lumen does not hold.** Reddit's public JSON is
gone for datacenter clients; restoring the largest source means a registered
Reddit app on Frank's account. That is the physical-act class — it would be
queued as a physical task, never as a gate — but it is a real cost to weigh, and
reasons 1 and 2 mean the thing it would buy is unwanted.

Salvage is recorded rather than built: the HN Algolia `OR` defect and the three
rotted feed URLs are written down here so that if a live-signal need ever
returns, the next lane starts from the root cause instead of rediscovering
`signals: 0`.

## Fix

This is a build lane. `/home/lumen/agent/` is outside its `--add-dir` scope
(orchestrator-only, "do under lumen-change-safety" —
`reports/fleetops_backlog_broker_2026-09-08.md:24`, `kb/ISSUE-LOG.md` #240) and
`systemctl disable/mask` is on the house deny-list (`harness/guard.sh:92-93`).
So this directory ships the artifacts and hands the privileged apply off, the
same shape wq#1643 → wq#1739 used.

The two steps are ordered deliberately, and **step 1 needs no root**. Three
unit-retirement tickets (wq#1643 split-watch, wq#1656/1762 rerank-thread-watch,
wq#1778/1779 dgm) are currently stranded because no lane in the fleet can run
privileged `systemctl` or write root-owned files in `/etc/systemd/system`, and
fleet-exec has no root-escalation verb (`kb/ISSUE-LOG.md` #377, #378;
`harness/fleet-exec-policy.yaml:170-176`). This retirement is deliberately
built not to become the fourth: the script no-op alone makes it inert, and that
needs only `/home/lumen/agent` write scope.

1. **`signal_engine.diff`** (this directory) — short-circuits `__main__` to a
   logged no-op. The timer may keep firing; the unit exits 0 having done
   nothing and contacted nothing. This is the retirement in effect, the same
   "script becomes a fast no-op instead" shape wq#1643 used for `split_watch.py`
   when the timer could not be disabled. Needs a lane with `/home/lumen/agent`
   in scope (orchestrator, under lumen-change-safety) — **no root**.
2. **`remove-units.sh`** (this directory) — root-run tidy-up. Disables and
   removes `lumen-signal-engine.{timer,service}` and the generated drop-in
   directory, clears the dead-man stamp, prunes the `deadman_state.json` row,
   registers both units in `/home/lumen/fleet-units/known-removed.txt`, and
   re-snapshots the unit-drift baseline. Safe to run any time after step 1;
   if it strands like its three predecessors, the engine is still inert.
3. **`docs/fleet/retired_services.yaml`** — formal record, `name: signal-engine`,
   carrying the apply status honestly (a decision, not a host state, until
   step 1 lands).
4. **Follow-up ticket** for the apply (§Handoff).

### Landed here: retired_services.yaml did not parse

Writing entry 6 turned up that the file had never been valid YAML. At `dafd943`
and every revision before it, `yaml.safe_load` raised `mapping values are not
allowed here` at line 57 — a `guards` item written as a multi-line *plain*
scalar with a `: ` in its continuation (`...unreadable at startup): missed by`).
The file's own header says retirement-audit tooling (wq#1644) "should read this
file before flagging a guard target as stale"; nothing does yet, which is
exactly why the breakage survived five entries. Fixed by quoting the two
offending `guards` keys and moving the long one to a `>` block — no wording
changed — and guarded by `ops/tests/test_retired_services_yaml.py` (4 tests,
parse + required fields + duplicate-name fold + this entry's apply-status row).

### Dead-man switch: no page on removal

`lumen-signal-engine.service.d/99-cron-stamp.conf` wires
`ExecStartPost=-/home/lumen/bin/lumen-cron-stamp %n`, so
`/home/lumen/logs/.stamp.lumen-signal-engine` exists and `cron_deadman.py`
(wq#1184) tracks it. Removing a timer whose stamp file survives does **not**
page: `check()` pairs each stamp against a cadence derived from
`systemctl list-timers`, and with the timer gone
`intervals.get(unit)` is `None` → `continue`
(`ops/wq1184-gatus-health-checks/cron_deadman.py:110-115`). The stamp and the
`"lumen-signal-engine": "ok"` row in `deadman_state.json:80` are therefore
cosmetic leftovers, not alerts. `remove-units.sh` clears both (non-fatally — a
tidy-up failure must not leave the unit half-removed).

While only step 1 has landed the timer still fires daily and still stamps, so
the dead-man stays green and correctly reports "this unit ran." It ran and did
nothing, which is now the intended behaviour.

## Verify

After step 1 (`signal_engine.diff`):

```
python3 -m py_compile /home/lumen/agent/signal_engine.py
python3 /home/lumen/agent/signal_engine.py   # prints the RETIRED line, exits 0, no network
grep -c 'VIP error' /home/lumen/scratch/signal_engine.log   # stops growing after the next 06:00 UTC fire
```

After step 2 (`remove-units.sh` — it self-checks and exits non-zero on failure):

```
systemctl status lumen-signal-engine.timer     # "could not be found"
systemctl status lumen-signal-engine.service   # "could not be found"
grep -qxF lumen-signal-engine.timer /home/lumen/fleet-units/known-removed.txt
/home/lumen/bin/unit-drift check --digest      # no unexpected drift
```

Liveness probe for the follow-up ticket — step 1, because that is the one that
makes the engine inert and the one a lane can actually reach:
`file_contains(/home/lumen/agent/signal_engine.py, RETIRED 2026-09-19)`

## Rollback

Unit files are not preserved by `remove-units.sh` — they are reproduced verbatim
in `units/` in this directory (captured from `systemctl cat` before the
change). Restore by copying both back to `/etc/systemd/system/`, recreating the
drop-in with `lumen-cron-stamp-dropin-gen`, `systemctl daemon-reload`,
`systemctl enable --now lumen-signal-engine.timer`, removing the
`lumen-signal-engine.*` lines from `known-removed.txt`, re-running
`unit-drift snapshot`, and reverse-applying `signal_engine.diff` with `patch -R`.
Nothing downstream depends on the unit, so rollback is safe but pointless
without first fixing the sink and all three sources.

## Handoff — needs a lane this one is not

| item | why this lane can't | ticket |
|---|---|---|
| apply `signal_engine.diff` (**the retirement**) | `/home/lumen/agent/` outside `--add-dir` (`kb/ISSUE-LOG.md` #240); no root needed | **wq#2041** step 1 |
| run `remove-units.sh` (tidy-up) | `systemctl disable/mask` denied (`harness/guard.sh:92-93`); root | **wq#2041** step 2 |
| `lumen_self_healer.py` `FALLBACK_TABLE` still names `lumen-vip-api` + port 8099 (`:52-57`) | already owned — wq#1643 prepared the exact diff, **wq#1739** (p1, open) applies it | **wq#1739**, not re-filed |
| Minerva `LUMEN_STATE_FALLBACK` still lists `lumen-signal-engine`, `lumen-prompt-engine`, `lumen-persona-engine` as live pipelines (`minerva_proposal.py:113,117`) — live file is `/home/lumen/minerva/`, not this repo | outside this repo and this lane (`/home/lumen/minerva` is not grantable — wq#1467) | **wq#2042** |

The ticket's own "second copy" item — `lumen_self_healer.py:57` port-checking
:8099 — is deliberately **not** duplicated here. wq#1112 already cleaned the
live `healer_watch_table.yaml` (the fallback is reached only if that YAML is
unreadable at startup), wq#1643 wrote the fix, and wq#1739 is open to apply it
with a reviewed diff in `ops/wq1643-split-watch-retire-agent5/healer_fallback_table.diff`.
Filing a second ticket for it would be the duplicate-producer problem this
ticket is about.

## Class note

Both stale artifacts found here are **fallbacks nothing exercises**: the
healer's baked `FALLBACK_TABLE` and Minerva's `LUMEN_STATE_FALLBACK`. Each was
correct when written, each is reached only when its live source fails, and so
each drifts silently — `LUMEN_STATE_FALLBACK` is stamped "verified 2026-09-18"
and still names three units retired on 08-28. A live table that is read every
cycle gets corrected; a fallback read once a year does not. Logged to
`kb/ISSUE-LOG.md`.
