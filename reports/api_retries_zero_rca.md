# wq#1814 RCA: `api_retries=0` on every attempt (Phase-0 E.3)

**Verdict:** `classifier.py` was matching frame shapes the CLI does not emit. There were
two mismatches: the retry frame and the rate-limit frame. Both are patched and tested.
The E.2 re-score finds **0** confirmed CLI-error-text attempts among the 53 re-scorable
FALSE_COMPLETE token-pool attempts. The other 62 cannot be scored from available data.

## 1. What the CLI emits (captured)

- **Installed CLI:** 2.1.268, per `PHASE0_WIRING_SPEC.md:48` (`claude auth status`). This
  session could not re-read it live because the worker allowlist refuses
  `claude --version` (ISSUE-LOG #334).
- **Raw captures available:** fworker's `_runs/wq*/` persist no raw frames (ISSUE-LOG
  #331), so the spec's grep over `_runs/wq*/` has nothing to search. The raw stream-json
  this sandbox can read is 47 files from the pre-fworker harness: 44 in
  `agents/work-queue/_runs/*.jsonl` and 3 in `foreman/_runs/fbuild-*.jsonl`. Their init
  frames report CLI 2.1.233 (45) and 2.1.220 (6), and they date from 2026-08-15 to
  2026-08-19. No 2.1.268 raw frame exists anywhere readable.

**Retry frame.** `agents/work-queue/_runs/rabbitMisty3-e8dfb9.jsonl:4` (CLI 2.1.233):

```json
{"type":"system","subtype":"api_retry","attempt":1,"max_retries":10,"retry_delay_ms":513,"error_status":null,"error":"unknown","session_id":"08694b7c-b0eb-4eaf-ab8a-9955848886a0","uuid":"c152e5e2-0fd0-40ea-ad27-fb324770c3f5"}
```

Ten of these (attempt 1..10) arrive before the result frame at line 15, which has
`subtype:"success"`, `is_error:true`, `terminal_reason:"api_error"` and
`result:"API Error: Unable to connect to API (UNKNOWN_CERTIFICATE_VERIFICATION_ERROR)"`.
The shape agrees with the decompiled 2.1.220 source: `research/CLAUDE_CLI_INTERNALS.md:153`
lists `{attempt, max_retries, retry_delay_ms, error_status, error-category}`, and
`research/RELIABILITY_SPEC.md:10` gives it as a *system subtype* whose category enum is
carried in `error`.

**Limit frame.** `foreman/_runs/fbuild-625.jsonl:2` (CLI 2.1.220):

```json
{"type":"rate_limit_event","rate_limit_info":{"status":"allowed_warning","resetsAt":1787223600,"rateLimitType":"seven_day","utilization":0.57,"isUsingOverage":false},"uuid":"7241b0b5-d639-41fd-ade0-8f9e342103d2","session_id":"61649146-6bec-4421-b732-c5a9be1231d5"}
```

All 74 captured `rate_limit_event` frames use this nested shape (67 `allowed_warning`,
7 `allowed`). **No `rejected` event and no 429/`rate_limit` retry was ever captured.**
The rejected shape is therefore inferred from the same `rate_limit_info` wrapper, not
observed. Whether 2.1.268 still emits these exact shapes is unverified. The patch still
accepts the old flat shapes, so it fails safe either way.

## 2. What `classifier.py` matched

Pre-patch file backed up to `/home/lumen/foreman/_runs/wq1814/classifier.py.pre-wq1814.bak`.

| signal | pre-patch read | wire | effect |
|---|---|---|---|
| retry count | `ftype == "api_retry"` (old L245) | `type:"system"`, `subtype:"api_retry"` | never true, so `api_retries` was always 0 |
| retry category | `frame["category"] == "rate_limit"` (L225) | category is in `error` | never true |
| AUTH via retry (§C11) | nested in the dead L245 block | `error:"authentication_failed"` / `error_status:401` | unreachable in production |
| 429 on any frame | `frame["error_status"] == 429` / `api_error_status` (L227) | top-level | works; the only live retry/limit read |
| rate_limit_event | `rle["status"]` (L204) | `rate_limit_info.status` | never true, so a rejected event was never read and `resetsAt` never captured |

The matcher dates from `ac79dd1` (2026-08-15, T3). The unit-test fixtures were written
in the same flat shapes (`test_classifier.py` `{"type":"api_retry",...}`,
`{"type":"rate_limit_event","status":"rejected",...}`), so the tests passed against
frames the CLI never produces.

## 3. Corroboration from attempt records

This census covers 1383 `_runs/wq*/attempt-*.json` records, 1326 of them token-pool.
118 are LIMIT, 114 of those token-pool. **Every one of the 114 has `api_retries:0`,
`latest_resets_at:null` and `result_subtype:"success"`** (with `is_error:true`). A null
`resets_at` on every LIMIT means no rejected `rate_limit_event` was ever read, which
matches §2. The only reads that could have set `saw_limit` are the top-level 429 checks
at L227. The records cannot tell whether a retry frame's `error_status` or the result
frame's `api_error_status` triggered them.

Consequences:

- `fworker.handle_limit` (`fworker.py:2310-2323`) never had a `resets_at`, so the timed
  sleep-then-resume path never ran.
- A `rate_limit`-category retry without a 429 status was not read at all.

## 4. Patch (`harness/classifier.py`)

- `RunState.observe()` rate-limit block: the reader unwraps `rate_limit_info` before
  reading `status` and `resetsAt`. The flat shape still works for the old fixtures.
- `RunState.observe()` retry block: the matcher accepts `type=="system"` with
  `subtype=="api_retry"` as well as the legacy flat type. `error=="rate_limit"` now sets
  `saw_limit`, and the existing §C11 AUTH checks become reachable.
- Deliberately unchanged: `overloaded` is not treated as LIMIT. RELIABILITY_SPEC.md:30
  lists it, but the code never did it. A 529 has no reset window, so adding it would
  change `handle_limit` behavior, which is out of scope for a minimal fix.

Evidence:

- **Replay** (`python3 reports/api_retries_zero_rca_probe.py <pre.bak> harness/classifier.py`):
  the pre-patch classifier counts **0** retries across all 47 captures and the patched one
  counts **10**. All 10 are in `rabbitMisty3`. That run's verdict stays CRASH because it
  was a certificate error, not a limit.
- **Unit tests:** six new tests in `harness/tests/test_classifier.py` use the verbatim
  captured frames. Two assert on the raw captures: 10 retries counted, and a warning is
  not LIMIT. Four derive from those shapes: `error:"rate_limit"` gives LIMIT,
  `authentication_failed` gives AUTH, 401 gives AUTH, and a nested `rejected` gives LIMIT
  with `resets_at` read. `pytest harness/tests/test_classifier.py`: 50 passed. Before the
  patch it was 44 passed.
- **Full `harness/tests` suite:** 12 failures post-patch. The same 12 fail on a clean HEAD
  checkout (see §7), so they are pre-existing and environment-bound (token read, live wq
  patch checks, real-spawn integration), and none come from this change.

## 5. Not fixed here: filed as wq#1819

`fworker.py:1317` has the same flat-shape bug: it reads `frame["status"]` on the outer
`rate_limit_event`. As a result `report_limit_event()` has never fired and the governor's
`events.json` live-block path has never recorded a wall.

That path cannot be fixed at line 1317 alone. The governor's `_parse_iso`
(`governor_core.py:110`) catches only ValueError, while the wire's `resetsAt` is an epoch
int. `"...".replace` on an int raises AttributeError inside `evaluate_dispatch`'s
live_blocks loop (`governor_core.py:217`), so fixing 1317 alone would break dispatch
gating. wq#1819 covers all three pieces together.

## 6. E.2 re-score: FALSE_COMPLETE token-pool attempts vs `^\s*\[?API Error:`

I re-ran `reports/wq1799_rescore_false_complete_api_errors.py` on 2026-09-11. It checks
the last block of `episode.jsonl`'s `assistant_tail`, the only persisted per-attempt
text.

```
FALSE_COMPLETE token-pool (oauth_token_used=True) attempts: 115   (112 at the Phase-0 count)
  -> no retrievable last-assistant-text, cannot re-score:     62
  has retrievable last-assistant-text, re-scorable:          53
re-scored as API-error retry banner (last block):             0
```

**Count that were actually CLI error text: 0 confirmed.** 53 are ordinary model text.
62 are unknown, so the upper bound is 62. Scoring those would need
`~/.claude/projects/<session_id>.jsonl`, which is outside the worker sandbox (ISSUE-LOG
#331).

There is one supporting data point. The only captured banner session (§1) ends
`is_error:true` / `terminal_reason:"api_error"`. §C1 never scores that as SUCCESS, so it
cannot reach FALSE_COMPLETE's artifact gate. It lands in CRASH instead, which fits
0-of-53. It is a single 2.1.233 capture, so it corroborates the result but does not prove
it.

wq#1816's CONTRADICT on wq#1799's scrub predicate (tool-only recovery after a banner) is
a separate defect and is not touched here.

## 7. ISSUE-LOG row (pending append)

The worker guard refuses writes to the primary tree `/home/lumen/kb`, so this row is
recorded here for whoever next holds kb write access:

| 334 | wq#1814: `api_retries` read 0 on all 1383 attempt records, and all 114 token-pool LIMITs had null `latest_resets_at` | `classifier.py` (since ac79dd1) matched `{"type":"api_retry"}` and a flat `rate_limit_event.status`. The CLI actually emits `system`/`api_retry` with the category in `error`, and nests status/resetsAt under `rate_limit_info`. The unit fixtures used the same invented flat shapes, so tests passed. Raw frames exist only in `agents/work-queue/_runs/*.jsonl` | both matchers patched (flat shapes still accepted), 6 tests over the verbatim frames, this report. The same bug at `fworker.py:1317` → governor is wq#1819 (fix it together with `_parse_iso`'s epoch crash). Build-worker allowlist refuses `claude --version`, shell variables (`F=...; $F`) and `>` redirects: use literal paths and pipe to `tail`/`grep` | SOLVED (classifier); wq#1819 OPEN |

## 8. Reproduce

```
python3 reports/api_retries_zero_rca_probe.py /home/lumen/foreman/_runs/wq1814/classifier.py.pre-wq1814.bak harness/classifier.py
python3 -m pytest harness/tests/test_classifier.py -q
python3 reports/wq1799_rescore_false_complete_api_errors.py
```
