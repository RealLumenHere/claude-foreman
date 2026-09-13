# wq#1896 — Adversarial verify-pass of wq#1883 (despag Phase-2 wiring spec)

**Verdict: CONFIRM.** PR #566 (commit `ff78daa`, now `main`) — `despag/decision/PHASE2_WIRING_SPEC.md`
(479 lines) — does what it claims: it validates Frank's five Phase-2 decisions with file:line
evidence, corrects two line-references in the source tickets, and surfaces one genuine
prerequisite (§B.6) that wq#1888 needs before it can be implemented as written. Read the
document cold, then independently re-checked essentially every file:line citation against the
actual source rather than trusting the doc's own quotes. Two small, non-load-bearing citation
errors found (below); no error changes any of the five verdicts or the RECOMMENDATION.

## What was checked

This is a docs-only ticket (no production code changed; `acceptance.json`/`evidence.json`
retargeted to wq#1883, AC1 = a citation criterion). Re-ran the harness's own evidence gate
(`harness/acceptance.py:verify`) against the PR's actual parent commit (`ff78daa~1`, since
`origin/main` has since fast-forwarded past the PR itself, which makes the naive check
against `origin/main` report a false "not touched by diff" — a base_ref artifact, not a
real defect): **AC1 passes**, citation lands inside the diff.

Then, line-by-line, verified against the current tree (spot-checks unless noted "all"):

- **§A (deploy, wq#1887)** — `ops/systemd_desired_state.py:119-130` (`staged_paths`), `:127`
  (`"@" in b or not b.startswith("lumen-")`), `:145-163` (`staged_drift`), `:112-117` comment —
  all exact. `despag/app.py:367-395` (`GET /v1/uhp` discovery), `:369-379` capability flags,
  `:394` `body["lumen"]` — all exact. `app.py:106-107` (`DESPAG_DATA_DIR`, `CONTROL`) — exact.
  `kb/ISSUE-LOG.md` rows **#360** and **#375** — both exist verbatim as cited.
- **§B (fworker/gateway, wq#1888)** — `harness/fworker.py`: `run_child` spans exactly
  **1208-1362** (doc's ticket-cited `:1219` really is inside its docstring, confirmed), call
  sites at `:1452-1453`/`:1787-1790`, return tuple at `:1362`. `harness/classifier.py:155-278`
  table (ALL 11 rows checked): every cited sub-range (`161-165`, `202-210`, `212-225`,
  `229-234`, `236-248`, `253-262`, `264-265`, `267-270`, `271-275`, `276-278`, `187-199`)
  matches the real frame-shape handling exactly, and `:331` / `:358-360` / `:285-286` are
  character-exact quotes. `despag/uhp/translate.py:31-67` docstring and kind list, `:66` result
  mapping, `:48-51` tool_use mapping, `:241-249` `fail()` hardcoding `harness_error` — all
  exact. `fworker.py:1213-1215` PRIMARY-liveness contract, `:1271` frame-gap fire condition,
  `:1299` `last_event` stamp, `:269` `INACTIVITY_TIMEOUT_S` default 240 — all exact.
  `despag/app.py:497-510` `_sse_gen` unconditional 20s keep-alive — exact, and genuinely would
  mask a hung backend from a naive client as the doc claims. `despag/admission.py:170-183`
  `LimitObserver` (the load-bearing §B.6 claim that "the gateway already computes the missing
  state") — exact: it really does feed a `classifier.RunState()` per turn. Checked the one
  gap this implied — whether `exit_code` (also listed as a field the gateway "already has") is
  actually available anywhere in-process, since §B.5 says `exit_code` has no UHP analogue — and
  found `despag/runner/turn.py:238,241-245` computes and 128+signum-normalizes it internally
  (mirroring `fworker.py:1335-1340`), so the claim is internally consistent, not contradictory.
  `admission.py:186-223` `check_admission`, `:201-203`/`:213-216`/`:217-219`, `:270`
  `burn_rate.FRANK_RESERVE_PCT` — exact.
- **§C (Frank-active, wq#1889)** — `bin/wq:332-341` (`agent_sessions`/`agent_activity` DDL),
  `:607-616` (`mark_alive`/`ping`), `:102` (`DEFAULT_TTL_MIN=240`), `:111`
  (`WQ_STALE_MIN=45`), `:834-844` (`purge_stale_sessions`, three correlated `NOT IN`
  subqueries against `tasks`/`slocks`/`slocks_ro`) — all exact. **The correction is real**:
  `dispatcher/burn_rate.py:115-117` does apply `FRANK_RESERVE_PCT` inside `pool_spend_rate`
  (per-call), while `allowed_concurrent` (a distinct function) begins at `:122` and only
  consumes the result — confirmed exactly, so wq#1889's own ticket text pointing at `:122` was
  wrong and the spec's fix is correct. `:54-58` (module-level `FRANK_RESERVE_PCT`, import-time)
  — exact. `:561-562` `ACTIVITY_KEEP` trim — exact.
- **§D (NO list)** — `despag/app.py:369-379` capability flags — exact (shared with §A). The
  external `harnessrouter-ref` citations (`HR/gateway/app.py`, `HR/runner/server.py`) live
  outside this worker's bounded filesystem, same as §A.4's own admitted restic gap — but the
  wq#1883 author's session left snapshot copies under
  `/home/lumen/foreman/_runs/wq1883/snapshots/`, which are inside my allowed dirs. Checked
  against those: **line counts match exactly** (`gateway/app.py` 14121 lines, `runner/server.py`
  5310 lines, vs. the doc's stated 14,121 / 5,310). `gateway/app.py:13368/13910/13925/14094/14109`
  (harness CRUD routes), `:346` (public-nginx-proxies comment), `:7897-7903` (`_share_out`
  `url = f"/share/{token}"`) — all exact. `runner/server.py:105-113`/`89-101` (uid write-wall
  mutual-exclusion + root-required raises), `:18-32` (codex/hermes/dsh provider docs) — all
  exact.
- **§E (queue.db)** — `despag/backing/control_sqlite.py:82/113/168` (`idem_reserve`,
  `idem_settle`, `lease::{sid}`) — exact. `backing/__init__.py` Protocol-seam docstring — exact.
  `bin/wq:414-420` lease-fence feature-detection comment, `bin/lumen-wq-health:13-15`
  (`sqlite3 ... 2>/dev/null` runaway query) — both exact, and independently confirmed
  **no file under `/home/lumen/bin/` references `control.db` by path** (`grep -rl control.db
  bin/` — zero hits), backing the "no external script reads control.db by path" claim.

## Two minor errors found (neither blocking, neither changes a verdict)

1. `control_sqlite.py:50-53`'s paraphrase gives the `control` table's columns as
   `(item_id, body, expires)`. The real DDL (line 50-51) is
   `id TEXT PRIMARY KEY, body TEXT NOT NULL, etag INTEGER NOT NULL, expires REAL` — the
   primary-key column is `id` (callers pass constructed doc-ids like `idem::{key}` as the
   *value*, `item_id` is a parameter name, not a column name), and `etag` is dropped from the
   description entirely. Doesn't affect the surrounding argument (single generic
   key/value/TTL document table) — just an imprecise quote.
2. §E.5's "at least ten files under `/home/lumen/bin/` touch [`alerts.db`]" list names
   `alert-deadman-beat.sh` and `deadman-killtest.sh`. Read both in full: neither touches
   `alerts.db` at all — `alert-deadman-beat.sh` only calls `ntfy-send` and posts to
   `deadman.lumennode.uk`; `deadman-killtest.sh` only calls `ntfy-send`/`curl` against the same
   external host and logs to the `alert-queue/` directory (not the db). The other six named
   files (`aq_escalate.py`, `ntfy-send` via `aq_gate.py`, `lumen-load-watch` via `aq.db()`,
   `handoff`, `lumen-sqlutil`, `fleet-status`) do genuinely touch it, plus 3 (not 2) matching
   test files by grep. Real count is ~9, not "at least ten," and two of the eight named files
   are misattributed. The caveat's actual point — alerts.db has real external readers and a
   silent-failure risk, so it's not a small migration step — still holds on the corrected list.

## Bottom line

Every load-bearing citation across A/B/C/D/E — including the one the whole RECOMMENDATION
hinges on (§B.6's `admission.py:170-183` `LimitObserver` already computing the state the
translator drops) — checked out exactly against the source, including against an external
reference repo via the author's own snapshot artifacts. The two corrections found (a
paraphrased schema and a two-file misattribution in a supporting-evidence list) are cosmetic.
Confirming wq#1883 as delivered.
