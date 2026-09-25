# wq#2732 — judgment: wq#2726's contradiction of wq#2687 / PR#1081

**wq#2726 is UPHELD, and the ticket's framing is wrong in the way that matters.**
The question it asks is "should PR#1081 be rejected before merge". PR#1081 is
already merged and already deployed, so the answer is not a merge decision — it
is a live-leak fix, and this ticket carries it.

## (0) The premise the ticket got wrong

| claim in wq#2732 | actual |
| --- | --- |
| "not yet merged to main" | `128f995` is an ancestor of `origin/main` (`git merge-base --is-ancestor`) |
| "no live exposure from THIS PR" | `/home/lumen/bin/mask-secrets` — the only deployed copy — carries the alnum-tail rules |

Probed against the deployed file, not the branch:

```
$ python3 /tmp/fworker-wq2732/probe_alnum_tail.py /home/lumen/bin/mask-secrets
  FAIL cfut tail _        -> 'tok cfutMASKED_ end'
  FAIL cfut tail .        -> 'tok cfut_MASKED. end'
  FAIL cfut tail -        -> 'tok cfut_MASKED- end'
  FAIL kv floor tail _    -> the 12-char value echoed back verbatim, nothing masked
  FAIL kv long tail _     -> 'TOKEN: MASKED_ end'
```

The `cfut` row is worse than wq#2726 reported: leftmost-longest gives up the
prefix's own underscore too, so the output is `cfut` + `MASKED_` — the mask moves
one character left and the real trailing character is printed.

wq#2726's technical account is otherwise exactly right, including the diagnosis
that `masking.py`'s `_body()` lookahead and a last-character constraint are not
the same mechanism.

## (1) Verdict on PR#1081

Reject the approach, fix forward. A revert to `92e5fa6^` would close the leak but
reopen wq#2687 (a sentence-final period eaten off already-masked prose), so this
ticket ships the correct mechanism instead.

## (2) The correct fix shape: a sentinel, not a cleverer pattern

The lesson of this bug is that **any mechanism which can end a match part-way
through a value leaks the tail**, because what is left over is the real secret.
So the run must stop where the *charset* ends and nowhere else. `masking.py` gets
that from a per-character lookahead that the literal `MASKED` cannot be entered
through. POSIX ERE has no lookaround — but it does not need one, because the
literal is our own output and we control when it exists:

```sed
s/MASKED/\x01/g          # our output leaves the alphabet
… every rule replaces with \x01, not MASKED …
s/\x01/MASKED/g          # and comes back
```

SOH is in no rule's body charset, so a greedy run stops at our own output for the
same structural reason `_body()` does, and the original `{6,}`/`{12,}` floors come
straight back — no floor arithmetic, no tail constraint. It also covers a case the
prefix-side `|MASKED` alternative never could: a literal emitted by an *earlier
rule in the same pass*, which the next rule's charset would otherwise swallow.

Three things fall out of it:

- the `|MASKED` boundary alternative (wq#2697) is deleted — after the swap the
  character in front of a glued value is `\x01`, which `[^A-Za-z0-9_]` already
  admits;
- the last divergence from `masking.py` closes. `cfut_MASKED` + a live value used
  to be swallowed whole into `cfut_MASKED`; it now masks the value on its own,
  byte-identical to `mask_text()`;
- one cost, in the safe direction: a literal SOH already in the input comes out as
  `MASKED`.

## (3) Rotation

**No rotation indicated, and no rotation ticket filed.**

- Exposure window: deployed file mtime `2026-09-25 17:43:04Z` to this fix. (The
  mtime predates `128f995`'s commit date — the bundle was installed from a
  worktree before it was committed. The file's *content* is the evidence.)
- Only one deployed copy exists: `grep -rl "xox\[bpasr\]"` across `/home/lumen/bin`,
  `agent`, `minerva`, `mailbot` returns `/home/lumen/bin/mask-secrets` alone.
- Residue scan for the leak signature (`MASKED` immediately followed by `_` or `-`,
  which a correct match would have consumed) across `/home/lumen/logs`, `kb`,
  `agents/work-queue`, `minerva`, `agent` and this repo: every hit is a synthetic
  fixture in wq#2726's own writeup or its ISSUE-LOG row. The one other file,
  `logs/supergateway/desktop-commander-mcp.log`, is denied by `guard.sh` and holds
  unmasked tool-call bodies by construction (wq#2546) — nothing there is
  attributable to this bug.

What I could **not** do, stated plainly: enumerating live credential values to
check which ones end in `.`/`_`/`-` means reading the credential inventory, which
instinct 4 and `guard.sh` both forbid, and correctly. The residue scan is the
strongest evidence available from inside a build worker, and it is clean.

## (4) Why 30 green tests shipped this

Every fixture in `test_mask_secrets_boundary.py` ended in an alphanumeric — the
exact population the alnum-tail bound was written around, so no test could have
failed (wq#2630). The bundle also had no probe against `harness/masking.py`, the
oracle its own header calls the thing it is "in lockstep" with.

`test_matches_masking_py_exactly` is that probe: 18 lines through both
implementations, asserted byte-equal, oracle not derived from the sed rules.
It falsifies —

```
$ python3 /tmp/fworker-wq2732/falsify.py <git show 128f995:…/mask-secrets>
  6/18 lines diverge from masking.py      # incl. the total-miss leak
$ python3 /tmp/fworker-wq2732/falsify.py ops/wq2316-mask-secrets-boundary/mask-secrets
  0/18 lines diverge from masking.py
```

`57 passed` for the bundle, `74 passed, 1 xfailed` for `harness/tests/test_masking.py`.
Neither changed file arms the mask tripwire (`masking.scan(...)` → `[]` on both).

## Left open

`/home/lumen/bin` is a primary tree and `guard.sh` denies build workers Write/Edit
there, so **the deployed copy is still the leaking one** until
`bash ops/wq2316-mask-secrets-boundary/install.sh` runs from an unguarded session
(orchestrator, `lumen-run`, or Frank). ISSUE-LOG row 826.

Incidentally resolved: my rewrite of
`test_trailing_punctuation_after_masked_literal_survives` deletes the comment
`4f7f0a5` (wq#2731) added, which is the text wq#2737 found re-arming the tripwire.
The assertion it guarded went with it — it encoded a behaviour `masking.py` does
not have, a sentence period surviving after a **raw** value, and that overreach
from "after our own literal" into "after any value" is where the leak came from.
