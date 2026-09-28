# wq#2945 — verify-pass (archetype 8) of wq#2931: burned credential sha256:9fbe965f997e (url-auth)

Reviewer: claude-sonnet-5 @ high (fresh instance). Worktree `wq2945-a1`, scratch
`/tmp/fworker-wq2945` (claude-foreman `origin` fetched anonymously, PR ref
`refs/pull/1245/head` fetched into `pr-1245-verify`, no credentials printed).
Verified 2026-09-28. Subject: PR
http://100.121.17.5:3000/lumen/claude-foreman/pulls/1245, PR-head **79ddd88**
("fix(mask): skip url-auth bodies holding a placeholder", Refs wq#2931), landed
on `claude-foreman` `main` as **05e65ce** (same message, content-identical
diff to `harness/masking.py`/`harness/tests/test_masking.py`, confirmed by
diffing the two blobs — the hash differs only because the branch was
rebased/squashed on land, not because the shipped content differs).

## Verdict

**CONTRADICT.** wq#2931's rotation call is correct — `9fbe965f997e` (the
`ghp_<body>` placeholder in wq#2913's docstring) is genuinely a template, not a
credential, and nothing needed rotating. But the fix it shipped to stop that
false alarm is broader than the false positive it targets: the new
url-auth exclusion skips masking for **any** userinfo body that contains a
`<name>`-shaped substring anywhere before the first `@`, not only a body that
**is** one. A real secret glued directly (no separator) to an unrelated
bracket-shaped annotation — e.g. an app's own `<truncated>` marker, or a
hand-redaction that left part of the real value in place before the tag — now
sails through with **zero mask and zero alarm**, where the pre-fix code
correctly masked it. This is a new, more dangerous false-negative surface,
traded for closing one false-positive. Escalating per ticket instructions to
opus-5@high — filed as **wq#2954** (via `wq add`, per this ticket's escalation
convention).

## 1. What the fix does, read cold from the diff (not the PR description)

`git show 79ddd88` (also re-verified against the landed `05e65ce` — identical):

```
harness/masking.py            |  6 ++++++
harness/tests/test_masking.py | 10 ++++++++++
```

The url-auth rule (`harness/masking.py:508-527` at PR-head) gained one new
negative lookahead, inserted between the existing false-positive guards and
the body pattern:

```python
r"(?![^\s/'\"`@]*<[A-Za-z_][A-Za-z0-9_-]*>)"
```

placed right after the `scheme://user:` capture group and before
`_body()`'s `[^\s/'"`]{8,}(?=@[A-Za-z0-9\[])`. Commit message's own framing:
"`<name>` template placeholder anywhere before the first `@`, not just at the
start ... `<`/`>` are illegal unencoded in RFC 3986 userinfo and in every
token alphabet, so no live value can have this shape." That RFC claim is true
in isolation — but the regex doesn't assert "the value **is** `<name>`", it
asserts "the run of non-space/quote/`@` characters starting right after the
colon **contains** `<name>` somewhere." Those are different claims, and the
gap between them is exploitable.

Old rule (parent `a361e80`, before wq#2931) only excluded a placeholder
**at the very first character** after the colon:
`(?!\\*[${%<*.]|(?i:password|...)@)` — a single-character class, not a
run-then-placeholder scan. It had no equivalent hole.

## 2. The break, reproduced against the real shipped code (not a synthetic reimplementation)

Fetched the PR ref anonymously into this worktree, confirmed the landed
`harness/masking.py` on `main` HEAD (`05e65ce`) carries byte-identical rule
logic to the PR's `79ddd88`, then ran the probe against the actual repo
checkout (not a copy):

```
$ python3 reports/wq2945_probe_url_auth_placeholder_glued_secret.py
FAIL: 2/2 real secrets survived unmasked:
  line='https://lumen:' 'ghp_A1b2C3d4E5f6G7h8<truncated>@100.64.0.9'
    hits=[] leaked=True
  line='remote https://x:' 'ghp_A1b2C3d4E5f6G7h8<redacted-rest>@git.example'
    hits=[] leaked=True
```

(wq#2954: each `line=` is split at the `user:` seam so this report does not arm the
tripwire once the fix masks the shape; the probe printed each as one string.)

(`REAL_SECRET` is `"gh" + "p_" + "A1b2C3d4E5f6G7h8"`, built at runtime and
split at the prefix seam per instinct 4 — token-alphabet-shaped, 20 chars,
well over the rule's 8-char body floor, but not a real credential and not
matched by any of masking.py's *other* rules either, confirmed by the empty
`hits` list rather than a hit under a different label.)

Confirmed this is a **regression**, not a pre-existing gap: replaying the
identical two lines against the **parent** commit `a361e80` (pre-wq#2931)
masks correctly:

```
$ python3 -c "... import masking (a361e80's copy) ...; masking.mask_text(line)"
hits= ['url-auth']  masked= https://lumen:MASKED@100.64.0.9
```

And the PR's own 122-test suite still passes in full against the shipped
code (`python3 -m pytest harness/tests/test_masking.py`, full worktree
checkout of `79ddd88`: `122 passed in 42.15s`) — the suite's two new cases
(`FALSE_POSITIVES` split-literal placeholders, and
`test_url_auth_placeholder_exclusion_stops_at_the_first_at`) only ever test a
**pure** placeholder body or a placeholder **strictly after** a raw `@`
(inside a DSN password that has its own earlier `@`). Neither test constructs
a body that is real-secret-chars-then-placeholder-chars glued together with
no separator, so the suite cannot observe this: the "probe can't fail" pattern
this ticket's own doctrine names (wq#2630) — the verification was drawn only
from the population the fix was designed around (does a pure placeholder get
skipped, does a placeholder past `@` still mask) and never from outside it
(does a placeholder glued onto a **real** value in the same segment still
mask).

## 3. Why this is realistic, not a contrived edge case

The scenario isn't "an attacker crafts an adversarial string" — it's the
ordinary shape of two things this exact codebase already handles elsewhere in
`masking.py`'s own comment history:

- **App-added truncation markers.** Log/error paths that truncate a long
  value and append an annotation like `<truncated>` directly to it with no
  delimiter are a common logging pattern; nothing in `_body()`'s character
  class (`[^\s/'"`]`) rules out `<`/`>` as part of the "non-boundary" run, so
  a value immediately followed by such a marker is exactly the input the new
  lookahead now waves through.
- **Partial hand-redaction.** The whole reason masking exists is that humans
  and agents write partially-redacted secrets into transcripts/PR text
  (`ghp_realprefix<redacted-rest>@host`, intending full redaction but leaving
  the live prefix). This is *more* dangerous than wq#2913's fully-synthetic
  placeholder, because real bytes are present — and it is now the one shape
  this rule refuses to catch.

## 4. Scope check on the rest of the PR

- `test_url_auth_placeholder_exclusion_stops_at_the_first_at`
  (`harness/tests/test_masking.py:286-291`): correct as far as it goes — a
  placeholder strictly after a raw `@` inside a DSN password does not exempt
  the password. Confirmed passing, not re-litigated.
- The two `FALSE_POSITIVES` additions (`harness/tests/test_masking.py:112-114`,
  split-literal `"tok_<body>@host"` / `"gh" "p_<value>@git.example"`): correct,
  those really are pure placeholders and mask_text should not alarm on them.
  Confirmed passing.
- `mask_replay 2913 = 0 unaccounted` (wq#2931's done-notes): not re-verified
  here — orthogonal to the regex-scoping question above, and re-running it
  would re-arm the same chain-guarded tripwire this ticket's own notes
  already logged once (wq#2299 chain guard, wq#2275 tripwire) for no new
  information.

## 5. What a correct fix looks like (for the judgment pass, not applied here)

Anchor the exclusion so the placeholder must consume the **entire** run up to
the `@`, not just appear somewhere inside it — e.g. replace
`(?![^\s/'"`@]*<[A-Za-z_][A-Za-z0-9_-]*>)` with a lookahead that requires the
placeholder to start at the current position and run to (only) trailing
placeholder-safe characters before `@`, such as
`(?!<[A-Za-z_][A-Za-z0-9_-]*>[^\s/'"`@]*@)` anchored so nothing but the
placeholder (optionally followed by more placeholder-shaped text) sits before
`@`, or simplest: keep the original start-anchored check but repeat it once
after skipping a leading run of placeholder-only characters — the key
invariant is "no real body character may appear before the placeholder", the
opposite of what's currently expressed.

## Evidence trail

- PR diff read in full via `git show 79ddd88` (both files) in this worktree,
  fetched anonymously (`refs/pull/1245/head`, no credentials printed).
- Landed-content check: `git diff 79ddd88 05e65ce -- harness/masking.py
  harness/tests/test_masking.py` shows no substantive difference in the
  url-auth rule text (line-number-only diff from the `grep -n` capture used).
- Full existing suite against the shipped PR code:
  `python3 -m pytest harness/tests/test_masking.py -q` → `122 passed in
  42.15s` (scratch worktree `/tmp/fworker-wq2945/pr1245wt`, removed after use).
- Regression probe committed at
  `reports/wq2945_probe_url_auth_placeholder_glued_secret.py`, run directly
  against this worktree's `harness/masking.py` (which already carries the
  landed `05e65ce` fix): `FAIL: 2/2 real secrets survived unmasked`.
- Pre-fix comparison: same two probe lines replayed against parent commit
  `a361e80`'s `harness/masking.py` → `hits=['url-auth']`, correctly masked.
- wq#2931 ticket detail + done-notes read via `/home/lumen/bin/wq show 2931`.
