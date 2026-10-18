# wq#4223 — verify wq#4204 (PR#2144): balanced-paren tag arg parsing

**Verdict: CONFIRM**

Archetype 8 adversarial verify-pass. Author: claude-sonnet-5. Verifier: claude-sonnet-5@high
(fresh instance). Target: commit `9fa04f4a4e` "fix(needs): balanced-paren tag arg parsing",
merged to `main` as PR#2144.

## What the diff claims

`harness/needs.py`'s `_TAG_RE = re.compile(r"(?:needs:)?([a-z_-]+)\(([^)]*)\)")` captured a
tag's argument only up to the *first* `)`, so a `research(...)` question containing a
parenthetical aside (e.g. `research(does gfx1013 (RDNA3) need a passthrough quirk?)`) was
silently truncated at `RDNA3`. Fix replaces the single-shot regex with `_scan_tags()`, a
depth-counting balanced-paren scanner, keeping `parse_tags()`/`parse()`/`validate()`
call-compatible. The diff also records that wq#4204's second claim (DeepSeek
chat/completions 402 on web search) didn't correspond to any reachable code, logged at
`kb/ISSUE-LOG.md` row 1416 rather than acted on.

## Verification performed

1. **Read cold**: `harness/needs.py` (full, post-diff), the diff itself
   (`git show 9fa04f4a4e`), `research/trigger.py:92-129` (the consumer —
   `unserved_wq_questions()` calls `needs.parse(needs_field, detail).tags` and filters
   `name == "research"`, confirming trigger.py has no keyword-extraction logic of its own;
   the shared parser in needs.py is the actual bug location, not trigger.py despite the
   ticket title).
2. **Ran the shipped probe**: `harness/tests/test_needs.py` + `research/tests/test_trigger.py`
   — 30 passed, including the two new regression tests
   (`test_parse_tag_arg_with_nested_parens_is_not_truncated`,
   `test_parse_unterminated_tag_arg_is_skipped`,
   `test_research_tag_with_parenthetical_aside_is_not_truncated`).
3. **Ran the wider blast radius**: `harness/tests/` filtered on `needs or liveness or
   capability` — 407 passed, 0 failed. This specifically exercises
   `harness/tests/test_liveness.py`'s identity check
   (`"parse_tags(" in inspect.getsource(needs_mod.parse_liveness)`), which still holds post-rename.
4. **Adversarial probes outside the shipped test population** (`/tmp/fworker-wq4223/probe_needs.py`,
   run via `python3 -I`):
   - Unterminated tag with an extra nested `(` (`research(open (paren`) → `[]`, correctly
     skipped, no crash, no infinite loop.
   - Unbalanced mix of a broken tag followed by a valid one
     (`research(bad (unterminated, repo(/x)`) → recovers `[('repo','/x')]`, abandoning only
     the unparseable fragment — same no-worse-than-before failure mode as the pre-fix regex
     on malformed input.
   - `repo()` (empty arg) → `validate()` still correctly rejects as empty-argument.
   - 5-deep nested parens inside an arg → captured whole and correctly balanced.
   - Nested same-shaped tag inside an outer tag's arg (`research(need dir(/tmp) check)`) →
     consumed as one tag, `dir(` not spuriously split out — matches intended depth-counting
     semantics.
   - `validate("research(...)")` → still rejected as "unknown needs tag" (research is not in
     `TAG_NAMES`); this is pre-existing, unchanged behavior — `research(...)` tags are only
     ever authored via the ticket detail's `Needs:` line (not validated against `TAG_NAMES`),
     never via `wq edit --needs` (which is). Confirmed in `harness/capability.py:422` as the
     only `validate()` call site.
   No case produced a crash, a hang, or a worse-than-baseline mis-extraction.
5. **Independently re-checked the dropped DeepSeek-402 claim** (didn't just trust the commit
   message): grepped `research/*.py` for `deepseek`/`web_search`/`402` — no hits in
   `trigger.py`. Read `research/trigger.py:204-223` (`gateway_chat`) directly: the one
   `chat/completions` call site takes `model` as a parameter (gateway-routed, not
   hardcoded), builds a plain `{model, max_tokens, messages}` body with no `tools`/
   `web_search` field. Confirms the diff's claim that this part of wq#4204 didn't correspond
   to reachable code; correctly not touched, correctly logged instead of guessed at
   (`kb/ISSUE-LOG.md` row 1416 exists and matches).

## One nit (not a contradiction)

`harness/needs.py:200`, a comment in `parse_liveness()`'s docstring still reads
"the SAME parse_tags/_TAG_RE that `Needs:` uses" — `_TAG_RE` was renamed/removed in this
diff (now `_TAG_START_RE` + `_scan_tags`). Cosmetic only: no test or code depends on that
symbol name in the comment, confirmed by the full `test_liveness.py` pass. Not worth a
redispatch; flagging for whoever next touches this docstring.

## Conclusion

The balanced-paren scanner correctly fixes the described truncation bug, preserves all
existing call-compatible behavior (empty/malformed-tag handling, `validate()` semantics,
`Liveness:`/`Needs:` parity), and the diff's narrower, honestly-investigated scope (fixing
needs.py, not trigger.py; declining to act on the unverified DeepSeek claim) matches the
repo's research-first doctrine. No breakage found under adversarial probing beyond the
shipped tests. **CONFIRM** — no escalation to opus-5@high.
