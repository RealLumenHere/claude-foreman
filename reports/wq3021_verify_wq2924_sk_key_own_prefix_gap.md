# wq#3021 — verify wq#2924: sk-key own-prefix letters-then-dash gap

Adversarial verify-pass (archetype 8) of `fix/wq2924-sk-key-own-gap-a1` (PR#1305,
`http://100.121.17.5:3000/lumen/claude-foreman/pulls/1305`, merged to main as `0acd7c55`
after rebase, original commit `818f32b7`). Author: claude-opus-5-5. Verifier:
claude-sonnet-5@high, fresh instance, read the diff cold.

**CONFIRMED on wq#2924's own claim** — no regression, no contradiction of the PR.
A separate, pre-existing, out-of-scope leak class was found while probing and is
logged at `kb/ISSUE-LOG.md` row 988 rather than treated as a contradiction of this PR.

## What the PR claims

`_PRE_SK` (`harness/masking.py`, mirrored in `token-engine/token_engine_daemon.py`) used
to be `sk-(?:(?:(?!_PRE_NOT_SK)[a-zA-Z])+-)*` — an open repetition of any letters-then-dash
unit, refusing only units that opened a FOREIGN rule's prefix (wq#2913). Any body that
happened to open with letters and a dash completed one more repetition and got swallowed
into the KEPT (unmasked) prefix group: `sk-` + letters + a second `sk-` masked to
`sk-<letters>sk-MASKED`, and a base64url `sk-proj-` body opening `Ab-` masked to
`sk-proj-AbMASKED`, printing live secret bytes. The fix replaces the open repetition with
`sk-(?:(?:ant|or|proj|svcacct|admin|None)-)?` — at most ONE qualifier, from a fixed
vocabulary of what Anthropic/OpenRouter/OpenAI actually issue. Anything else, including a
second dash-terminated letter run, now falls into the FLOORED body and gets masked away
in full.

## What I did to try to break it

1. Read the diff cold: `git diff 6a28b8e...fix/wq2924-sk-key-own-gap-a1` (merge-base to
   branch tip) across all 7 changed files — `harness/masking.py`,
   `token-engine/token_engine_daemon.py`, `harness/tests/test_mask_foreign_prefix_matrix.py`,
   `harness/tests/fixtures/wq2913_values.py`, `harness/tests/test_mask_replay.py`,
   `ops/wq2316-mask-secrets-boundary/README.md` + its test. Confirmed the daemon mirrors
   `_PRE_SK` verbatim and the ops sed copy delegates to `masking.py` since wq#2913 (its
   README diff correctly describes the fix as inherited, not reimplemented).
2. Ran the PR's own regression suite: `harness/tests/test_mask_foreign_prefix_matrix.py`
   — 907 passed (includes the new `test_sk_key_keeps_only_a_known_qualifier`, 3 impls x 7
   qualifiers x 4 gaps). Ran the broader `test_masking.py` + `test_mask_replay.py` +
   `ops/wq2316-mask-secrets-boundary/test_mask_secrets_boundary.py` — 342 passed.
3. Probed the exact shapes the PR's commit message and ISSUE-LOG row 984 describe (letters
   run + second `sk-`, `sk-proj-` + `Ab-`-opening body, non-vocabulary qualifiers like
   `sk-org-`, case variants `sk-ANT-`/`sk-Ant-`, a real-looking `sk-ant-api03-` chain) via
   `masking.mask_text()` directly (`/tmp/fworker-wq3021/probe_sk.py`). All masked correctly
   — no live characters survived outside the kept vocabulary prefix.
4. Per the acceptance-probe doctrine (an input from OUTSIDE the tested population): the
   PR's own `test_sk_key_keeps_only_a_known_qualifier` parametrizes `gap` over
   `["zz-", "zzzz"+_SK, "zzz-zz-", "zz"+_XOX]` — every fixture has non-empty filler between
   the outer prefix and the embedded foreign/own prefix. I probed the ZERO-gap case: a
   second recognized prefix glued immediately, with no filler at all, right after the first
   kept prefix. `masking.mask_text("sk-ant-sk-ant-" + "9"*20)` → `'sk-ant-sk-ant-MASKED'`
   — the second, live `sk-ant-` (7 characters) prints in the clear
   (`/tmp/fworker-wq3021/probe_sk3.py`, `probe_sk4.py`). The same shape against a FOREIGN
   prefix leaks identically: `sk-ant-AKIA<16 digits>` → `'sk-ant-AKIAMASKED'`;
   `sk-ant-ghp_<body>` → `'sk-ant-ghp_MASKED'` (`probe_sk7.py`). Reproduced identically in
   `token_engine_daemon.mask_rewrite()` (`probe_daemon.py`).
5. Checked whether this is a wq#2924 regression: extracted `harness/masking.py` at the
   merge-base `6a28b8e` (pre-fix) and ran the same zero-gap probe against it
   (`probe_sk_pre.py`). Identical leak, byte-for-byte: ``sk-ant-sk-ant-` + `99999999999999999999``
   → `'sk-ant-sk-ant-MASKED'` on BOTH pre-fix and post-fix code. **Not introduced by this
   PR.** By contrast, the non-zero-gap variant the PR's own tests DO cover
   (`sk-ant-` + 20 live chars + `sk-ant-` + 20 more, real body between the two prefixes)
   leaked much more on the pre-fix code (`'sk-ant-aaa...ask-ant-MASKED'`, the whole first
   body in the clear) and is fully masked on the post-fix code (`'sk-ant-MASKED'`) — so the
   PR's fix does close every case its own probe was built to catch; the zero-gap case is
   a different code path entirely.
6. Traced the actual root cause to rule out any blast-radius ambiguity: the single-pattern
   regex substitution (`rx.sub`) for `sk-ant-sk-ant-<body>` is CORRECT on its own
   (`probe_sk3.py`: `rx.sub(repl, s)` → `'sk-ant-MASKED'`, full coverage). The leak is
   introduced downstream, in `read()`'s phase-2 nested-match rescan (`masking.py:698-711`,
   wq#2913/wq#2960 union-of-spans redesign) plus `_redundant()`'s container-drop screen
   (`masking.py:726-763`): phase 2 finds the second `sk-ant-` inside the first match's span
   and reads it as a second, independent credential; `_redundant()` then compares the
   OUTER match's un-redacted lead-in text (`sk-ant-`, i.e. everything before the inner
   match) against `_OWN_TAIL` (`(?:<any fixed prefix>)*MASKED`) and, because a live,
   not-yet-masked prefix chain is textually indistinguishable from this module's own
   already-redacted output at screening time, decides the outer (correct, wider) match adds
   nothing and drops it — leaving only the narrower inner match to do the redaction, which
   starts 7 characters too late. Neither `_PRE_SK` (what wq#2924 changed) nor `_redundant`/
   `read()` (what it left alone) individually looks wrong in isolation; the composition is
   the gap.
7. Checked whether this was a known, accepted tradeoff: `_redundant()`'s own docstring
   (masking.py:753-756) cites a named regression test for exactly this invariant —
   `test_a_container_screen_never_uncovers_a_live_character` — as evidence the screen is
   safe. `git log --all -S test_a_container_screen_never_uncovers_a_live_character` across
   every branch returns nothing: that test does not exist and, per its content search,
   never has. The safety net the code cites as proof of soundness was never written.
8. Checked a realistic trigger, not just a synthetic one: two identical live keys glued
   with NO separator (a plausible "log format string printed the key twice" bug) —
   `LIVE + LIVE` where `LIVE = "sk-ant-" + 20 live chars` — masks correctly
   (`'sk-ant-MASKED'`, full coverage, `probe_sk5.py`), because there IS body filler between
   the outer prefix and the second occurrence of `sk-`. The zero-gap leak needs the second
   prefix to sit with literally nothing between it and the first prefix's end — narrower
   than "two secrets concatenated" but still reachable (a templating/formatting bug that
   emits a qualifier twice with no body in between, or — lower probability but nonzero at
   fleet scale — a live random body that itself opens with a recognized prefix literal
   right after the true prefix).

## Verdict

**CONFIRMED**: wq#2924 does exactly what it claims. `_PRE_SK`'s greedy
letters-then-dash qualifier repetition is closed (907 + 342 tests pass; every shape in the
PR's own commit message and ISSUE-LOG row 984 masks correctly; no non-vocabulary or
letters-only unit can grow into the kept prefix anymore). No regression versus the pre-fix
merge-base for any case in scope.

**Separate finding, not a contradiction of this PR**: a zero-gap glued-prefix leak
in `read()`/`_redundant()` (the wq#2913/wq#2960 union-of-spans machinery, untouched by
this diff) reproduces identically before and after wq#2924's change, so it is pre-existing
and out of this PR's stated scope. Logged at `kb/ISSUE-LOG.md` row 988 with full
reproduction, root cause, and the missing regression-guard test, for a follow-up ticket —
not filed here as a contradiction because wq#2924 never claimed to close this class and
introduced no part of it.

## Evidence

- `git diff 6a28b8e74135d47d860728b0f4bf9a2016d26ad7 fix/wq2924-sk-key-own-gap-a1` — the
  full 7-file PR diff.
- `python3 -m pytest harness/tests/test_mask_foreign_prefix_matrix.py -q` → 907 passed.
- `python3 -m pytest harness/tests/test_masking.py harness/tests/test_mask_replay.py ops/wq2316-mask-secrets-boundary/test_mask_secrets_boundary.py -q` → 342 passed.
- `/tmp/fworker-wq3021/probe_sk.py`, `probe_sk3.py`, `probe_sk4.py`, `probe_sk5.py`,
  `probe_sk6.py`, `probe_sk7.py`, `probe_daemon.py`, `probe_sk_pre.py` — all probe scripts
  and their stdout as pasted into this report's steps 3-8 above.
- `kb/ISSUE-LOG.md` row 988 — the new, separate finding, with root cause and the missing
  `test_a_container_screen_never_uncovers_a_live_character` regression guard.

## Liveness

Every probe ran the actual `harness/masking.py` and `token-engine/token_engine_daemon.py`
from this worktree (which already carries wq#2924 merged, `0acd7c55` on `main`) and a
byte-identical extract of `harness/masking.py` at the pre-fix merge-base — not mocks, not
a description of expected behavior. The full existing test suites (907 + 342 cases) were
executed, not assumed green from the PR description.
