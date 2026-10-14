# wq#3842 — judgment: wq#3841 vs wq#3839 (n8n-watcher guards justification)

**Ruling: wq#3841 is upheld on all three points.** The retirement action
stands; the written justification was false and is now corrected. The
"0 workflows" carry-forward is no longer a carry-forward — it is freshly
confirmed by n8n itself, same day as the retirement.

## 1. `docs/fleet/retired_services.yaml` n8n guards text — CORRECTED (upheld)

The contradiction is real, and the false clause was the load-bearing one.
The entry argued that disabling `n8n-watcher.service` drops no alerting path
*because the topic-mismatch bug was already silencing it*. That bug
(`lumen_alerts` underscore vs the subscribed `lumen-alerts`) was fixed a
month before the retirement:

- `/home/lumen/agent/n8n_watcher.py:17-19` — comment naming wq#1645 and the
  fix; `:36` — `[NTFY_SEND, "-t", "lumen-alerts", ...]`.
- `journalctl -u n8n-watcher.service`, Oct 07 23:38:39 — `ntfy sent: n8n
  Watcher Online`, exit 0, no failure warning. The alert path was *working*
  on the watcher's last start.

So the watcher was not silenced; it was silent because it had nothing to
say. Same journal line and the watcher's own state file prove that
independently:

- Oct 07 23:38:39 — `Seeked to end of log (0 lines)`. That message is only
  reachable when `EVENT_LOG.exists()` is true (`n8n_watcher.py:110-114`), so
  `/home/lumen/.n8n/.n8n/n8nEventLog.log` existed and held **zero lines**.
- `/home/lumen/agent/n8n_watcher_state.json` still reads `last_pos: 0`, and
  its mtime is 2026-10-14 02:41:45 — one minute before the unit's final stop
  at 02:42:07. `save_state()` runs every 60 s unconditionally
  (`n8n_watcher.py:116-122`), so `last_pos: 0` at the last write means the
  event log never gained a line across the whole 6-day run.

The corrected guards text states that reason and records the error, so the
next reader sees why the entry changed rather than silently inheriting a
fixed version of a false claim.

**Why this was worth a follow-up edit rather than a note:** this file is a
*formal record* — its own header asks retirement-audit tooling to read it
before flagging a guard as stale. A false justification here survives every
future reader who trusts the record instead of re-deriving it, which is the
entire point of writing it down.

## 2. `research/FLEET_SERVICE_ATLAS.md` — ANNOTATED, not rewritten (upheld, narrowly)

The atlas is a self-labelled dated capture: "_captured 2026-09-10 ... Hand-
written sections (§1, §3–§7) go stale_". Its present-tense F-6 prose was
therefore *true as written*, and in practice the file has never been
maintained as findings closed — only F-11 (closed one day after capture)
carries a closure annotation, while F-1/F-4/F-8 and others landed fixes
without one. A full F-1…F-18 back-annotation sweep is a different job from
this ticket and would be partly stale again next week.

So: the minimal fix that prevents recurrence, at the exact sites with
demonstrated harm. Following the F-11 convention (strikethrough + bold
closure note), annotated the four F-6 prose sites plus the findings row —
§0 headline 7, §2 `lumen-alert-queue` row, §5 `n8n-watcher` row, §6
"active but wrong" line, §7 F-6 — and added one header rule: look an F-id up
in the §7 table and check its ticket before citing it as current,
*especially* into a permanent record. That rule is the generalisable lesson;
the systematic sweep of the remaining findings is filed as **wq#3843**
(pri 3, mechanical-edit). Class logged as ISSUE-LOG row 1298.

## 3. The "0 published workflows" carry-forward — VERIFIED FRESH (gap closed)

wq#3841 flagged this correctly and could not check it (sandbox blocks
`/home/lumen/.n8n`). It is checkable without that path, from n8n's own
journal:

```
Oct 14 00:54:58 lumen n8n[1771360]: Finished building workflow dependency
  index. Processed 0 draft workflows, 0 published workflows.
Oct 14 00:56:23 lumen n8n[1795906]: ... Processed 0 draft workflows, 0
  published workflows.
```

That is n8n reading its own workflow store at startup, twice, under two
hours before the 02:42:07 retirement stop. It confirms 0 published *and* 0
draft workflows — strictly stronger than the wq#1302 claim, and dated the
day of the retirement rather than six weeks earlier. Corroborated by the
empty event log above: nothing was ever created, so nothing ever ran.

Scope limit stated honestly: journald retention only reaches back to Oct 14
for this unit, so this proves "0 workflows at retirement", not "0 workflows
ever created since Sept 3". The latter is still inherited — but it is no
longer the claim the record depends on. The disposition text now cites the
fresh evidence and names what it does and does not cover.

## Acceptance probe (designed to be able to fail)

The fix was pointed at one entry, so the probe was run against the other
nine — the population the fix was *not* designed around. Parsed the yaml and
scanned every entry's `reason`/`data`/`guards` for atlas finding citations:
three other entries cite one (`research-mill` F-4, `lumen-agent` F-4,
`lumen-rerank-thread-watch` F-10). All three state the finding *together
with* its resolution — "Found by wq#1627 (F-4), cleaned by wq#1643",
"flagged it as 'harmless but stale'; this entry is that documentation",
"already self-neutralizes". The probe could have returned two or three more
entries needing the same correction; it returned none. The n8n entry was the
only one that carried a fixed finding forward as a live fact, which bounds
this fix to the one entry.

## Observation, not ruled on (out of scope, worth recording)

`docs/fleet/retired_services.yaml`'s header asks retirement-audit (wq#1644)
to read it, but `/home/lumen/bin/retirement-audit:29` defaults `CONF_PATH`
to `/home/lumen/docs/fleet/retired_services.yaml` — a different path — and
reads a different schema (`kind`/`unit`/`expect`/`node`, plus a `nodes:`
map) than this file's (`name`/`units`/`load_state`/`guards`). So this file is
currently an agent- and human-facing record, not the machine-consumed one.
That does not weaken the correction (a false formal record is worth fixing
either way), but the header overstates its consumer. Noted here; not touched.
