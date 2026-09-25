# wq#2350 — verify-pass (archetype 8) of wq#2298 (PR #819, `4a1874a`/`99ae7fb`)

Verifier: claude-sonnet-5@high, fresh instance (author was claude-opus-5).
Verdict: **CONTRADICTED** (two real defects found in the fixed-point invariant;
the false-positive/no-rotation-needed part of wq#2298's original claim is
correct). Escalated as **wq#2638** (opus-5@high judgment, `deps=2298`).

## What wq#2298 shipped

`harness/masking.py` (+41), `harness/tests/test_masking.py` (+50),
`token-engine/token_engine_daemon.py` (+9). Claim: the `github`/`ghp_MASKED`
alarm on wq#2292 was a false positive (the masker re-arming on its own
previous output), and the fix — `_matches()`/`_sub()` drop any regex match
whose `.group(0)` ends in the literal `MASKED`, on the theory that such a
match is always the module's own prior replacement, never a live secret —
makes `mask_text`/`scan` a fixed point on their own output.

## Confirmed correct

- **The false-positive diagnosis for wq#2292.** Independently recomputed:
  `sha256("MASKED")[:12] == 20227a136ec3` and the HiGHS-solver-tail hash match
  the values the ticket cites. No credential existed in that transcript.
- **The core mechanism, for the common case.** `pytest harness/tests/test_masking.py -q`
  on current HEAD: 1 failure, and it is unrelated to this diff (see below) —
  `test_masking_never_re_arms_on_its_own_output` and
  `test_a_real_secret_next_to_a_masked_one_still_fires` both pass. Ordinary
  `mask(mask(x))` for every rule in `SECRETS` is genuinely idempotent, and a
  live token sharing a *line* with an already-masked value (separated by
  ordinary punctuation/whitespace) still fires. The property test's own
  framing — "a match ending at the mask literal is our own output, never a
  secret" — is the exact invariant that breaks below.

## Contradiction found — two failure modes, one root cause

The invariant silently assumes the mask literal `MASKED` is always bordered
by a character *outside* the matching rule's own body charset. That holds
for `github`/`aws`/`slack` (fixed-width bodies, or a body charset that
excludes `_`/`-`), but not for `cloudflare`, `kv-secret`, or `sk-key`, whose
body classes (`[A-Za-z0-9_.-]{6,}`, `[A-Za-z0-9_-]{16,}`) are supersets of
their own prefix syntax.

**(1) Re-arm (noisy, not unsafe) — the direction wq#2298 already knew about
but didn't fully close:**

```
>>> masking.mask_text("the value read back as cfut_MASKED.")
('the value read back as cfut_MASKED.', ['cloudflare'])
```

Trailing prose punctuation (`.`, `_v2`, `-v2`) is inside the cloudflare body
charset, so the greedy match runs past the literal and `m.group(0)` no
longer ends in `MASKED` — it re-fires. This exact regression is already
tripping `test_doctrine_corpus_does_not_re_arm_the_alarm` on current HEAD
(kb/ISSUE-LOG.md's own prose about this bug re-arms the alarm it describes).

**(2) Leak (severe) — the direction that matters more:**

```
>>> masking.mask_text("cfut_REALSECRETVALUE123456cfut_MASKED")  # secret-scan:allow (synthetic, not a live token)
('cfut_REALSECRETVALUE123456cfut_MASKED', [])  # secret-scan:allow
```

A real secret concatenated with **no separator** immediately *before* an
already-masked value of the same rule's shape merges into one regex match
whose tail is the literal. `_matches()` drops the **whole match** — the live
secret included — with zero alarm and zero masking. `scan()` records
nothing either. Confirmed this is a **regression**, not a pre-existing gap:
the same input against the parent commit (`99ae7fb^`, pre-fix) masks and
alarms correctly:

```
>>> masking_prefix.mask_text("cfut_REALSECRETVALUE123456cfut_MASKED")   # 99ae7fb^  secret-scan:allow
('cfut_MASKED', ['cloudflare'])
```

`sk-key` is affected identically — this **corrects** a claim in this
ticket's own killed attempt-1 draft (which asserted `sk-key` was safe as
"fixed-width or narrower"): its body (`[A-Za-z0-9_-]{16,}`) includes the
hyphen/letters its own `sk-(?:[a-zA-Z]+-)*` prefix uses.

```
>>> masking.mask_text("sk-ant-REALSECRETVALUE1234567890sk-ant-MASKED")  # secret-scan:allow (synthetic)
('sk-ant-REALSECRETVALUE1234567890sk-ant-MASKED', [])  # secret-scan:allow
```

Reproduction scripts: `/tmp/fworker-wq2350/probe_concat.py`,
`probe_concat2.py` (sk-key), `probe_concat_pre.py` (parent-commit control).
Kept worker-local in `/tmp`, not committed.

## Why this is realistic, not contrived

Instinct 4 ("a credential printed is a credential burned — rotate it same
day") is the whole reason this subsystem exists. Case (2) defeats both the
mask *and* the alarm silently, for exactly the artefact shapes this fleet
produces constantly: log tails where a new token is quoted right after a
previously-masked one, diff hunks, JSON without inter-field whitespace,
repeated `grep -o` output joined without a delimiter. A worker or a human
reading such an artefact would see the live secret in plaintext with no
signal that masking ever ran on it.

## What I tried to break, that held up

- Whether `github`/`aws`/`slack` share this exposure — no: fixed-width
  bodies (`aws`), or bodies excluding `_`/`-` from the legal charset
  (`github`'s `[A-Za-z0-9]{30,}`, `slack`'s `[A-Za-z0-9-]{8,}` — hyphen only,
  and `xox[bpasr]-` prefixes don't recur mid-body the same way) can't chain
  a prefix occurrence into the same match this way.
- Whether the daemon mirror (`token_engine_daemon.mask_hits`) independently
  avoids this — no, it has the identical `endswith(MASK)` filter and the
  identical exposure (not separately re-verified with a live probe here,
  since the logic is textually identical and the risk is already proven at
  the shared root).

## Disposition

Per doctrine ("do not re-implement; verify"), no fix was written here.
`wq review 2298 reject` was attempted first and refused (`wq#2298 is 'done' —
review only acts on pending_review tickets`); recorded the verdict via
`wq note 2298 ...` instead. Filed **wq#2638** (lane build, pri 1,
`model=claude-opus-5`, `effort=high`, `deps=2298`) with full repro and a
concrete fix direction (bound the fixed-point check to the exact known
prefix+literal, or cap the greedy body class with a lookahead so it cannot
run through a second prefix occurrence; add adjacency property tests in both
directions for every charset-widening rule). ISSUE-LOG row 724 corrected and
completed (kb/ISSUE-LOG.md, append-only edit per doctrine — no local commit
in that primary tree).
