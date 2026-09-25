# wq#2736: verify-pass of wq#2733 (rotation burned credential sha256:a745825001bc, cloudflare)

**Verdict: CONTRADICT.** wq#2733's specific evidence for "committed text, no rotation" is
false — the cited `path:line` does not contain the flagged text anywhere. Independent
reconstruction of the real source strongly suggests the underlying alarm is *also* a false
positive (self-referential prose, not a live token), so the bottom-line action (no rotation)
is probably still right — but it isn't right for the reason given, the tool that produced
that reason has a reproducible accounting bug, and the true source sits in a place
`mask_replay.py` cannot currently see. That combination is a judgment trigger, not a rubber
stamp.

## What wq#2733 claimed

Closing notes (verbatim): *"Settled, no rotation: mask_replay 2732 -> 2 values, 0
unaccounted; a745825001bc is the committed cfut_<...> placeholder at
`2322_verify_wq2288_kv_secret_paren_regression.md:73`, no longer flagged by masking on main
(24f1ba4). No also-fired runs."* Same claim, same file:line, was carried into
`/home/lumen/kb/ISSUE-LOG.md` row 824 (uncommitted local edit at verify time — separate
problem, noted below, not fixed here).

## Re-running the probe today

```
$ python3 harness/mask_replay.py 2732
wq#2732: 3 credential-shaped values in the run's model context, 0 unaccounted
  [tracked] kv-secret  sha256=ddd35c808e54 -> reports/2726_..._charset.md:67
  [tracked] cloudflare sha256=a745825001bc -> 2322_verify_wq2288_kv_secret_paren_regression.md:73
  [tracked] sk-key     sha256=93b0cabf8668 -> harness/tests/test_masking.py:35
```

Already a mismatch: **3 values today, not 2** as the closing notes state. `test_masking.py`
(the source of the 3rd, unrelated `sk-key` hit) was last touched by `24f1ba4`, committed
2026-09-25T22:54:39Z — 14 minutes *before* wq#2733 claimed/ran (23:08–23:09). The file was
already in its current form when wq#2733 ran, so this isn't drift between then and now; the
closing notes simply don't match what the tool reports, then or now, for whichever run
produced "2 values."

## Breaking the citation

The claim under test: is `a745825001bc` (`cfut_`-prefixed, 9-char body) really committed
text at `2322_verify_wq2288_kv_secret_paren_regression.md:73`?

```
$ python3 -c "
text = open('2322_verify_wq2288_kv_secret_paren_regression.md', encoding='utf-8').read()
print('cfut in text (any case):', 'cfut' in text.lower())
print('line 73:', repr(text.split(chr(10))[72]))
"
cfut in text (any case): False
line 73: 'none of the vendor-prefixed patterns (github/cloudflare/sk/slack/aws) and hits this exact'
```

`grep -in cfut 2322_verify_wq2288_kv_secret_paren_regression.md` also returns nothing. The
string "cfut" does not appear anywhere in this file. Line 73 is real text, but it is about a
*different* bug (a kv-secret lookahead gap) and shares no wording with the masked preview
`mask_replay` shows for this record (`"...kv-secret value ending in '_' ... a cfut_MASKED
value ending in '_' leaks its trailing char..."`). The citation is not approximately right;
it points at unrelated prose.

## Root-causing the false "accounted"

Instrumented `mask_replay.account()` in-process (no source edits) to see which branch fired.
Isolating the cloudflare/`a745825001bc` record from `corpus('2732')`:

```
label=cloudflare prefix='cfut_' value_len=9
prefix+value literal found in tracked files: []          # nowhere, whole tree
body-only value found in 22 tracked files, e.g.:
  2322_verify_wq2288_kv_secret_paren_regression.md:73  "...vendor-prefixed patte..."
  2538_verify_wq2529.md:51                             "...dash-prefixed searc..."
  despag/harnesses.yaml:6                               "...chrn_-prefixed per t..."
```

The 9-character body is the literal word **"-prefixed"** — it matches in 22 unrelated files
purely because that's an ordinary English word fragment ("vendor-**-prefixed**",
"dash-**-prefixed**", "env-var-**-prefixed**", ...). `account()`'s primary match path is
`text.find(value)` on the **body alone**; it never checks that `prefix` (`cfut_`) sits
immediately before the match. Searching the whole tracked tree for `prefix + value` glued
together (`harness/masking.py`'s own captured concatenation) returns **zero** hits. So the
"[tracked] ... accounted: 2322_...:73" line the tool prints is a coincidental substring hit
on the body only, not evidence the actual flagged string (`cfut_` immediately followed by
this body) is committed anywhere. That's a genuine bug in `account()` (`harness/mask_replay.py:428`):
a short, common-word body can satisfy the direct-match branch without the prefix ever being
checked, manufacturing a false "accounted" — and by extension a false "no rotation" — for any
future ticket where the flagged body happens to collide with ordinary prose.

## Finding the real source

`_hits()` reports this record's source as `transcript:b58ea02f-...`, not a `read:` of a
report file — i.e. it came from something written *in a session*, not read from a committed
doc. `wq show 2732` (the ticket that session was working) contains, verbatim, in its
`detail` field:

> "...a **cfut_-prefixed** value ending in '_' leaks its trailing char..."

That's `cfut_` immediately followed by `-prefixed` — exactly the flagged prefix+body,
contiguous. This is wq#2732 talking *about* the cloudflare pattern by name (self-referential
meta-prose describing what a `cfut_`-prefixed value would do under the bug being discussed),
not a live Cloudflare token — consistent with essentially every other row in this saga
(ISSUE-LOG rows 799/802/804/812/821/824 are all the same class: the masking vocabulary
itself reads as credential-shaped). But `mask_replay.tracked_text()` only scans `git
ls-files` over this repo plus one extra doctrine file (`ISSUE-LOG.md`, `mask_replay.py:84`);
it has no path to wq ticket `detail`/`notes` fields at all. A worker reading that ticket (as
doctrine requires) reproduces this text in their own transcript, `mask-secrets` fires
correctly on it, and there is currently **no tooling path** that can account for it as
"committed" — it can only ever show as coincidentally-accounted (this bug) or genuinely
unaccounted.

## Bottom line

- wq#2733's stated evidence is false: the cited file:line does not hold this text, and the
  tool it cites for support currently reports a different value count than the notes claim.
- The likely-true root cause (self-referential prose in wq#2732's ticket detail, naming the
  `cfut_` prefix directly) does support "not a live credential" on the merits — but that
  evidence was never surfaced by wq#2733, isn't visible to `mask_replay.py` today, and isn't
  what got written into ISSUE-LOG row 824.
- `account()` has a reproducible false-accounting bug (body-only direct match, no
  prefix-adjacency check) independent of whether this particular row turns out safe to close.
  A future ticket with a short/common-word body and a genuinely leaked prefix+body could be
  auto-cleared the same way this one was.

Recommending opus-5@high judgment on: (1) whether `a745825001bc` can be closed no-rotation
on the corrected evidence (ticket-detail self-reference) rather than the false citation, (2)
whether ISSUE-LOG row 824 needs a correction append, and (3) whether `account()` should
require `prefix` immediately precede `value` on its direct-match path before accepting a hit
(`harness/mask_replay.py:436-439`).
