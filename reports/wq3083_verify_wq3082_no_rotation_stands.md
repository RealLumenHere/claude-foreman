# wq#3083 — verify wq#3082 [rotation] burned credential set:248906ee66a0 (sk-key)

Archetype 8 verify pass. Author of wq#3082: claude-opus-5-5. Verifier: claude-sonnet-5@high.

PR under test: claude-foreman#1381 (merged `c15ff6f1`), adds `harness/tests/fixtures/wq3076_values.py`
and two tests in `harness/tests/test_mask_replay.py`. No rotation was performed.

## Verdict

CONFIRM. The no-rotation conclusion holds. One unrelated test is red today (see Side finding).

## Checked

- `python3 harness/mask_replay.py 3076`: 6 credential-shaped values, 0 unaccounted, "No rotation".
  Five are accounted to tracked lines (`wq3076_values.py`, `wq2756_research_note.py`); one is
  `rule-fixed` (a 31-char `sk-` string in a test source line that today's `masking.PATTERNS` no
  longer reads as a credential).
- `pytest harness/tests/test_mask_replay.py -k 3076`: 2 passed
  (`test_wq3076_pins_are_their_hashes_and_read_silently`, `test_wq3076_corpus_burned_nothing`).
- Independent hash check, values never printed: the 9 `sha256_12` hashes from the replay were
  compared against every `sk-[A-Za-z0-9_-]{6,}` candidate (80,572 of them, 1,974 files) under
  `/home/lumen/secrets`, `/home/lumen/.secrets`, `agent/.env`, `/home/lumen/litellm`,
  `mailbot/.dev.vars` and `/home/lumen/docker`. Zero matches, so none of the values is a
  live secret file entry.
- The diff is 60 lines added, tests and fixtures only. No masking rule was loosened.

## Limits of this check

- LiteLLM virtual keys live hashed in the database, so they were not covered by the file scan.
- The `rule-fixed` value relies on the current mask rules, which is weaker than a tracked-line
  match. It is a test-source string next to `MINTED = ...`, and the hash matches nothing live.

## Side finding (not caused by #1381)

`test_wq2253_sk_key_is_absent_from_the_model_context` fails today (1 failed, 39 passed with `-x`).
It finds the wq#2253 values in a `read-stale:/home/lumen/kb/ISSUE-LOG.md@658#unresolved` item as
well as the frozen snapshot. `read-stale` is today's text of the live ISSUE-LOG standing in for
bytes that are gone, so the test's expectation moves whenever that file changes. #1381 did not
touch that test. Needs its own ticket: pin the test to a frozen corpus rather than the live log.
