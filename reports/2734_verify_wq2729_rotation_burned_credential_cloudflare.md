# wq#2734: verify-pass of wq#2729 (rotation burned credential 2 values set:655ba578f22e)

**Verdict: CONFIRM.** wq#2729's "not a credential, nothing rotated" closure holds under
fresh, falsifiable evidence and a genuine attempt to break it.

## What the title names, and a coincidence worth flagging up front

`[rotation] burned credential 2 values set:655ba578f22e (cloudflare)` is keyed by
`sha256("\n".join(sorted(hashes)))[:12]` over the firing run's BURNED-labelled hashes:

```
hashes = sorted(['416784616b2f', 'f0ff05bc0020'])
sha256("\n".join(hashes))[:12] == '655ba578f22e'   # exact match
```

This is the **same set hash, and the same two underlying values**, as wq#2708 (verified
CONFIRM by wq#2721 — `reports/2721_verify_wq2708_rotation_burned_credential_cloudflare.md`).
That's not a duplicate-ticket bug: wq#2708's firing run was `_runs/wq2692`, wq#2729's is
`_runs/wq2726` — a different run printing the same two fixture-derived strings again, which
the wq#2721 report and ISSUE-LOG rows 799/802/804/812 already establish as an expected,
recurring noise source (unmerged `wq#2687` branches still sitting in `git branch -a`
output). wq#2729's own notes independently reach the identical root cause. Treating each
firing as its own settled ISSUE-LOG row (cross-referenced, not merged) matches the
precedent wq#2721 already set for this exact value pair.

## Re-running the probe today

```
$ python3 harness/mask_replay.py 2726
wq#2726: 10 credential-shaped values in the run's model context, 0 unaccounted
  [tracked] cloudflare len=11 sha256=416784616b2f prefix='cfut' ...
  [tracked] cloudflare len=11 sha256=f0ff05bc0020 prefix='cfut' ...
$ echo $?
0
```

Exit 0, 0 unaccounted, both title-keyed hashes `[tracked]` — matches wq#2729's notes
exactly.

## Direct fixture and hash check

`harness/tests/fixtures/wq2687_values.py`:

```
13: ("416784616b2f", "cloudflare", "-kv-secr-a1"),
19: ("f0ff05bc0020", "cloudflare", "-kv-secr-a2"),
```

Recomputed independently, not trusted from the fixture comment:

```
>>> hashlib.sha256(b"-kv-secr-a1").hexdigest()[:12]
'416784616b2f'
>>> hashlib.sha256(b"-kv-secr-a2").hexdigest()[:12]
'f0ff05bc0020'
```

Both exact matches. The mask_replay context line for these two records shows the source:
a `git branch -a` tool_result listing `feat/wq2687-mask-secrets-bin-sed-mirror-cfut-a3`,
`...-cfut<cloudflare 11 chars>` (the masked form) and `...-cfutMASKED` — i.e. these are
branch-name tails, not Cloudflare tokens. Consistent with the fixture module's own header
(wq#2701, pinned as committed text) and with row 802's independent derivation of the same
two values from a third run (wq#2699/wq#2687).

## Chasing "also fired" as an adversarial check

wq#2729's notes claim "No also-fired runs." I grepped every `_runs/` tree for both hashes
beyond wq2726 and found hits in wq2725, wq2701, wq2703, wq2708, wq2730's evidence logs —
but each is a `replay-AC1.log` from an unrelated build/verify ticket **replaying the
already-known wq#2726, wq#2687, or wq#2692 corpora** as a regression check on the masking
tool itself (headers read `wq#2726: ...`, `wq#2687: ...`, `wq#2692: ...`), not a new live
fire in that ticket's own model context. No genuine unaccounted-for later firing exists.
The claim holds.

## What nearly contradicted it

`pytest harness/tests/test_mask_replay.py` is still red today, same 5 failures wq#2721
already found and explained (ISSUE-LOG row 812: live-corpus drift in `_runs/wq2687` and
`_runs/wq2702` gaining new snapshot attempts over time, not a code regression). Isolated
`test_wq2687_corpus_burned_nothing`: `assert 47 == 44` — count drift from new snapshots,
not a reclassification of either title-keyed hash. Unrelated to wq#2729's claim.

## No code change to verify

wq#2729 shipped no PR ("no credential burned, nothing rotated"). Confirmed:
`fix/wq2729-rotation-burned-credential-2-values-set--a1` is 0 commits ahead of `main`
(HEAD `24f1ba4`, an ancestor of `main`'s `4f7f0a5`).

## Bottom line

wq#2729's conclusion is correct on the merits and matches independent re-derivation: both
title-keyed hashes are tracked branch-slug-tail fixture text, not credentials; `mask_replay
2726` is 0 unaccounted; ISSUE-LOG row 821 accurately reflects the evidence. Nothing to
rotate.
