# wq#2735: verify-pass of wq#2730 (rotation burned credential 2 values set:41fa202c9ec9)

**Verdict: CONFIRM.** wq#2730's "not credentials, nothing rotated, leak path closed" holds
under fresh, falsifiable evidence: a re-run of the named probe, a direct test of the masking
detector before/after the diff, and a full-suite adversarial run that turned up nothing
implicating wq#2730's own commit.

## What the title actually names

`[rotation] burned credential 2 values set:41fa202c9ec9 (kv-secret)` is keyed by the sha256
of the sorted `value_sha256_12` hashes of the firing run's kv-secret records:

```
hashes = sorted(['78c1235f68e2', 'ddd35c808e54'])
sha256("\n".join(hashes))[:12] == '41fa202c9ec9'   # exact match
```

## Re-running the named probe

wq#2730's notes cite `mask_replay.py 2726` as "exit 0, 0 unaccounted." Reproduced today:

```
$ python3 harness/mask_replay.py 2726
wq#2726: 10 credential-shaped values in the run's model context, 0 unaccounted
  [tracked] kv-secret len=12 sha256=ddd35c808e54 prefix='TOKEN:' accounted: reports/2726_..._charset.md:67
  [tracked] kv-secret len=17 sha256=78c1235f68e2 prefix='TOKEN:' accounted: reports/2726_..._charset.md:68
  ... (8 more, unrelated cloudflare/kv-secret tracked records)
```
Exit code 0. Both title-keyed hashes are `[tracked]`, resolving to the toy `masking.mask_text()`
example literals already committed in the report's own code block (lines 67-68) — not live
secrets. Matches wq#2730's notes exactly.

## The diff itself: does it actually close the leak?

PR#1092 = commit `3f95600`, one file, `reports/2726_verify_wq2687_mask_secrets_cfut_kv_secret_charset.md`,
2 lines. It splits the one remaining *whole* copy of the 12-char `TOKEN:`-prefixed toy literal
(same value as the already-split code-block example two paragraphs up) — wrapped across prose
lines 90-91, not inside the already-split code block — into
`` `'TOKEN: ' + 'abcdefghijk_'` ``, mirroring the concatenation style already used at lines 63-68.

Tested `masking.mask_text()` directly, before vs. after:

```
$ git show 3f95600^:reports/2726_..._charset.md | python3 -c "...masking.mask_text(text)..."
BEFORE hits: ['kv-secret']

$ python3 -c "...masking.mask_text(open('reports/2726_..._charset.md').read())..."
AFTER hits: []
```

Confirms wq#2730's own claim verbatim ("mask_text hit 1->0"). `3f95600^` is `24f1ba4`
("fix(masking): the end of our own mask literal is a boundary") — the commit ISSUE-LOG row
822 names as the one the alarm was still live on, so the causal chain is exact: 993234d split
the code-block copies, 24f1ba4 left the file still firing via the prose copy, 3f95600 (this PR)
splits that last copy and the file goes fully clean.

Grepped the tree for any remaining unsplit copy of the literal — none:

```
$ grep -rn "abcdefghijk_" --include="*.md" .
  (only the three already-split occurrences at lines 66/67/76/91, all `'...' + '...'` form)
```

## Adversarial pass: full suite, not just the named test

Ran the whole masking suite, not scoped to this report:

```
$ pytest harness/tests/test_masking.py -q
FAILED test_the_mask_suite_does_not_arm_its_own_tripwire
FAILED test_doctrine_corpus_does_not_re_arm_the_alarm
2 failed, 72 passed, 1 xfailed
```

Both failures traced to files wq#2730 never touched: `ops/wq2316-mask-secrets-boundary/
test_mask_secrets_boundary.py:120` (a comment added by commit `4f7f0a5`, landed 36s *after*
wq#2730's `3f95600`) and `kb/ISSUE-LOG.md`'s own doctrine-corpus text. Both are already
identified, reproduced, and escalated by a sibling ticket — ISSUE-LOG row 825, wq#2737
contradicting wq#2731 — not a new finding here and not wq#2730's defect. wq#2730's own diff
predates and is untouched by that regression.

## Bottom line

- Both title-keyed hashes are tracked, committed toy literals, not credentials — nothing to
  rotate, confirmed independently via `mask_replay.py` and direct `masking.mask_text()` calls.
- The diff does what it claims: splits the one remaining whole copy of the literal, taking the
  file's own `mask_text` hit count from 1 to 0. Grep confirms no other unsplit copy remains.
- The only suite failures found while adversarially running the full test file are a separate,
  already-filed, already-escalated regression (wq#2737 vs. wq#2731) in files wq#2730 never
  touched — not a contradiction of this ticket.
