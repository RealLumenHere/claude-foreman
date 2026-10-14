# wq#3773 verify of wq#3770: [rotation] sk-key sha256:80ddc9fffb71

Verdict: **CONFIRMED.** No rotation. wq#3770 shipped no code, so the verify is of its
no-rotation verdict.

## Checks (all re-run cold, 2026-10-14, values never printed)

1. `harness/tests/fixtures/wq2913_values.py` carries a row `("80ddc9fffb71", "sk-key", "sk-", ...)`.
   Slicing that row's joined string after the prefix and hashing it reproduces
   `sha256[:12] == 80ddc9fffb71`, length 11. The value has the shape `aaa-aaaaaa-` (a pytest
   id fragment: lowercase words joined by dashes), i.e. prose, not a credential body.
2. `mask_replay.replay(3753)` reports the record as `accounted_in: harness/tests/fixtures/wq2913_values.py:3`.
3. The origin matches wq#3770's account: it is cut from ISSUE-LOG #947/#984 pytest-output
   prose quoted in the wq#3753 transcript (`where 7 = len((('ba8aad54215e', 'sk-key', 'sk-key-letter-', ...`).

## Side finding (not a contradiction of wq#3770's subject)

`python3 harness/mask_replay.py 3753` now reports **1 unaccounted** value, not 0:
`kv-secret` len 21 sha256:`fb516393af08`, prefix `TOKEN=`, from transcript
`fc85f2ea-...` (wq#1482's `wq1482_smoke/root1/leak.env` smoke probe, 2026-10-12 13:50Z).

- It is **not** caused by wq#3076's per-span `today_closed` change: replaying at the commit
  before that PR gives the same 1 unaccounted.
- Shape: `ghp_` + 4 upper + `...` + 9 upper + 1 digit. It contains a literal `...`, which no
  GitHub token can; it is a synthetic, display-elided placeholder in a smoke probe, not a
  credential. Not in any committed file, so nothing accounts it.
- Follow-up (separate ticket, not re-implemented here): pin it as a split-literal fixture
  alongside the wq#1482 smoke values so the 3753 replay is clean again.
