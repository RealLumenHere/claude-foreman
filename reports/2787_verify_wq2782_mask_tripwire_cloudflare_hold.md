# wq#2787: verify-pass of wq#2782 (mask tripwire: file cloudflare rotation rows frank-gated)

**Verdict: CONTRADICT.** wq#2782's fix files a `[rotation]` row frank-gated (and refuses
its dispatch) only when the mask hit names Cloudflare by the literal substring
`cloudflare`. The fleet's own live config already leaks a CF credential under a name
that doesn't contain that word, so this defeats the hold ISSUE-LOG 850 exists to enforce.
Escalated to opus-5@high as wq#2843.

## What wq#2782 shipped (PR#1125, `f091c5b261`)

- `harness/fworker.py:1144`: `CF_ROTATION_RE = re.compile(r"cloudflare", re.I)`
- `harness/fworker.py:1147-1151`: `is_cf_rotation(labels, records)` — true if the
  literal regex matches any label, or any record's masked `context` string.
- `dispatcher/foreman_dispatcher.py:172`: `CF_ROTATION_HOLD_RE = re.compile(r"\[rotation\].*\bcloudflare\b", re.I)`
  — second wall, refuses dispatch of any open `[rotation]` row whose *title* matches.
- Both regexes are gated on the same one word.

The commit's own comment (fworker.py:1139-1143) claims wider coverage than the regex
delivers: *"a CF API token/zone id/account id caught by variable name lands under the
generic kv-secret label instead... so both are checked."* — true that the `context`
window is checked, false that `CF_ROTATION_RE` recognizes a CF resource named by
abbreviation rather than by the word "cloudflare".

## Breaking it: a real, live counterexample already in this repo

```
$ grep -n "CF_API_TOKEN=" /home/lumen/foreman/ops/wq2608-dc-endpoint-bes-a1/dns_access_setup.sh
46:CF_API_TOKEN=
```

This is not a hypothetical shape — it's a variable name already live in the fleet's own
ops scripts, standing right alongside `CLOUDFLARE_API_TOKEN` (wrangler's own convention,
used elsewhere in this repo, e.g. `ops/wq2172-mailbot-liveness/install.sh:15`). Both
names are equally plausible to appear in a future leak.

Reproduced end to end against the shipped code, no modifications:

```
$ python3 -c "
import sys; sys.path.insert(0, 'harness')
import masking
_, records = masking.scan('CF_API_TOKEN=' + 'abcdef0123456789abcdef0123456789abcd')
print(records)
"
[{'label': 'kv-secret', 'prefix': 'CF_API_TOKEN=', 'value_len': 36,
  'value_sha256_12': '30f8df099069', 'context': 'CF_API_TOKEN=MASKED'}]

$ python3 -c "
import sys; sys.path.insert(0, 'harness')
import fworker
records = [{'label': 'kv-secret', 'context': 'CF_API_TOKEN=MASKED'}]
print(fworker.is_cf_rotation(['kv-secret'], records))
"
False
```

`is_cf_rotation` returns `False` — no `--frank-gated`. Ran the full `mask_alarm()` path
(monkeypatching `wq`/`ntfy`/`wq_show` the same way the shipped tests do) on this exact
input:

```
add args: ('add', '--lane', 'fleet', '--pri', '1', '--title',
  '[rotation] burned credential sha256:30f8df099069 (kv-secret)', ...)
frank-gated present: False
```

The row files **open**, titled `... (kv-secret)` — no "cloudflare" substring anywhere in
it, so the dispatcher's second wall (`CF_ROTATION_HOLD_RE`, keyed on the *title*) also
misses it:

```
$ python3 -c "
import re
title = '[rotation] burned credential sha256:30f8df099069 (kv-secret)'
print(bool(re.compile(r'\[rotation\].*\bcloudflare\b', re.I).search(title)))
"
False
```

Both walls wq#2782 built are open. A `CF_API_TOKEN` leak dispatches to a fire-and-forget
worker exactly as Frank's 2026-09-25 hold (ISSUE-LOG 850 rule c) forbids — the precise
failure mode wq#2782 was written to close.

## Why the shipped tests didn't catch this

Every test wq#2782 added (`test_is_cf_rotation_matches_the_cloudflare_label`,
`test_is_cf_rotation_matches_a_cf_resource_under_another_label`,
`test_mask_alarm_files_a_cloudflare_row_frank_gated`,
`test_cf_rotation_hold_title_skipped`) probes a value or context string that already
contains the literal word "cloudflare"/"CLOUDFLARE_ZONE_ID". None uses the abbreviated
`CF_` shape the commit's own prose says it covers. This is the wq#2290/wq#2319 pattern
the ticket doctrine warns about: a probe drawn only from the population the fix was
designed around confirms and can never falsify. All four tests still pass unmodified —
run today:

```
$ python3 -m pytest harness/tests/test_masking.py -k "cf_rotation or cloudflare" -q
4 passed
$ python3 -m pytest dispatcher/tests/test_dispatcher.py -k cf_rotation -q
1 passed
```

Passing is expected and uninformative — the probe cannot fail by construction.

## Scope note (not part of this verdict)

`python3 -m pytest harness/tests/test_masking.py -q` (full suite, current `main` tip)
has one unrelated failure, `test_the_mask_suite_does_not_arm_its_own_tripwire`, caused
by files added in a later commit (`56a720d`, after `f091c5b261`) — confirmed
`reports/2756_rotation_triage.md` does not exist at wq#2782's own commit
(`git cat-file -e f091c5b261:reports/2756_rotation_triage.md` fails). Pre-existing
drift, unrelated to wq#2782's diff; not part of this verify-pass.

## Disposition

- `kb/ISSUE-LOG.md` row 869.
- Escalated to opus-5@high: wq#2843 — widen `CF_ROTATION_RE` (and
  `CF_ROTATION_HOLD_RE`) to also catch a bare `CF[_-]` variable-name shape without
  false-firing on unrelated `CF`-prefixed identifiers, with a probe keyed on
  `CF_API_TOKEN`/`CF_ZONE_ID`/`CF_ACCOUNT_ID` (not `CLOUDFLARE_*`) that can actually fail.
- Not re-implementing here per this ticket's own instruction (verify only).
