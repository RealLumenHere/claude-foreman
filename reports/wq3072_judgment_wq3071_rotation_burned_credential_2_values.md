# wq#3072 — verify-pass of wq#3071 (rotation row set:b05f5da514d1, kv-secret)

**Verdict: CONTRADICT.** Escalating per archetype-8 protocol (contradiction -> opus-5@high
judgment) — though see "Existing escalation" below: this exact question already has an
open, unresolved opus-5@high judgment ticket (wq#3065) that predates wq#3071's closure.

## What wq#3071 claimed

wq#3071 closed wq#2275's pri-1 rotation row with `status=done`, no rotation, no PR:

> No rotation: mask_replay.py 3065 exit 0, 2 values, 0 unaccounted. HR_SECRET_KEY= len12
> f80e633770c8 = committed text (/home/lumen/worktrees/wq3071-a1/dispatcher/DESIGN.md:462);
> remove-key= len25 95aa1524eedf = rule-fixed (today's PATTERNS no longer read it as a
> credential). No 'also fired on' runs in notes. No code change / PR needed.

## This is the same value set wq#3062 already contradicted

`set:b05f5da514d1` is not new. wq#3061 closed this exact set the same way (`mask_replay.py
3059`, same two hashes, same "committed text at DESIGN.md:462" / "rule-fixed" gloss).
wq#3062 (verify-pass of wq#3061, same archetype as this ticket) found that closure false,
escalated, and landed `reports/wq3062_judgment_wq3061_rotation_accounted_in_false_positive.md`
(PR #1353, `main@0900be93`) plus ISSUE-LOG row 1012 — **status OPEN**, "boundary fix not
yet made." That escalation is what produced wq#3065
(`[judgment] wq#3062 verify-pass CONTRADICTED wq#3061...`, `model=claude-opus-5`,
`deps=3061`), which is **still `status=claimed`, `notes` empty, `done_at` None** as of this
verify-pass (lease valid to 2026-09-28 22:47:47, current time 2026-09-28 ~18:48). wq#3071's
detail text explains why a *third* row exists for the same set: the tripwire fired again on
"wq#3065 attempt 1's stream-json" — i.e. the still-open judgment ticket's own transcript,
discussing these values, re-armed the alarm on itself.

wq#3071's notes say "No 'also fired on' runs in notes" — it did not surface wq#3061/wq#3059/
wq#3062/wq#3065 at all. It re-ran `mask_replay.py` fresh and took the output at face value,
reaching the identical, already-debunked conclusion a second time, while the authoritative
opus-5@high judgment on the exact same question was (and still is) unresolved.

## Reproduction

```
$ python3 /home/lumen/foreman/harness/mask_replay.py 3065
wq#3065: 2 credential-shaped values in the run's model context, 0 unaccounted
  [tracked] kv-secret len=12 sha256=f80e633770c8 prefix='HR_SECRET_KEY='
      accounted: dispatcher/DESIGN.md:462
  [rule-fixed] kv-secret len=25 sha256=95aa1524eedf prefix='remove-key='
      accounted: None
EXIT: 0
```
Matches wq#3071's notes exactly — the probe reproduces. This part is real.

## The `DESIGN.md:462` citation is still false, for the same reason as wq#3062

`dispatcher/DESIGN.md:462` (current working tree, byte-identical to HEAD, no local diff):

```
2. **Lane cap defaults** in §4's config (6/4/4/4/2 per day) are placeholders scaled to
```

Frank-decision governance prose about lane cap config defaults — nothing to do with
`HR_SECRET_KEY`. Per the ticket's own sanctioned method ("sha256 a candidate and compare;
the value itself is nowhere on disk"), hashing the candidate confirms the redacted value:

```
$ python3 -c 'import hashlib; print(hashlib.sha256(b"placeholders").hexdigest()[:12])'
f80e633770c8
```

The burned value is the literal English word "placeholders", which also happens to occur
at that DESIGN.md line's offset 64 ("...are **placeholders** scaled to..."). Coincidental
match on an unrelated common word, not evidence of safe committed text — exactly wq#3062's
finding, unchanged.

## Root cause is still unpatched

- `account()` (`harness/mask_replay.py:468`, confirmed current):
  `needle = prefix + value if prefix and len(value) < TRUNCATED_FLOOR else value` —
  `TRUNCATED_FLOOR=12`, this value is exactly 12, `12 < 12` is `False`, so the prefix is
  never required and the bare word self-accounts anywhere in the tracked tree. ISSUE-LOG
  row 1012 recommended `<=`; still `<`.
- `today_closed` (`harness/mask_replay.py:705-706`, confirmed current):
  `today_closed = not today_hits or any(account(v, tracked, p) for p, v in today_hits)` —
  computed over every hit on the shared source line, not the specific (label, value) pair.
  `remove-key=`'s own `account()` call returns `None` (confirmed: it has no independent
  tracked hit); its "rule-fixed" mark rides entirely on the bogus `HR_SECRET_KEY=` match on
  the same line. ISSUE-LOG row 1012 flagged this too; still unfixed.

Both bugs are byte-for-byte the ones wq#3062 already diagnosed for this identical value
set. Nothing about wq#3071's re-run changes the analysis — same tool, same bug, same
inputs, same wrong output.

## Net effect

Neither burned value is shown safe by wq#3071's evidence, for the same reason wq#3062
already established. wq#3071 additionally skipped the "keyed by burned VALUE, not by run"
doctrine its own ticket text states — it should have found wq#3061/wq#3062/wq#3065 by
searching for `b05f5da514d1` or the two value hashes, and deferred to (or picked up) the
outstanding opus-5@high judgment rather than re-deriving and re-closing on the same flawed
probe.

## Existing escalation

This ticket's mandated escalation path (contradiction -> opus-5@high judgment) already
exists and is unresolved: **wq#3065**, `model=claude-opus-5`, `status=claimed`, lease valid.
Filing a second, parallel opus-5@high judgment ticket for the same question would just
race wq#3065. Instead: `wq review 3071 reject` was attempted (see disposition below) and a
note was appended to wq#3065 linking this second occurrence, so whichever judgment session
resolves wq#3065 has both data points.

## Falsifiability check (wq#2630)

The probe (`mask_replay.py <ticket>`) is not vacuous: it has previously exited 1 (BURNED?)
on other tickets (e.g. ISSUE-LOG row 1013/wq#3063: `mask_replay.py 3036` exits 1, 6
BURNED?) and distinguishes `[tracked]`/`[rule-fixed]`/`[BURNED?]` by real hash comparison
against the tracked tree, not a fixed pass. The failure here is not that the probe can't
fail — it's that `account()`'s exactly-at-floor boundary makes it pass when it shouldn't
for this specific 12-char value.

## Evidence trail (no credential values printed as literals)

- `python3 /home/lumen/foreman/harness/mask_replay.py 3065` — exit 0, output above.
- `dispatcher/DESIGN.md:462` read directly (working tree, allowed dir); `git status
  --short -- dispatcher/DESIGN.md` empty.
- Candidate hash check: `hashlib.sha256(b"placeholders").hexdigest()[:12] ==
  "f80e633770c8"` — the only "secret" value handled was the word "placeholders" itself,
  sourced verbatim from committed, public `DESIGN.md` text, per the ticket's own sanctioned
  method.
- Direct read of `mask_replay.py:468` and `:705-706` confirm both bugs ISSUE-LOG row 1012
  named are still present, unmodified since wq#3062.
- `wq show 3061/3062/3065/3071` — status/notes fields as quoted above.
- `/home/lumen/bin/wq list --all | grep -i "b05f5da514d1\|f80e633770c8\|95aa1524eedf"` — no
  other rows.

## Disposition

`wq#3072` closes CONTRADICT. wq#3071's closure does not stand as stated — same unresolved
false positive as wq#3062 already found for this value set, with the authoritative
opus-5@high judgment (wq#3065) still open. ISSUE-LOG row 1016 appended to record the
repeat.
