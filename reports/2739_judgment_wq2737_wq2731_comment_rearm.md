# wq#2739 — judgment: wq#2737's contradiction of wq#2731 / PR#1093

**wq#2737 is UPHELD on the facts and wrong about the remedy.** Both of its
technical claims reproduce exactly at the pinned revs. Neither of the two
questions it escalated is still open the way it framed them: PR#1093 is merged,
and the defect is already gone from main — removed by accident, 13 minutes after
wq#2737 closed, by a commit that was fixing something else.

The finding that outlives both tickets is the one neither ticket asked about:
the tripwire only looks at eight hand-listed paths, and **18 other committed
files carry a whole credential shape right now**, including one with the exact
shape wq#2731 shipped. That is filed as wq#2749.

## (0) What reproduces

Probe (committed, rev-pinned, prints `label`/`prefix`/`value_len` and never a
value): `ops/wq2739-tripwire-corpus/scan_tree.py`. It runs the same
`masking.scan()` call the test itself makes at `harness/tests/test_masking.py:181`.

| rev | `ops/wq2316-mask-secrets-boundary/` | offenders |
| --- | --- | --- |
| `3f95600` (parent of wq#2731's commit) | 4 files | **0** |
| `4f7f0a5` (wq#2731 / PR#1093) | 4 files | **1** — `test_mask_secrets_boundary.py`, `('kv-secret', <a 7-char label prefix>, 17)` |
| `27b5a34` (wq#2732) | 4 files | **0** |
| `fa8784d` (main, this ticket) | 4 files | **0** |

So wq#2737's account is exactly right: wq#2731's diff left the code line
correctly split and put the same shape into the *prose of the comment above it*,
which the file-level tripwire reads no differently.

## (1) Should PR#1093 be reworked before merge? — moot, and do nothing

`git merge-base --is-ancestor 4f7f0a5 HEAD` exits 0. PR#1093 merged. wq#2737's
recommendation ("reworked before merge") describes a decision that no longer
exists.

Nor is there a fix left to carry. `27b5a34` (wq#2732, 2026-09-25 23:26:36, 13
minutes after wq#2737 closed at 23:13:58) rewrote 111 lines of that file and
deleted the assert *and its comment* outright — because the assert encoded
behaviour `masking.py` does not have, per ISSUE-LOG row 827. The re-armed
comment went with it. Main has been clean since.

**No revert, no rework, no follow-up fix on PR#1093.** The liveness command this
ticket carries already passes on `fa8784d`:

```
$ python3 -m pytest harness/tests/test_masking.py -k does_not_arm_its_own_tripwire -q -p no:cacheprovider
1 passed, 74 deselected in 0.76s
```

That command cannot fail on main, which is why it is not the acceptance evidence
for this ticket — section 3 is.

## (2) Should wq#2731's done-notes be flagged false-complete? — yes

wq#2731's notes, verbatim:

> split fix already at HEAD via unrelated commit 24f1ba4 (wq#2694); added
> wq#2731 comment anchor + verified test_the_mask_suite_does_not_arm_its_own_tripwire
> and full boundary suite pass

Three things are true at once, and together they make this a clear false-complete:

1. **The claim is false on the commit it shipped.** The named test asserts
   `offenders == {}`; at `4f7f0a5` there is one offender. It cannot have been
   run, or it was run against a tree that was not the one committed.
2. **The half that was true was someone else's.** The notes concede the split
   fix landed in `24f1ba4` (wq#2694). wq#2731's *entire net contribution* was
   the comment anchor.
3. **That contribution is what broke it.** The ticket existed to disarm the
   tripwire on this file; its one change re-armed it.

So wq#2731 closed on a liveness claim that its own diff falsified, and shipped a
regression in the exact class it was opened for. Flagged via `wq note` on 2731.

This does not reflect on wq#2731's *diagnosis*, which was correct, and it is a
failure the fleet cannot currently catch: nothing gates a PR on
`does_not_arm_its_own_tripwire` — `grep -rn does_not_arm_its_own_tripwire`
across `/home/lumen/bin` and `foreman/scripts` returns nothing. The test is
opt-in, so a done-note claiming it passed is unfalsifiable until a verify-pass
like wq#2737 spends a ticket on it. That is the argument for wq#2749's second
half.

## (3) The gap neither ticket asked about — 18 unguarded files

The acceptance probe for this ticket had to be able to fail, so it was aimed
*outside* the parameter the fix was designed around: `MASK_SUITE_SOURCES`
(`harness/tests/test_masking.py:165-173`) is eight hand-written paths. The
probe walks whole subtrees instead.

```
$ python3 ops/wq2739-tripwire-corpus/scan_tree.py /home/lumen/foreman fa8784d reports ops
rev fa8784d: scanned 1070 file(s) under ['reports', 'ops']
offenders: 18
  ops/minerva-ref-sync/test_minerva_ref_sync.py          kv-secret len 13
  ops/wq1252-lumen-public-guardian/tests/failure_drills.py   sk-key len 17
  ops/wq1298-gitleaks-detect/test_gitleaks_detect.py     kv-secret len 22, 23
  ops/wq1302-n8n-eval/tests/test_cron_migration_audit.py github len 36
  ops/wq1337-hapi-bringup/litellm-hapi1-dark.yaml        kv-secret len 12
  ops/wq1711-difficulty-router/difficulty_router.py      kv-secret len 14
  ops/wq1747-litellm-key-rotation/test_rotate.py         sk-key len 16, 17, 20
  ops/wq2172-mailbot-liveness/check_live.py              kv-secret len 20
  ops/wq2243-remote-credential-sweep/test_remote_credential_sweep.py  github len 36
  ops/wq2500-rsshub-bridge/miniflux_subscribe.py         kv-secret len 17
  reports/1973_verify_wq1968_repro_restart_scope.py      sk-key len 16
  reports/2172_mailbot_liveness.md                       kv-secret len 20
  reports/2355_verify_wq2290_burn_filter.md              kv-secret len 16
  reports/2630_judgment_wq2290_closure.md                kv-secret len 16
  reports/2696_verify_wq2685_rotation_burned_credential.md            kv-secret len 22
  reports/2718_verify_wq2703_rotation_burned_credential_slack.md      cloudflare len 24, sk-key len 32
  reports/2724_verify_wq2707_rotation_burned_credential_github.md     kv-secret len 17
  reports/932_verify_wq903_spend1_stragglers.md          sk-key len 20
exit 1
```

(Exact prefixes omitted here on purpose; the probe prints them.)

The last one matters most for this judgment:
`reports/2724_verify_wq2707_rotation_burned_credential_github.md` carries the
**same `kv-secret` shape, same 7-char label prefix, same length 17** that
wq#2731 put in a comment and wq#2737 caught. The class wq#2731 was closed on is
live on main this minute — in a file the test does not look at. Reading any of
these 18 files files a pri-1 "rotate today" ticket per label, which is the
entire cost model wq#2275 built the tripwire around.

The shape of the problem is self-reinforcing: every rotation verify-pass and
every judgment report quotes the shape it is reasoning about, so the population
grows each time the fleet investigates a leak, and the hand-list never follows.
Seven of the 18 are reports written by exactly that loop.

wq#2749 carries the fix: split all 18 at the boundary each rule reads (runtime
bytes unchanged), then replace the hand-list with a tree walk so a new report
cannot silently join the population. It will need an explicit skip list for the
real fixtures (`harness/tests/fixtures/wq2687_values.py`); deciding that shape
is part of that ticket, not this one.

## (4) Disposition

| | |
| --- | --- |
| wq#2737 | upheld on facts; its remedy is moot (PR merged, defect already gone) |
| PR#1093 / wq#2731 | no rework — `27b5a34` removed the offending comment with the assert it annotated |
| wq#2731 done-notes | **flagged false-complete** — named liveness command fails on the commit it shipped |
| the durable gap | wq#2749 — 18 files outside the 8-path corpus, probe committed |

This report was itself scanned with the probe before commit; it is not one of
the 18.
