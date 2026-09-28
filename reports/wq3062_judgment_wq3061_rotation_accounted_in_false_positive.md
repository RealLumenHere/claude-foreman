# wq#3062 — verify-pass of wq#3061 (rotation row set:b05f5da514d1, kv-secret)

**Verdict: CONTRADICT.** Escalating per archetype-8 protocol (contradiction -> opus-5@high judgment).

## What wq#3061 claimed

wq#3061 closed wq#2275's pri-1 rotation row with `status=done`, no rotation, no PR:

> Settled on replay, no rotation, no PR: `mask_replay.py 3059` exit 0, 2 values / 0
> unaccounted. `HR_SECRET_KEY=` len12 f80e633770c8 = committed text
> (dispatcher/DESIGN.md:462, upstream doc placeholder); `remove-key=` len25 95aa1524eedf
> = rule-fixed (CLI flag, current PATTERNS don't match). No also-fired runs in notes.

wq#3061 shipped no diff and no PR (confirmed: no commits in `/home/lumen/foreman`
mention wq3061, `harness/mask_replay.py`'s history has no wq3061 commit, and no
`reports/wq3061_*` file exists). Its only artifact is this ticket-notes claim, resting
entirely on `mask_replay.py`'s output.

## Reproduction

```
$ python3 /home/lumen/foreman/harness/mask_replay.py 3059
wq#3059: 2 credential-shaped values in the run's model context, 0 unaccounted
  [tracked] kv-secret len=12 sha256=f80e633770c8 prefix='HR_SECRET_KEY='
      accounted: dispatcher/DESIGN.md:462
  [rule-fixed] kv-secret len=25 sha256=95aa1524eedf prefix='remove-key='
      accounted: None
EXIT:0
```
Matches wq#3061's notes exactly — this part is real and reproducible.

## The `dispatcher/DESIGN.md:462` citation is false

Read the actual line three independent ways — working tree (`sed`/`awk`), and the git
blob at HEAD (`git show HEAD:dispatcher/DESIGN.md`, confirmed byte-identical to the
working tree, no uncommitted diff):

```
462: 2. **Lane cap defaults** in §4's config (6/4/4/4/2 per day) are placeholders scaled to
```

This is Frank-decision governance prose about lane cap config defaults. It is not a
doc, not a placeholder credential, and has nothing to do with `HR_SECRET_KEY` or any
upstream self-hosting guide. wq#3061's gloss — "upstream doc placeholder" — does not
describe this line; it describes a *different* location that appears only in the
mask-hits context snippet: `/home/lumen/research/harnessrouter-ref/src/docs/
self-hosting-guide.md:118` and `:541`.

## Why `mask_replay.py` matched it anyway (in-process verification, value never printed)

Imported `mask_replay` directly and, without ever printing the raw value, confirmed by
exact string equality (`value == "placeholders"` → `True`) that the actual 12-character
value redacted behind `HR_SECRET_KEY=` in wq#3059's transcript is the literal, ordinary
English word **"placeholders"**. `dispatcher/DESIGN.md:462` contains that same word at
character offset 64 ("...are **placeholders** scaled to...") — a coincidental match on
an unrelated common word, not evidence the credential is safely committed text.

`account()` (harness/mask_replay.py:458) only requires a value's own `prefix` to sit
next to it when `len(value) < TRUNCATED_FLOOR` (12). This value is *exactly* 12, so the
prefix requirement never engages and the bare word is treated as "long enough to be a
credible secret [context] on its own" — precisely the coincidental-match failure class
the file's own wq#2740 comment (lines 444-457) warns about, just one character outside
the guard's boundary.

Confirmed the real justification was never validated either:
`foreign_repos(corpus(3059), REPO_ROOT)` returns `[]` — the harnessrouter-ref mirror
that supposedly holds the genuine placeholder was never loaded into `tracked` for this
replay, so `self-hosting-guide.md` was never actually checked. wq#3055 (ISSUE-LOG row
1008) hit this identical shape before — a kv-secret value living only in
`research/harnessrouter-ref`, outside every tracked tree — and resolved it correctly by
pinning the value as a split-literal test fixture with hash-checked tests
(`test_wq2120_*`). wq#3061 did not do this; it took the tool's `accounted_in` output at
face value without checking what it pointed at.

## The second value inherits the same false positive

`remove-key=` (len 25, `95aa1524eedf`) has `accounted_in: None` on its own — genuinely
unaccounted by itself. It was still folded into `today_closed=True` (the field that
excuses it as "rule-fixed") only because `today_closed`'s `any(account(v, tracked, p)
for p, v in today_hits)` check (mask_replay.py:705-706) is computed over *every* hit on
the shared source line, and the bogus ``HR_SECRET_KEY=` + `placeholders`` match on that same
line makes the `any()` true. Traced directly: `account()` on the remove-key value itself
returns `None` in every call. Its "rule-fixed" disposition is not independently
established either — it rides on the same coincidental match.

## Net effect

Both credential-shaped values wq#2275 flagged were cleared as "0 unaccounted, no
rotation" on the strength of a single spurious dictionary-word match in unrelated
governance prose. Neither value has been genuinely shown to be safe committed text by
this replay. This does not mean either value **is** a live secret — the value shapes
("placeholders"; a `--remove-key=` desktop-file CLI flag) and the wq#3055 precedent both
point toward "benign, just under-verified" rather than "leaked" — but wq#3061's closing
evidence is false as stated and does not support the verdict it was used to justify.

## Recommendation

- Reopen wq#3061 (or file a follow-up) to pin both values properly, the way wq#3055 did:
  split-literal fixtures + hash-checked tests proving each value's real source (the
  harnessrouter-ref doc; the `.scratch/autorandr_Makefile` CLI-flag example), not a bare
  `mask_replay.py` run trusted at face value.
- `account()`'s `len(value) < TRUNCATED_FLOOR` boundary (mask_replay.py:468) should
  likely be `<=`, or otherwise stop treating an exactly-floor-length ordinary word as
  self-accounting without its prefix — logged as ISSUE-LOG row 1011.
- `today_closed`'s `any()` over every hit on a shared line lets one value's accounting
  (bogus or not) excuse a *different* value on the same line; it should check the
  specific (label, value) pair being classified, not the whole line's hits.

## Evidence trail (reproducible, no credential values printed)

- `python3 /home/lumen/foreman/harness/mask_replay.py 3059` — exit 0, output above.
- `sed -n '462p'`, `awk 'NR==462'`, `git -C /home/lumen/foreman show HEAD:dispatcher/DESIGN.md | sed -n '462p'` — all three agree on the line text; `git status --short -- dispatcher/DESIGN.md` empty (no drift between HEAD and working tree).
- In-process probes (scratch scripts under `/tmp/fworker-wq3062/`, not retained in the
  repo) imported `harness/mask_replay.py` and called `tracked_text()`, `corpus(3059)`,
  `_located()`, `account()`, `foreign_repos()` directly, comparing the real value to
  `"placeholders"` by `==` and confirming `foreign_repos(...) == []`. No credential-shaped
  literal was typed or printed; the only value printed to output was the word
  "placeholders" itself, sourced verbatim from committed, public `dispatcher/DESIGN.md`
  text.
