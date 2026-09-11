# wq#1799: re-score FALSE_COMPLETE API-error attempts (E.1/E.2 + E.3 check)

Source: `FWORKER_FIXES_FROM_HR_TEARDOWN.md` §E.2/E.3 (de-spaghettifier Phase-0,
studying a separate codebase referred to as "HR"). Re-run any time with
`reports/wq1799_rescore_false_complete_api_errors.py` — read-only, recomputed
from `/home/lumen/foreman/_runs` on each run.

## E.2: classifier fix

`classifier.py` lacked a scrub for a CLI `API Error:` retry banner rendered as
the last assistant text block of an otherwise success-shaped session. Fixed in
`harness/classifier.py`:

- `RunState.observe()` now tracks `self.last_assistant_text` (last non-empty
  assistant text block seen) — `harness/classifier.py:171-181`.
- `classify()`'s §C1 SUCCESS branch scrubs it before returning SUCCESS: if the
  last assistant text matches `^\s*\[?API Error: `, the verdict is CRASH, not
  SUCCESS — `harness/classifier.py:297-311`. CRASH is the same bucket the two
  litellm-gateway examples of this exact text shape already carry in the corpus
  (`_runs/wq1569/attempt-{1,2}.json`, `_runs/wq913/attempt-5.json`), so the
  oauth CLI path stops disagreeing with itself over the same signal. This
  prevents future occurrences from cascading into FALSE_COMPLETE through
  fworker's artifact/evidence gate (no real work happened, so neither gate
  would find any — the old failure mode was charging the model for an infra
  hiccup it never actually failed at).
- Covered by three new unit tests in `harness/tests/test_classifier.py`
  (`test_api_error_retry_banner_as_last_assistant_text_is_crash_not_success`,
  `test_api_error_retry_banner_bracketed_and_indented_still_matches`,
  `test_api_error_text_not_in_last_block_still_scores_success`).

## E.1: re-score the historical 112 (now 115) FALSE_COMPLETE token-pool attempts

Attempted a mechanical re-score of every current FALSE_COMPLETE attempt with
`oauth_token_used=True` against the same regex, using the best data actually
available in this environment. Result as of 2026-09-11:

```
FALSE_COMPLETE token-pool (oauth_token_used=True) attempts: 115
  no episode.jsonl at all (pre-dates wq#1183 or write failed): 26
  episode.jsonl exists but no attempt_end for this attempt#:  31
  attempt_end matched but assistant_tail was empty:           5
  -> no retrievable last-assistant-text, cannot re-score:     62
  has retrievable last-assistant-text, re-scorable:          53

re-scored as API-error retry banner: 0
confirmed real failure (has text, does not match): 53
```

**Data-availability gap, stated plainly:** the only per-attempt text this fleet's
fworker persists under `_runs/wq<ID>/` is `episode.jsonl`'s `assistant_tail`
(last 6 text blocks, 1500 chars each, written by `append_episode_event` — wq#1183,
`harness/fworker.py:1941-1952`), and only for attempts where that write
succeeded. Full CLI transcripts (`~/.claude/projects/...`) are outside this
worktree's allowed directories and were not readable from this session (`ls`
blocked: "may only list files in the allowed working directories"). 62 of the
115 attempts (54%) have no retrievable last-assistant-text by this route, so
they cannot be honestly re-scored — re-scoring them anyway would be a guess,
which §D/DOCTRINE both forbid.

Of the 53 that ARE re-scorable, **zero** currently match the API-error-retry
pattern — the last assistant text in all 53 is an ordinary work summary
(`wq1384/1`, `wq1325/1-2`, etc. — see script output). The 2 real corpus examples
of this exact banner text (`_runs/wq1569`, `_runs/wq913`) are litellm-gateway
attempts (`oauth_token_used=False`), already correctly bucketed CRASH, and are
outside the FALSE_COMPLETE token-pool set this ticket scoped.

**Conclusion:** the classifier fix (E.2) is applied and tested, and will catch
this shape in every future attempt regardless of episode-log coverage (it reads
`state.last_assistant_text` live off the stream, not the lossy episode tail).
The historical re-score of the specific 112/115 attempts named in the Phase-0
report could not be completed for 62 of them (no retrievable evidence, honestly
reported as a gap rather than guessed), and found no matches among the 53 that
could be checked — none of the currently-open FALSE_COMPLETE token-pool
attempts warrant reclassification on this data. If the historical count matters
more precisely than "could not be substantiated from available data", the
remaining 62 would need direct access to `~/.claude/projects/` transcripts by
session_id (recorded per-attempt in `attempt-N.json`), which this worktree
cannot read.

## E.3: is `api_retries` under-reading the wall?

30-second check, per the ticket:

```
api_retries distribution across ALL 1373 attempt-N.json records: {0: 1351, None: 24}
LIMIT-classified attempts: 118, api_retries distribution among them: {0: 114, None: 4}
LIMIT attempts with nonzero api_retries: 0
```

`api_retries` is **0 or missing for every single attempt ever recorded** —
never once nonzero, including all 118 LIMIT verdicts. That is implausible on
its face and corroborates the Phase-0 E.3 suspicion: `classifier.py:221-228`'s
`ftype == "api_retry"` match is either matching a frame shape the CLI (2.1.268+)
no longer emits, or the frame arrives nested/renamed the way `rate_limit_event`
and `api_error` already are (both of which DO have nested-frame fallback reads
a few lines above — `api_retry` has none).

This fleet does not persist raw stream-json captures under `_runs/wq*/` (see
E.1 above), so the literal "grep recent captures for `"type": "api_retry"`"
30-second check the Phase-0 note describes cannot be run here — there is
nothing to grep. The `api_retries=0` universality above is the closest
substitute evidence obtainable from this environment, and it is sufficient to
flag the undercount as real. **Not fixed by this ticket** — its ACTION list
scopes E.1 (re-score) and E.2 (classifier scrub) only; E.3 says "do the 30-sec
check", which is done and written up here. Recommend a follow-up ticket to
capture a live frame sample (e.g. tee raw stdout for one deliberately
rate-limited attempt) and update `classifier.py`'s `api_retry` match once the
real current shape is known — guessing the shape now would repeat the same
mistake this ticket is fixing.
