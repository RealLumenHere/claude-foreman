# wq#3864 — verify-pass of wq#3861 (guard: exempt lone wq quoted args from denied-verb scan)

PR: http://100.121.17.5:3000/lumen/claude-foreman/pulls/1852
Commit: `0d157964` "fix(guard): exempt wq quoted args from verb scan" (already on `main`)
Author model: claude-sonnet-5. Verifier: claude-sonnet-5@high, fresh instance.

## What the diff does

`harness/guard.sh` (lines 116–138): before the denied-verb scan (sudo/reboot/
shutdown/poweroff/halt/init 0|6), if `RAW_CMD` is a lone `/home/lumen/bin/wq `
call with no newline/CR/`$`/backtick/backslash anywhere, every quoted string in
the space-collapsed copy is blanked (`sed -E 's/"[^"]*"|'"'"'[^'"'"']*'"'"'/Q/g'`)
before the verb regex runs. If any separator/quote-shaped character
(`;&|()<>"'`) survives the blanking, the exemption is abandoned and the full
unblanked `RAW_CMD`/`NORM` scan runs exactly as before. Every other check in
guard.sh (systemctl, credential shapes, `rm -rf`, etc.) is untouched — only this
one scan moves. `harness/tests/test_guard.py` adds 2 tests (`WQ_QUOTE_EXEMPT_MUST_ALLOW`,
3 cases; `WQ_QUOTE_EXEMPT_MUST_BLOCK`, 8 cases).

Root cause cited (ISSUE-LOG row 1304, wq#3858): the denied-verb scan used to read
quoted `--notes`/`--detail` prose as if it were a real command; wq3854 lost a
worker when its own `wq done --notes "... reboot ..."` close-out call got
self-blocked. Verified row 1304 exists and matches this description, and that it
was explicitly filed as "proposed, needs a BUILD ticket to land" — wq#3861 is
that ticket. Rows 41/324/447/451 (cited as "same class") exist in ISSUE-LOG.md.

## Verification performed

1. Read the full diff (`git show 0d157964`) cold against `harness/guard.sh`'s
   current state — diff applies cleanly, matches HEAD, already merged to `main`.
2. Ran the shipped test suite: `pytest harness/tests/test_guard.py` — **78
   passed** (76 pre-existing + 2 new), no regressions, no skips.
3. Reasoned through the safety argument: given backslash/`$`/backtick/newline
   are excluded up front, bash's own quote-toggling for `'` and `"` (absent
   escapes and command substitution) is exactly pairwise/non-nested per quote
   type — identical to what the `sed` blanking does. The post-blank leftover-
   separator-char check is the safety net: any real unquoted `;&|()<>` or an
   unbalanced quote forces the fallback to the old (quote-blind, conservative)
   full scan, so a bypass requires the blanking to consume a real separator
   char leaving zero trace — found no construction that does this for the
   restricted quote grammar.
4. Adversarially probed `guard.sh` directly (bypassing the test corpus) with 10
   constructed commands covering: single-quote-containing-literal-double-quote,
   double-quote-containing-literal-single-quote next to a real `;`, separator
   chars fully inside quotes, adjacent concatenated quoted tokens, an odd
   (unbalanced) quote count both with and without a trailing denied verb, pure
   separator-character notes text, a tab (not newline) after `wq`, and two
   separate quoted flags one of which contains `;reboot`. All 10 matched the
   expected allow/block verdict — see `/tmp/fworker-wq3864/probe_wq3861.py`.
   No bypass found.
5. Confirmed the "lookalike path" (`wqx`) and "escalation verb in front"
   (`sudo /home/lumen/bin/wq ...`) cases both correctly fail the `^/home/lumen/bin/wq[[:space:]]`
   anchor and fall through to the full, unexempted scan.

## Verdict: CONFIRM

No contradiction found. The fix is correctly scoped (only the denied-verb scan
moves; every other guard check still sees the full unblanked text), the
fallback-on-any-leftover-separator design is sound against the bash quoting
grammar it assumes (no backslash/`$`/backtick/newline), the shipped test corpus
passes in full (78/78), and 10 additional adversarial probes outside that
corpus all produced the correct verdict. No escalation to opus-5@high needed.
