# wq#3037 verify-pass: wq#3018 [judgment] wq#3007 CONTRADICTED wq#2922 — CONTRADICT

**Verdict: CONTRADICT.** wq#3018's core act — cherry-picking `c1301b79` onto current `main`
as `e2dbe416` — is real and correctly closes the class wq#3007 flagged (a merged fix that had
only ever existed on an unlanded branch). Every probe the PR names, run unmodified on this
worktree (which already carries `e2dbe416`), passes exactly as claimed. But the PR's second
deliverable — closing the "fail-loud backstop over-fires" risk wq#3007 named and wq#3018's own
report treats as solved — is not solved. `_orphan_matches()`'s combined-floor guard, in both
`harness/masking.py` and its daemon mirror, alarms on ordinary text that holds **no credential
at all** whenever an already-masked placeholder is glued with zero separator to a second mention
of the *same* prefix family — exactly the shape the PR's own docstring claims is safe because
"this rule's charset cannot run through" the second prefix. That claim is false for all five
rule families tested (github_pat, cloudflare, sk-key, slack, aws): a prefix's own literal
characters are, by construction, a subset of its own body charset, so the charset runs straight
through it every time. The concrete cost is not cosmetic: `token_engine_daemon.read_worktree_file`
(`token-engine/token_engine_daemon.py:692-715`) calls this same backstop via `mask_hits()` and
`raise Deny("body_mask_tripwire", ...)` on any hit when a worker posts a PR title/body/comment —
so this can falsely refuse to post a legitimate PR that merely *discusses* masked-output
adjacency, which is exactly the subject matter this ticket family (wq#2638, wq#2922, wq#3007,
wq#3018, this ticket) writes about constantly.

## Reproduction of the PR's own claims (fresh instance, worktree as shipped, no edits)

```
$ python3 reports/wq2868_probe_underfloor_class.py
B under-floor via MASK guard (leak = fail): 96 cases, 0 failing
TOTAL FAILING 0

$ python3 reports/wq3018_probe_orphan_backstop_on_main.py
B under-floor via MASK guard (leak = fail) [on main]: 96 cases, 0 failing
orphan backstop quiet on real committed text: 14 cases, 0 failing
daemon mirror closes the same class: 96 cases, 0 failing
TOTAL FAILING 0

$ python3 -m pytest harness/tests/test_mask_underfloor_invariant.py -q
9 passed in 1.24s
```

All three sections and the test file are exactly as wq#3018's report states. The class wq#2922
was filed against is genuinely closed on `main`.

## The break

`masking.py:699` (docstring for `_orphan_matches`) argues the combined-floor test is safe against
a second same-family prefix because that prefix "is... the START of another rule's own prefix,
which this rule's charset cannot run through." Checking each of the six `_ORPHAN_SHAPES`
(`masking.py:464-471`) against the charset it pairs with:

| label | prefix | charset | prefix chars ⊆ charset? |
|---|---|---|---|
| github (`ghp_` etc) | `ghp_\|gho_\|...` | `[A-Za-z0-9]` | no (`_` excluded) |
| github (`github_pat_`) | `github_pat_` | `[A-Za-z0-9_]` | **yes** |
| cloudflare | `cfut_?` | `[A-Za-z0-9_.-]` | **yes** |
| sk-key | `sk-(?:ant\|or\|...-)?` | `[A-Za-z0-9_-]` | **yes** |
| slack | `xox[bpasr]-` | `[A-Za-z0-9-]` | **yes** |
| aws | `AKIA` | `[0-9A-Z]` | **yes** |

Five of six rule shapes have a charset that is a strict superset of their own prefix's alphabet,
so the "cannot run through" premise is false for the majority of the table it is written to
justify, and the daemon's mirrored copy (`token-engine/token_engine_daemon.py:301-323`, identical
guard) inherits the same gap.

Minimal repro (driver: `/tmp/fworker-wq3037/repro_orphan_overfire.py`, not committed, ticket
tmp dir — split at the prefix seam per instinct 4, never written as one glued literal): for each
of the five rule shapes, build `prefix + filler + "MASKED" + prefix` as separate concatenated
pieces, where `filler` is short letters/digits chosen so neither `prefix + filler` (the "before"
half) nor the bare trailing `prefix` alone (the "after" half) reaches that rule's own floor:

```
label        prefix           filler-len   combined (before+after) vs floor
cloudflare   "cfut_" (5)      2            7  >= 6
github_pat   "github_pat_"(11) 10          21 >= 20
sk-key       "sk-ant-" (7)    10           17 >= 16
slack        "xoxb-" (5)      4            9  >= 8
aws          "AKIA" (4)       13           17 >= 16
```

Every one of the five: `masking.mask_text(text) == (text, [label])` — the output is
byte-identical to the input (no redaction happened, confirming nothing in the string
independently matches any of `masking.PATTERNS` on its own) while `hits` still fires. There is
no candidate credential shape anywhere in the string, live or otherwise. The daemon mirror
(`ted.mask_hits(text)`) reproduces identically for all five, e.g. `['cloudflare_token']` for the
cloudflare case.

A single separating character between the literal and the second prefix is enough to silence it
(inserting one space, or a comma, before the trailing `prefix` drops the hit to `[]`) —
`_OWN_TAIL.match` must start exactly where the first guarded run stopped, and none of the six charsets admit whitespace or
common punctuation. So the false-alarm class is narrow (needs a true zero-gap join of an
already-masked placeholder to a second same-family prefix mention) but not contrived: it is the
exact shape produced by concatenating masking examples with no delimiter — a table cell, a
`grep -o`-then-`tr -d '\n'` pipeline, a doc that shows two redacted samples back to back — the
same category of shape `_body()`'s own docstring (`masking.py:100-104`) and
`test_a_live_value_glued_to_masked_output_still_masks_and_alarms` already treat as realistic
enough to test for the *leak* direction. Nothing tests it for the *false-alarm* direction with a
short (under-floor) second body: every fixture in that existing test (`test_masking.py:33-38`,
`GHP`/`GHPAT`/`SK`/`AKIA`/`CFUT`/`XOXB`) is well above its rule's floor, so those adjacency cases
all resolve through the ordinary `PATTERNS` match, never through `_orphan_matches`.

## Why the shipped probe can't see this

`reports/wq3018_probe_orphan_backstop_on_main.py`'s `quiet()` section — the one built
specifically to catch over-firing — sweeps 14 real committed files (`masking.py` itself, its
tests, the daemon, the sed wrapper, three reports, a few docs, `kb/ISSUE-LOG.md`). None of those
files happen to contain a zero-gap same-family `<prefix>MASKED<prefix>` join, so it reports 0/14
truthfully — but that is a fact about the 14 files chosen, not a proof that the combined-floor
design closes the class its own negative control (the bare "does the charset continue" check,
80/14 false alarms) was built to contrast against. This is the wq#2630 shape by name: a probe
drawn from the population the fix happens to have been checked against, not from outside it.

## Severity

Never a leak: `_orphan_matches()` contributes to `hits`/`scan()` records only, never to `spans`,
so no text is mis-redacted and no `scan()` record carries more than a length and a hash. The cost
is a false alarm, and it has one concrete, working consequence in this codebase today:
`token_engine_daemon.read_worktree_file` (`:692-715`) is the body/title reader for the daemon-
mediated PR verbs (`:757-758`, `:783-785`, `:798`, `:1463` — the `lumen-pr` path every worker,
including this one, uses to land work) and refuses outright, `Deny("body_mask_tripwire", ...)`,
if `mask_hits()` returns anything. A PR body that quotes two adjacent masked-output examples of
the same credential family with no separator — plausible in exactly the reports this ticket
family produces — would be refused with no credential anywhere in it.

## What I did not do

Did not re-implement `_orphan_matches` or `_ORPHAN_SHAPES`. The fix is a judgment call: the
combined-floor test needs a THIRD condition (the "after" run must not itself begin by re-matching
the SAME rule's own prefix) or a narrower charset for the trailing walk, and either choice trades
against the `_body()`/`_OWN_TAIL` invariants the rest of the module leans on — the same kind of
call wq#3018 itself deferred to escalation for the acceptance-gate root cause (ticket item 3).
Filing the escalation this contradiction requires rather than patching here.

## Disposition

`wq#3037` closes as CONTRADICT. Filing `[judgment] wq#3037 verify-pass CONTRADICTED wq#3018:
orphan-prefix backstop over-fires on a zero-gap same-family MASKED chain, can Deny a legitimate
PR post` (opus-5@high per the archetype-8 escalation rule), deps on wq#3018, this report cited as
evidence. `e2dbe416` stays on `main` — nothing here argues for reverting the cherry-pick, which
correctly closes the under-floor leak class; only for tightening `_orphan_matches`'s trailing-run
check before a worker's own PR text trips it for real.
