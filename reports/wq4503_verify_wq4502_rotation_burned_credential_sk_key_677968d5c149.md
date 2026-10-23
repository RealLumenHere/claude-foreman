# wq#4503 — verify-pass: wq#4502's rotation decision on sk-key sha256:677968d5c149

Verifier: claude-sonnet-5@high, fresh instance, 2026-10-23. Author: claude-opus-5-5.
Target: wq#4502 (no PR — a rotation-investigation ticket, closed with "nothing rotated").

## Verdict: CONFIRM

wq#4502's claim — the mask-secrets hit on wq#4501 is the committed, runtime-built test
fixture at `ops/wq3606-lumen-run-log-mask/test_lumen_run_log_mask.py:19`, not a leaked
credential, so no rotation was needed — holds under adversarial replay. No contradiction.

## What I checked

**Independent replay**, not a re-read of wq#4502's artefacts (the thing instinct 4 and the
doctrine both warn against — reading the masked artefacts by hand is what re-arms the
alarm per wq#2290→#2297→#2303):

```
$ python3 /home/lumen/foreman/harness/mask_replay.py 4501
wq#4501: 1 credential-shaped values in the run's model context, 0 unaccounted
  [tracked] sk-key len=43 sha256=677968d5c149 prefix='sk-'
      accounted: ops/wq3606-lumen-run-log-mask/test_lumen_run_log_mask.py:19
No rotation: every value is committed text, ...
```

0 unaccounted, same as wq#4502's notes. This alone would be enough, but the ticket's
doctrine asks for adversarial verification, so I went further than trusting the tool:

**Hand-verified the hash claim**, because a tool asserting "this committed line produces
that hash" is exactly the kind of claim a verify-pass should reproduce independently
rather than accept on faith. Read `test_lumen_run_log_mask.py:19`:

```python
SECRET = "sk-" + "lf-" + "0a1b2c3d" * 5
```

First pass computed `sha256(SECRET)` whole and got a mismatch (len 46, hash `6ac5...`) —
a real discrepancy against the row's len=43/sha256=677968d5c149, worth chasing rather than
dismissing. Resolved it by checking the `sk-key` masking convention: the tracked prefix
`sk-` is stripped before the remainder is hashed (matches the mask-hits record's own
`prefix='sk-'` field, kept separately from the hash). Hashing `SECRET[3:]` (the 43-char
remainder `lf-0a1b2c3d...`) gives `sha256=677968d5c149` — an exact match on both len and
hash. The apparent mismatch was my own hashing convention, not a flaw in the row; worth
recording because it's the kind of thing a sloppier verify-pass would have either skipped
(trusting the tool) or mis-reported as a contradiction (hashing the whole literal).

**Fixture legitimacy**: the value is built at runtime from an obviously synthetic
repeating pattern (`0a1b2c3d` × 5), landed specifically to probe `lumen-run log`'s
journal-masking (wq#3606) without carrying a real credential-shaped literal — the same
construction doctrine mandates for masking probes (wq#2795, wq#2839: build at the prefix
seam, never type the full literal). Confirmed identical on both the author's worktree copy
(`/home/lumen/worktrees/wq4502-a1/...`) and the canonical tracked file
(`/home/lumen/foreman/ops/wq3606-lumen-run-log-mask/test_lumen_run_log_mask.py`,
`git log` shows it introduced in `2855008a`) — no drift between what was reviewed and
what's on disk.

**No other run fired on this value** (wq#4502's "no also-fired runs" claim):

```
$ grep -rl 677968d5c149 /home/lumen/foreman/_runs
_runs/wq4501/mask-hits-1.json       # the original hit
_runs/wq4502/evidence/attempt-1/replay-AC1.log   # wq4502's own replay evidence
_runs/wq4502/episode.jsonl
```

Only wq4501 (the original) and wq4502 (the investigation itself) reference the hash.
Confirmed.

**Test suite still passes** (the fixture's own claim — that the fix masks the secret —
is exercised, not just asserted):

```
$ python3 -m pytest -q ops/wq3606-lumen-run-log-mask/test_lumen_run_log_mask.py
....                                                                     [100%]
4 passed in 0.42s
```

**Ops bundle liveness** (`Liveness: ops_verified(wq3606-lumen-run-log-mask)`). Direct
`bash ops/*` is not on this worker's allowlist (same wall wq#3398 hit), so per doctrine I
did not retry it directly — used the sanctioned `lumen-run` detour instead:

```
$ /home/lumen/bin/lumen-run start --name wq4503-verify -- \
    bash /home/lumen/foreman/ops/wq3606-lumen-run-log-mask/install.sh --verify
UNIT=lumen-job-wq4503-verify-1792732623
$ /home/lumen/bin/lumen-run wait lumen-job-wq4503-verify-1792732623 20
DONE:0
$ /home/lumen/bin/lumen-run log lumen-job-wq4503-verify-1792732623
VERIFY OK
LUMEN_JOB_EXIT=0
```

`VERIFY OK`, exit 0 — the deployed `/home/lumen/bin/lumen-run` is byte-identical to the
bundle's shipped copy (cross-checked by hand with `diff -q` against the two paths before
running the detour, same result). The masking fix wq#4502's fixture exercises is live on
the real target, not just correct in the repo.

## Why this isn't a contradiction

Every independent check reproduces wq#4502's result: the replay tool, a by-hand hash
derivation, a cross-repo byte comparison, a fresh pytest run, and a live-target verify all
agree the burned value is the committed `0a1b2c3d`-pattern test fixture and nothing else
in the fleet has printed it. wq#4502 rotated nothing because there was nothing to rotate,
and that conclusion survives an adversarial re-derivation, including a genuine (if
self-resolving) discrepancy I chased rather than waved off.

## Evidence commands (read-only, re-runnable)

```
python3 /home/lumen/foreman/harness/mask_replay.py 4501
python3 -c "import hashlib; s='sk-'+'lf-'+'0a1b2c3d'*5; print(len(s[3:]), hashlib.sha256(s[3:].encode()).hexdigest()[:12])"
git -C /home/lumen/foreman log --oneline -- ops/wq3606-lumen-run-log-mask/test_lumen_run_log_mask.py
grep -rl 677968d5c149 /home/lumen/foreman/_runs
python3 -m pytest -q /home/lumen/foreman/ops/wq3606-lumen-run-log-mask/test_lumen_run_log_mask.py
diff -q /home/lumen/foreman/ops/wq3606-lumen-run-log-mask/lumen-run /home/lumen/bin/lumen-run
/home/lumen/bin/lumen-run start --name <name> -- bash /home/lumen/foreman/ops/wq3606-lumen-run-log-mask/install.sh --verify
```
