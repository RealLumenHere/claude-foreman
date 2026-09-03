# wq#1399 — Adversarial verify-pass of wq#1396 (minerva proposal -> wq ticket promoter)

**Verdict: CONTRADICT** — the mechanics (idempotency, dedup, crash-recovery, wq-add
integration) are correct and I reproduced all 14 shipped tests passing against the real
`wq` binary. But the promoter treats every `proposals.status='staged'` row as
promotable, and `staged` is not a neutral "ready" state — it is set exclusively by
`minerva_factory.py`'s `stage_yellow()` for plans minerva's own GATE step classified
**YELLOW or RED** (never GREEN/auto-apply) and parked pending a human-touched
`.approved` file. The promoter files these as ordinary, non-gated `build` lane wq
tickets, which a BUILD worker (an agent running under the fleet's standing
full-autonomy CLAUDE.md — no Frank-gates, decide-and-act) will simply execute. That
routes changes minerva_factory's own code labels `'RED lane - Frank required'` (and
YELLOW changes awaiting Frank's approval-file gate) straight to unattended execution,
bypassing the fleet's one existing safety gate for this class of change. Not a
hypothetical: 61 real `staged` proposals sit in `minerva.db` today, none installed but
merged to `main` and ready to fire on the next `install.sh` + `minerva-proposal-promoter.timer`
cycle.

## What was shipped

PR http://100.121.17.5:3000/lumen/claude-foreman/pulls/277 (merged, commit `c123f1f`,
7 files, `ops/wq1396-minerva-proposal-promoter/`): `minerva_proposal_promoter.py` reads
`minerva.db::proposals` for rows in status `deploy_ready` or `staged` with no `ticket`
set, files a `wq add --lane build --archetype standard-engineering` ticket per row
(security/CVE-flavored content -> `--pri 1`, else `--pri 3`), and writes the new ticket
id back to `proposals.ticket`. Idempotent two ways (ticket-set skip; marker-in-detail
backfill on crash). Installed via `install.sh` + a systemd timer firing 15 min after
each `minerva-eval.timer` slot.

## Mechanics — CONFIRMED

Ran the shipped suite directly: `python3 ops/wq1396-minerva-proposal-promoter/test_minerva_proposal_promoter.py`
against the real `/home/lumen/bin/wq` and scratch DBs — **14/14** checks pass (repo
README claims "13/13"; harmless miscount, not a defect). Spot-checked the two regexes
against the real `wq` source rather than trusting the tests alone:
- `ADDED_RE = r"added task (\d+)"` matches `wq`'s actual add-success print
  (`/home/lumen/bin/wq:1011`: `print(f"added task {...} [{a.lane}]"`).
- `DUPLICATE_RE = r"DUPLICATE — task (\d+)"` matches `wq`'s actual dedup print
  (`/home/lumen/bin/wq:996`).
- `wq add`'s own guardrails (unknown lane/archetype refusal, `bin/wq:960-990`) print
  `REFUSED:` and match neither regex, so `file_ticket()` correctly falls through to its
  `FAILED to file ticket` branch rather than mis-reporting a refusal as success — checked
  `build`/`standard-engineering` are in fact registered (`/home/lumen/foreman/dispatcher/config.json`,
  `/home/lumen/foreman/research/models.yaml:199`), so this path isn't hit today, but the
  failure handling is right if it ever is.
- `proposals` schema columns the script reads (`proposed_change`, `rollback`, `risk`,
  `source_intel_id`, `current_state`, `created_at`) all exist in the live
  `/home/lumen/minerva/minerva.db` (`PRAGMA table_info(proposals)`); `ticket` does not
  yet exist there — `ensure_ticket_column()`'s `ALTER TABLE` is live-untested but is a
  one-line, well-trodden pattern.

## Policy gap — CONTRADICTED

`minerva_factory.py` (`"production factory for deploy_ready proposals"`) is the
system's **existing** consumer of `deploy_ready` proposals: PLAN -> GATE
(deterministic GREEN/YELLOW/RED lane classification) -> CANARY APPLY. Per its own
header doc (`minerva_factory.py:12-17`):

```
GREEN  auto-apply whitelist: litellm_route_add, new_standalone_script (inert, ...)
YELLOW everything else plannable: staged with LLM draft diff + approval file
       (reuses lumen_exec approval convention: touch <id>.approved) + ntfy.
RED    never auto: reboot/netplan/MAC/vault-HA/dd/off-limits/no-rollback.
```

`stage_yellow()` is the *only* place in the codebase that sets
`proposals.status='staged'` (grepped every `.py` under `/home/lumen/minerva`):

```
c.execute("UPDATE plans SET status='staged' WHERE id=?", (plan_id,))
c.execute("UPDATE proposals SET status='staged' WHERE id=?", (row['proposal_id'],))
```

and `apply_plan()` refuses to act on a YELLOW plan without a touched approval file
(`YELLOW - needs approval: touch {ok_file}`) and refuses a RED plan outright
(`'RED lane - Frank required: ' + ...`). So `proposals.status='staged'` means, by
construction, "minerva already decided a human must sign off (or it's RED and can
*never* auto-apply) before this happens" — not "ready to go."

Live evidence against `/home/lumen/minerva/minerva.db` (read-only, via `sqlite3.connect(..., uri=True, mode=ro)`):
- 61 proposals currently sit at `status='staged'` (plus 1 `deploy_ready`) — accumulated
  history, not a fresh batch.
- Joining to `plans`: of the staged proposals with a linked plan, lanes split
  **65 YELLOW / 18 RED, 0 GREEN** — GREEN never lingers at `staged` because it
  auto-applies immediately, so every `staged` row this promoter would pick up is, by
  definition, either explicitly Frank-gated or explicitly Frank-only.
- Concrete example: proposal `55826e11e443` ("Upgrade the DesktopCommanderMCP package
  to v0.2.48 on bes and nephthys, and re-apply any local dist patches if needed", M
  risk) has plan `8a2fd3ae7185`, `lane='yellow'`. Its approval request
  `/home/lumen/instructions/approvals/F8a2fd3ae7185.md` exists; there is no
  `F8a2fd3ae7185.approved` — i.e. still pending, unapproved, today.
- RED-lane examples at `staged` today include proposals for new always-on services
  (`change_type='new_service'`, e.g. `21454c8e2f63` "Implement learned
  reactive-vs-planning router selector") — `minerva_factory`'s own gate classified
  these RED and its `apply_plan()` refuses them unconditionally without Frank.

The promoter's `wq add` call passes no `--gate`/`--gate-type`/`--frank-gated` (all
present on `wq add`, `bin/wq:2212`) — every ticket it files is a plain, ungated `build`
ticket. Under this fleet's own standing BUILD-worker protocol (full autonomy, no
Frank-gates, decide-and-act — this verify session's own prompt), an agent claiming one
of these tickets would just do the described infra work (SSH to `bes`/`nephthys` and
run the upgrade, wire up the new service, etc.) with no code review checkpoint, since
most of these changes are direct fleet ops, not repo PRs. That is a real bypass of the
one approval mechanism minerva_factory was built with, not a theoretical one — the
ticket itself hedged this exact spot ("deploy_ready (and maybe staged after a gate)")
and the shipped diff added no gate at all: no lane check, no `plans` table query, no
`--frank-gated`, and no mention of any of this in the PR's own README.

## Disposition

Minimal fix, two options, either avoids re-implementing minerva_factory's own gate
logic:
1. Drop `staged` from `PROMOTABLE_STATUSES` entirely — `minerva_factory`'s
   `stage_yellow`/`apply_plan` path already owns staged proposals end-to-end (approval
   file -> `apply_plan(approved=True)`); a wq ticket is redundant with, and races,
   that existing flow. Only `deploy_ready` (proposals minerva hasn't yet planned/gated
   at all) needs a promoter.
2. If tracking `staged` proposals as wq tickets is still wanted (e.g. so a human sees
   them on the board), join `plans` on `proposal_id`, and for any row whose lane is
   `yellow` or `red`, file with `--frank-gated` (`bin/wq:2212`, implies
   `gate_type=frank_decision`, `status=frank_gated`) instead of a plain open ticket, so
   a BUILD worker cannot claim and execute it without a `wq decide`/`answer`/`review`
   round trip first.

Either is a small, additive change to `promote()`/`file_ticket()` — no schema change,
no rework of the parts that are already correct (idempotency, dedup, crash-recovery,
CVE classification, all confirmed above).

Escalating per archetype-8 protocol: contradiction → opus-5@high.
