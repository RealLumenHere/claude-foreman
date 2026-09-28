# wq#3059 verify-pass: wq#2120 [SEC] triage 3 residual read_dir_sweep kv_secret hits

Adversarial verify-pass (archetype 8) of the diff shipped by wq#2120 — PR
http://100.121.17.5:3000/lumen/claude-foreman/pulls/1340 (merged `d9820239`).
Author: claude-sonnet-5. Verifier: claude-sonnet-5@high, fresh instance, read cold.

**CONFIRMED** — code is correct and tested as claimed. One residual operational
caveat noted below (not a code defect; not escalation-worthy).

## What the PR claims

`harness/read_dir_sweep.py` (+34) and `harness/tests/test_read_dir_sweep.py` (+29),
no other files touched. Adds `REVIEWED_HIT_FINGERPRINTS` — a `sha256(matched_span)[:12]`
ack-list, same convention as `wq523_pat_residue.py`'s `FALSE_POSITIVE_FINGERPRINTS` —
checked in `sweep_root()` (`read_dir_sweep.py:225`) so a hit whose exact matched text has
been human-reviewed as non-secret no longer blocks a `dir()` grant, while a genuinely new
secret on the same line/file still fires (keyed on match text, not path/line). Ack list
seeded with 3 fingerprints for the wq#2115-discovered hits: `self-hosting-guide.md:118`,
`:541`, and `autorandr_Makefile:71`. Claimed test results: 91/91 `read_dir_sweep`, 142/142
`capability_lint`. Also claims an unrelated test-isolation fix bundled in
(`test_write_runtime_mode_exits_zero_on_a_hit` was sweeping real fleet dirs).

## What I did to try to break it

1. Read `read_dir_sweep.py` end to end cold. Diff is additive-only: `hashlib` import,
   the `REVIEWED_HIT_FINGERPRINTS` set + `reviewed_hit_fingerprint()`, and one
   `if fingerprint in ACK: break` guard inside `sweep_root()`'s match loop
   (`read_dir_sweep.py:225-226`). No existing regex/matching logic touched.
2. Confirmed the fingerprint convention against the actual precedent
   (`ops/wq2911-evidence-perms/staged/wq523_pat_residue.py:118-136`): both use
   `sha256(token).hexdigest()[:12]`. Matches the claim.
3. Ran the suites myself rather than trusting the notes:
   `pytest harness/tests/test_read_dir_sweep.py -q` → 91 passed.
   `pytest harness/tests/test_capability_lint.py -q` → 142 passed.
   Exact match to the PR's numbers.
4. Tried to break the falsifiability property (the whole point of keying on match-text,
   not path/line): the new regression test
   `test_reviewed_hit_fingerprint_suppresses_only_that_exact_match` writes a `kv_secret`
   line, acks its fingerprint, confirms suppression, then writes a **different** secret
   value under the same label on a different file and confirms it still fires. Read
   through it line by line — it does exercise a real fingerprint mismatch, not a tautology
   (`ops/wq523...` precedent for the same style of test). Passes.
5. Checked whether the ack list could be trivially bypassed or made to swallow a live
   secret: the hash is over the full matched span (label+connector+value), not just the
   label, so a collision requires an attacker to reproduce the *exact* previously-reviewed
   placeholder text verbatim — and that text is itself not secret (public doc). Same
   residual-collision shape already accepted for `wq523_pat_residue.py`'s precedent; not
   a new hole.
6. Verified the 3 seeded fingerprints correspond to real, previously-observed hits (not
   invented): `_runs/wq2184/snapshots/20260920T173727Z-...read_dir_sweep.json` (a sweep
   captured *before* this fix) shows exactly two `kv_secret` hits at
   `research/harnessrouter-ref/src/docs/self-hosting-guide.md:118` and `:541` — matching
   the PR's cited locations. I do not have `dir()` access to
   `research/harnessrouter-ref` or `research_prompts/.scratch` from this verify worktree
   (confirmed: `ls` on those paths is refused by the sandbox), so I could not
   independently recompute the raw match text or its hash — that access gap is exactly
   what wq#2120 itself flagged as blocking ("neither path is reachable from a foreman
   worktree"). I could not reproduce the author's `sweep_root()`-on-real-files probe
   myself for this reason; I instead leaned on the pre-fix runtime snapshot as
   independent corroboration of the hit locations/kind, and on the synthetic-fixture
   regression test for the mechanism itself.
7. Checked the bundled "unrelated" test-isolation fix claim
   (`monkeypatch.setattr(rds, "worker_add_dirs", lambda *a, **k: [])` added to
   `test_write_runtime_mode_exits_zero_on_a_hit`). Read `write_runtime()`
   (`read_dir_sweep.py:283-299`): when called with `roots=None` (which
   `_main(["--write-runtime"])` does), it sweeps `policy_read_dirs() + worker_add_dirs()`.
   The pre-fix test (`git show d9820239~1:.../test_read_dir_sweep.py`) monkeypatched only
   `policy_read_dirs`, leaving `worker_add_dirs()` to read the **real**
   `AGENTS_SPEC_PATH`/`agents.yaml` and sweep whatever real `defaults.add_dirs` are
   declared there. Claim checks out — it was a real, live-filesystem leak into the test,
   introduced by wq#2119 folding `worker_add_dirs()` into `write_runtime()`'s default
   roots, and wq#2120's one-line monkeypatch addition is the correct, minimal fix.

## What I could NOT confirm, and why it matters less than it sounds

The PR/ticket notes claim, present tense, that landing this "suppresses the 3
confirmed-benign kv_secret hits ... so dir() grants under research/harnessrouter-ref and
research_prompts/.scratch are unblocked." That is not yet true at verification time, and
won't be until the next scheduled sweep:

```
$ python3 -c "
import sys; sys.path.insert(0, '/home/lumen/foreman/harness')
import capability_lint as cl
sweep = cl.load_sweep()
print('stale:', cl.sweep_stale(sweep))
print(cl.sweep_hit_under('/home/lumen/research/harnessrouter-ref/src/docs',
                          '/home/lumen/research/harnessrouter-ref', sweep))"
stale: False
('/home/lumen/research/harnessrouter-ref/src/docs/self-hosting-guide.md', 118)
```

`harness/runtime/read_dir_sweep.json` (the file `capability_lint.py` actually gates on)
is timestamped `2026-09-28T05:18:17Z` — written by the **pre-fix** code, ~9.5h before
this PR merged (`14:56:33Z`) — and still lists all 3 old hits. It is not stale (48h
budget), so `capability_lint.py` fails closed on it as-is. Nothing regenerates that cache
except `lumen-read-dir-sweep.timer`, confirmed via `systemctl list-timers`:

```
NEXT                        LEFT LAST                         PASSED UNIT
Tue 2026-09-29 05:19:58 UTC  10h Mon 2026-09-28 05:17:54 UTC 13h ago lumen-read-dir-sweep.timer
```

No merge hook, CI job, or other caller of `write_runtime()` exists (grepped
`harness/` and `bin/` for `write_runtime`/`--write-runtime`; only the systemd unit and
`read_dir_sweep_messenger.py`, which targets an unrelated root). So for roughly the next
10 hours after merge, `dir()` grants under both cited roots remain refused exactly as
before — contradicting the notes' present-tense "are unblocked."

I'm not treating this as a contradiction of the shipped code: the new logic is correct,
tested against synthetic fixtures that prove both the positive case (suppression) and the
falsifiability case (new secret still fires), and this cache-refresh latency is an
inherent, pre-existing property of the whole `read_dir_sweep`/`capability_lint` design
(wq#1482) — any fix to the sweep, including wq#2115's before it, has this same
merge-to-effective gap, since the gate reads a periodically-refreshed cache rather than
sweeping live. wq#2120 did not introduce this lag and had no acceptance criterion
covering it (`AC1` was citation-only: "cite file:line of the code that implements it").
It's worth flagging so whoever next needs a `dir()` grant under these two roots today
knows to either wait for the 05:20 UTC timer or run
`read_dir_sweep.py --write-runtime` by hand rather than assume the PR alone unblocked
them immediately.

## Verdict

CONFIRMED. Code change is minimal, correct, and covered by a regression test that can
fail (falsifiability proven). Test counts reproduce exactly (91/91, 142/142). The bundled
test-isolation fix is real and accurately described. The one gap found — the runtime
cache `capability_lint.py` reads is still pre-fix as of this verify pass, so the grants
are not *yet* unblocked in practice — is a pre-existing systemic latency, not a defect in
this diff, and resolves itself at the next daily sweep (`2026-09-29T05:19:58Z`) or on
manual `--write-runtime`. Not escalating.
