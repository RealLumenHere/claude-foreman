# wq#2721: verify-pass of wq#2708 (rotation burned credential 2 values set:655ba578f22e)

**Verdict: CONFIRM.** wq#2708's "not a credential, nothing rotated" closure holds under
fresh, falsifiable evidence, including a re-run of the probe under today's tooling (which
changed after wq#2708 closed) and a genuine attempt to find drift that would break it.

## What the title actually names

`[rotation] burned credential 2 values set:655ba578f22e (cloudflare)` is keyed by
`fworker.rotation_title`: `sha256("\n".join(sorted(hashes)))[:12]` over the distinct
`value_sha256_12` of the firing run's BURNED-labelled records:

```
hashes = sorted(['416784616b2f', 'f0ff05bc0020'])
sha256("\n".join(hashes))[:12] == '655ba578f22e'   # exact match
```

Both hashes are exactly the two `cloudflare` records in `_runs/wq2692/mask-hits-2.json`,
matching wq#2708's own notes and ISSUE-LOG row 804.

## Re-running the probe today

wq#2708 closed at 19:28:21Z citing `mask_replay.py 2692` as "18 values, 0 unaccounted."
Tooling changed twice after that: `b28a936` (18:59:49Z, already landed before wq#2708 ran)
closed the branch-slug leak path at the source, and `52da96c`/`596dab1` (20:44-20:45Z,
**after** wq#2708 closed) fixed `--rules auto` to stop silently defaulting to the stale
pre-2026-09-20 table. Replaying under both tables the ticket could plausibly have meant:

```
$ python3 harness/mask_replay.py 2692                  # today's correct default: current
wq#2692: 2 credential-shaped values in the run's model context, 0 unaccounted
  [tracked] cloudflare len=11 sha256=416784616b2f prefix='cfut' accounted: harness/tests/fixtures/wq2687_values.py:13
  [tracked] cloudflare len=11 sha256=f0ff05bc0020 prefix='cfut' accounted: harness/tests/fixtures/wq2687_values.py:19

$ python3 harness/mask_replay.py 2692 --rules legacy    # forced; what auto gave before 52da96c/596dab1
WARNING --rules legacy forced on a run that started 2026-09-25T17:52:04Z, at or after the
table's 2026-09-20T22:35:00Z retirement — this replays the WRONG table for this ticket's own date
wq#2692: 18 credential-shaped values in the run's model context, 0 unaccounted
  ... includes both 416784616b2f and f0ff05bc0020, both [tracked] at wq2687_values.py:13/:19 ...
```

The forced-legacy run reproduces wq#2708's cited "18 values, 0 unaccounted" exactly — it
wasn't hallucinated, it's what `--rules auto` genuinely returned before today's default was
fixed. Both rule tables agree on the only fact this ticket's title depends on: the two
title-keyed hashes are `[tracked]`, and the run has 0 unaccounted values either way.

## Direct fixture check

`harness/tests/fixtures/wq2687_values.py`:

```
13: ("416784616b2f", "cloudflare", "-kv-secr-a1"),
19: ("f0ff05bc0020", "cloudflare", "-kv-secr-a2"),
```

Both are branch-slug tails (`feat/wq2687-mask-secrets-bin-sed-mirror-cfut` + `-kv-secr-a1`/`-a2`),
not credentials — confirmed by the fixture module's own header (wq#2701, pinned as committed
text) and consistent with row 802's independent derivation of the same two values.

## What nearly contradicted it

Running the full `test_mask_replay.py` suite today shows **5** failures, not the 3 wq#2708's
notes (ISSUE-LOG row 804) cite as pre-existing and unrelated:

```
$ pytest harness/tests/test_mask_replay.py -q
FAILED test_the_live_rules_would_not_have_filed_wq2307
FAILED test_the_live_rules_would_not_have_filed_wq2336
FAILED test_wq2278_unaccounted_values_are_fixtures_off_its_own_branch
FAILED test_wq2687_corpus_burned_nothing        # new
FAILED test_wq2702_corpus_burned_nothing        # new
5 failed, 72 passed
```

That looked like it might mean row 804's evidence chain was already stale. It isn't: row 804
was committed at 19:56:09Z, before the two new failures existed — they're caused by
`52da96c`/`596dab1` landing at 20:44-20:45Z, **after** row 804 was written, and are already
independently recorded and explained at ISSUE-LOG row 812 (confirmed there, by stashing that
change back out, to be live-corpus drift in `_runs/wq2687` and `_runs/wq2702` growing new
attempts over time — not a code regression, and not caused by wq#2708's own (nonexistent)
diff). Row 804's "3 failures, unrelated" claim was accurate when written; it is simply
dated now, same as any snapshot of a live-drifting fixture.

## No code change to verify

wq#2708 shipped no PR ("No PR (no code change)"). Confirmed: `fix/wq2708-rotation-burned-
credential-2-values-set--a1` has 0 commits ahead of `main`.

## Bottom line

- wq#2708's conclusion is correct on the merits and on its own evidence: both title-keyed
  hashes are tracked branch-slug-tail fixture text, not credentials; 0 unaccounted under
  either rule table; nothing to rotate.
- Its "18 values" figure and "3 pre-existing failures" claim both check out as exactly what
  the tooling reported at the time it closed, even though both numbers have since moved
  (2 values under today's fixed default; 5 failures due to unrelated live-corpus drift) —
  neither move implicates wq#2708's own work, and both are already explained by other
  ISSUE-LOG rows (793/812) rather than needing a new one here.
