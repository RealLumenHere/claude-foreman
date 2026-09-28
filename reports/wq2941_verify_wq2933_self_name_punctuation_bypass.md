# wq#2941 — verify-pass: wq#2933's kv-secret self-name guard (PR #1244)

Verifier: claude-sonnet-5 @ high, fresh instance. Worktree `wq2941-a1`.
Subject: `harness/masking.py` `_KV_NOT_SELF` + `_kv_rule`'s new `kl` group, as
shipped on the PR branch `fix/wq2933-rotation-burned-credential-sha256-c38f1b-a1`
(tip **1892763**, http://100.121.17.5:3000/lumen/claude-foreman/pulls/1244).

Note on identity: `1892763` is **not yet an ancestor of origin/main**
(`git merge-base --is-ancestor 1892763 origin/main` → not merged), but main
already carries a content-identical commit, **203440b** (same author/date/diff
for `harness/masking.py` and `harness/tests/test_masking.py`, differing only in
commit metadata and a `Co-authored-by` trailer — `diff` of `git show
<sha> -- harness/masking.py harness/tests/test_masking.py` for the two is
empty). This worktree's HEAD already contains 203440b, so all reproduction
below runs directly against the shipped code, unmodified.

## Verdict: CONTRADICTED

wq#2933 fixed a real false-positive (self-name placeholders like
`MISTRAL_API_KEY: MISTRAL_API_KEY` in `agent/litellm_config.yaml` paging the
kv-secret rotation tripwire — ISSUE-LOG row 921). But the guard it shipped,
`_KV_NOT_SELF`, has a punctuation-class bug that makes it a **bypass**: a real,
live, high-entropy credential glued to its own label with a single `,`, `;`,
or `?` in between is masked as if it were the harmless placeholder — silently,
with **no suppression record** in most of the cases tested. This is the
opposite of what a credential-masking rule is for, and it is a strictly worse
outcome than the bug being fixed: instinct 4 in this file's own comments
(`harness/masking.py:354-356`) calls a silent miss "the one failure direction
worse than the false positive."

## 1. The mechanism

`_KV_NOT_SELF` (`harness/masking.py:408`):

```
_KV_NOT_SELF = r"(?!(?P=kl)[.,:;!?]*(?![A-Za-z0-9._\-+/=:!#$%^*@~]))"
```

Read as: right after `LABEL<sep>`, refuse the match (i.e. treat it as a safe
placeholder) if the value repeats the label (`(?P=kl)`), optionally followed
by punctuation from `.,:;!?`, and then the body genuinely ends (next char is
not in `_KV_BODY`).

The bug: `_KV_BODY` (`harness/masking.py:376`) is
`[A-Za-z0-9._\-+/=:!#$%^*@~]`. Three of the six punctuation characters in the
guard's own class — `,` `;` `?` — are **not** members of `_KV_BODY`, while the
other three — `.` `:` `!` — **are**. Because `[.,:;!?]*` is a greedy-but-
backtracking group, the regex engine doesn't need the body to actually end: it
can backtrack the punctuation match down to zero width and then satisfy
`(?!bodychar)` by pointing at the punctuation character itself, which by
definition is not a body character. For `.`/`:`/`!` that trick fails (they
*are* body characters, so backtracking to zero-width lands on a body char and
the lookahead correctly fails, forcing real content behind them to be seen).
For `,`/`;`/`?` it succeeds unconditionally — the guard fires and suppresses
the match regardless of what comes after that single punctuation character.

So `LABEL: LABEL.<opaque>` genuinely still fires (as the PR's own comment and
tests claim), but `LABEL: LABEL,<opaque>`, `LABEL: LABEL;<opaque>`, and
`LABEL: LABEL?<opaque>` do not — contradicting that same comment's claim that
trailing punctuation is "allowed only where the body then ENDS."

The PR's test suite never probes this: `test_a_self_name_with_anything_behind_
it_is_still_a_credential` (`harness/tests/test_masking.py`, new in this PR)
covers a bare `.` and no separator at all, but no case uses `,`, `;`, or `?`
before real content — exactly the three characters that don't work the way
the rest of the class does. The probe population was drawn entirely from
punctuation that happens to be safe; per the ticket's own acceptance-probe
doctrine, a probe restricted to a safe subset "confirms and never falsifies."

## 2. Reproduction

`reports/wq2941_probe_self_name_punctuation_bypass.py` (committed alongside
this report, re-runnable, no live/mutable inputs — a fixed 32-char opaque
literal with no recognized fixed-prefix shape, so only the kv-secret rule is
exercised):

```
$ python3 reports/wq2941_probe_self_name_punctuation_bypass.py
FAIL: 6/6 real secrets survived unmasked:
  line='API_KEY: API_KEY,Zx8qLp3vWn7tRk2mYb9cQe4sHj6uFd1a'
    hits=[] suppressions=[] leaked=True
  line='API_KEY: API_KEY;Zx8qLp3vWn7tRk2mYb9cQe4sHj6uFd1a'
    hits=[] suppressions=[] leaked=True
  line='API_KEY: API_KEY?Zx8qLp3vWn7tRk2mYb9cQe4sHj6uFd1a'
    hits=[] suppressions=[] leaked=True
  line='GITEA_PASSWORD=GITEA_PASSWORD;Zx8qLp3vWn7tRk2mYb9cQe4sHj6uFd1a'
    hits=[] suppressions=['self-name'] leaked=True
  line='AUTH_TOKEN=AUTH_TOKEN,Zx8qLp3vWn7tRk2mYb9cQe4sHj6uFd1a'
    hits=[] suppressions=[] leaked=True
  line='DB_SECRET: DB_SECRET?Zx8qLp3vWn7tRk2mYb9cQe4sHj6uFd1a'
    hits=[] suppressions=[] leaked=True
```

Every case: `masking.mask_text()` returns the secret verbatim, unmasked
(`hits=[]`, no `"kv-secret"` label recorded). In 5 of 6 cases
`masking.suppressions()` — the audit trail wq#2677 built specifically so an
overbroad guard "shows up as rows, not silence" — records **nothing at all**;
only the env-var form (`GITEA_PASSWORD=...`) logs a `self-name` suppression
row. The general `LABEL: LABEL,<secret>` form is a fully silent miss: no mask,
no label, no audit row, byte-identical to clean text.

For contrast, the three punctuation characters that ARE in `_KV_BODY` behave
correctly and mask the same secret (confirmed interactively, not included in
the committed probe since they already pass):

```
'API_KEY: API_KEY.' + secret  -> masked, hits=['kv-secret']
'API_KEY: API_KEY:' + secret  -> masked, hits=['kv-secret']
'API_KEY: API_KEY!' + secret  -> masked, hits=['kv-secret']
```

This isolates the defect precisely to the three body-excluded punctuation
characters, not to the self-name concept in general.

## 3. Why this matters beyond a test-coverage gap

This module exists to catch credentials before they leave the fleet
(instinct 4, this ticket's own chain: wq#2933 was itself a rotation-tripwire
false-positive fix). A masking rule that can be defeated by inserting one
comma, semicolon, or question mark between a label and its value is a
practical bypass, not a cosmetic edge case:
- `,` is a field separator in CSV output and in many `key,value` log/env
  dumps — exactly the shape `agent/litellm_config.yaml`-adjacent tooling
  produces.
- `;` is a common shell/statement separator that shows up glued to values in
  command substitutions and multi-assignment lines.
- `?` appears in URL query strings (`...?api_key=API_KEY?live_value...`-style
  concatenation) and grep/regex snippets, which is literally the class of
  input (`wq#2923`'s `grep -o` pattern) this guard was built to tolerate.

A future accidental (or adversarial) `LABEL,` / `LABEL;` / `LABEL?` prefix on
a real credential now sails through the exact rule meant to catch it, with no
record for `mask_replay.py`-style audits to find later.

## 4. Scope check — did the PR do anything else wrong?

No other regression found. `test_a_key_labelled_identifier_is_a_recorded_
suppression` and the rest of `harness/tests/test_masking.py` (130 tests, this
worktree) pass unchanged; the `kl`/`kn` named-group split does not collide
(each `_kv_rule()` call compiles its own regex, never combined into one
pattern via alternation); Python's backreferences are IGNORECASE-aware
(verified: `re.compile(r'(?P<kl>abc)(?=(?P=kl))', re.IGNORECASE).match('ABCabc')`
matches), so case-mismatched labels (`Api_Key: API_KEY`) suppress correctly,
not by accident. The bug is narrowly the four-character mismatch between
`_KV_NOT_SELF`'s punctuation class `[.,:;!?]` and `_KV_BODY`'s charset.

## 5. Unrelated, already-logged, not a regression here

ISSUE-LOG row 922 (same wq#2933 worktree): `test_burn_filter.py::test_no_
tracked_module_pages_when_a_worker_reads_it` fails identically with and
without this diff on main — pre-existing, open, out of scope for this verify.

## Recommendation

Do not land PR #1244 as-is. The fix needs the punctuation class narrowed to
`_KV_BODY`-member characters only (i.e. just `.`, `:`, `!` — drop `,`, `;`,
`?` from `_KV_NOT_SELF`, or equivalently require the lookahead to check a
body-membership condition that cannot be satisfied by the punctuation
character itself), plus test cases using `,`/`;`/`?` immediately before real
secret content in both `test_a_self_named_value_is_a_recorded_suppression`
and `test_a_self_name_with_anything_behind_it_is_still_a_credential`.
