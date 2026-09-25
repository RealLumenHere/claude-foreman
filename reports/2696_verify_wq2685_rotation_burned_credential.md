# wq#2696: verify-pass of wq#2685 (rotation burned credential 2 values set:7eb405b32014)

**Verdict: CONFIRM.** wq#2685's "not a credential, nothing rotated" closure holds. It was
re-derived below with fresh, falsifiable evidence rather than re-checked by re-reading its
own notes — and it survived a genuine attempt to break it (see "what nearly contradicted it").

## What wq#2685's title actually names

`[rotation] burned credential 2 values set:7eb405b32014 (cloudflare)` is keyed by
`fworker.rotation_title`: `sha256("\n".join(sorted(hashes)))[:12]` over the distinct
`value_sha256_12` of every BURNED-labelled record in the firing run. Reproduced directly:

```
hashes = sorted(['94ad1f0edff7', 'bef57ec7f53a'])
sha256("\n".join(hashes))[:12] == '7eb405b32014'   # exact match
```

Those two hashes are exactly the two records in `_runs/wq2674/mask-hits-1.json` (attempt 1,
`ts=2026-09-25T13:31:05Z`, both `label=cloudflare prefix=cfut_`): `bef57ec7f53a` (len 6) and
`94ad1f0edff7` (len 9). **This ticket is keyed on those two values, not on any value discussed
in ISSUE-LOG row 783 or wq#2685's own PR notes** (which instead name `e86d1f06f41a`, len 21).

## What nearly contradicted it

The ticket's own "FIRST run" instruction, taken literally — `python3 harness/mask_replay.py
2674` with no flags — defaults to `--rules legacy` and reports 16 tracked values, 0
unaccounted. **Neither `bef57ec7f53a` nor `94ad1f0edff7` appears anywhere in that report.** A
verifier stopping there would have no evidence either way for the values this ticket is
actually about — and wq#2685's own merged fix (PR#1063, commit 11932e1) never names either
hash either: its regression test pins only `e86d1f06f41a` ("len 21 under the legacy table"),
which is the value ISSUE-LOG row 783 discusses, not this ticket's title pair.

That looked like a real gap: the closing worker's evidence trail addresses an adjacent but
different value from the same run, and never demonstrates the actual title-keyed hashes are
safe.

## Resolving it: `--rules current` is correct for a same-day ticket

`mask_replay.py --help`: `legacy (default): the pre-2026-09-20T22:35 table that filed the
backlog; current: today's masking.PATTERNS`. wq#2674 fired 2026-09-25T13:31Z — same day,
long after the legacy cutoff — so `current` is the rule table actually live when it fired,
and `legacy` is the wrong table for this ticket (it exists for the pre-9/20 backlog).

```
$ python3 harness/mask_replay.py 2674 --rules current
wq#2674: 3 credential-shaped values in the run's model context, 0 unaccounted
  [tracked] cloudflare len=6  sha256=bef57ec7f53a prefix='cfut_'
      accounted: 2322_verify_wq2288_kv_secret_paren_regression.md:44   (pre-existing doc)
  [tracked] cloudflare len=9  sha256=94ad1f0edff7 prefix='cfut_'
      accounted: harness/tests/test_masking.py:320                     (wq#2685's own WQ2674_PROBE literal)
  [tracked] cloudflare len=14 sha256=2f69993c081d prefix='cfut_'
      accounted: harness/tests/test_masking.py:320

No rotation: every value is committed text, or a string today's masking.PATTERNS no longer reads as a credential.
```

`account()` does a literal substring search of the tracked tree (`mask_replay.py:385-401`),
not a fuzzy or length-excused match — confirmed independently:

- `masking.value_sha12("abcdef") == "bef57ec7f53a"` — `"abcdef"` is the filler in
  `2322_verify_wq2288_kv_secret_paren_regression.md:44`'s
  `TOKEN=abcdefghijklmnop123456(` example, pre-dating this run.
- `masking.value_sha12("someToken") == "94ad1f0edff7"` — `"someToken"` is the literal
  placeholder body inside `WQ2674_PROBE = "cfut_" "someTokencfut_MASKED."`
  (`harness/tests/test_masking.py:320`), which wq#2685's PR itself committed.

So both of this ticket's title values are genuinely accounted for: one pre-existing, one an
incidental side effect of wq#2685's own committed literal. `pytest harness/tests/test_masking.py
-k wq2674` passes (1 passed).

## Bottom line

- wq#2685's substance is correct: nothing to rotate, no live credential reached the model.
- Its evidence chain is incomplete on its own terms: it never named, tested, or explicitly
  accounted for the two hashes that actually key this ticket's title, and its regression
  test comment's "under the legacy table" phrasing suggests the closing worker reasoned from
  a `--rules legacy` replay (ISSUE-LOG row 783's value) rather than checking the title pair.
  That gap is closed here with fresh evidence, not by trusting the prior closure's notes.
- Tooling gap recorded at `/home/lumen/kb/ISSUE-LOG.md` row 793: `mask_replay.py` defaulting
  to `--rules legacy` silently omits same-day tickets' own values from its report. Needs a
  follow-up ticket (not done here — this is a verify-pass, not a re-implementation) to make
  the tool default sanely for recent runs, or to have rotation tickets note which ruleset
  applies.
