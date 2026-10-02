# wq#3407 — judgment: wq#3406 CONTRADICT of wq#3404 (`--verify --deps` exit code)

**Verdict: upheld. Fix applied.**

wq#3406 (`reports/wq3406_verify_wq3404_emba_deps_exit_code.md`, ISSUE-LOG #1169) is
right on the contract and right that it matters. The `--deps` arm wq#3404 added to
`ops/wq3383-emba/install.sh` *did* look — `command -v docker` and `docker image
inspect` both ran and returned a conclusive negative — and then reported that
negative with `_V_ERR=1`, so `vdone` exited 2. `ops/_verify.sh:8-9` and the
`vremote_up` note at `:85-87` reserve exit 2 for "I could not look" and are explicit
that it "must not read as it is not deployed"; `ops/VERIFY_CONTRACT.md:14-15,:47`
says the same, and `vflag`'s own comment (`_verify.sh:70-72`) already settles the
analogous case the other way: a build too old to answer `--help` is a MISS, because
that is an answer.

## Why it was not waved off as not-yet-wired

`harness/liveness.py:442-451` maps rc=1 to STILL_BLOCKED and anything else to
UNKNOWN. Nothing calls `--verify --deps` through it today (`ops_verified()` hardcodes
`["bash", inst, "--verify"]`), so wq#3406 is correct that no live dispatch is wrong
yet. But the cost asymmetry is lopsided: the fix is one line inside a branch nobody
else calls, while the latent failure is a *silent* one — the dependency is correctly
diagnosed as blocked and the probe reports it as indeterminate, which is the failure
mode liveness exists to prevent. The README's "`--verify --deps` is the one-line
confirmation that it worked" framing is an open invitation to wire exactly that.
Fixing an unwired flag is cheap; finding this again through a mis-dispatched probe is
not.

## Change

- `ops/wq3383-emba/install.sh:55-70` — the absent arm calls
  `_vmiss "DEPS MISSING: ..."` instead of `echo` + `_V_ERR=1`, with a comment
  recording why both arms of the test are MISS and not ERR.
- `harness/tests/test_wq3407_emba_deps_exit_code.py` — regression test, `docker`
  shimmed on PATH so both arms are deterministic on any host (including one that
  really has the image pulled).
- `ops/wq3383-emba/README.md:107-111` — documents the exit code and why.

Both arms are MISS deliberately. No `docker` at all is not "I could not look": the
only supported dependency path installs `docker-ce` (`install.sh:11-12`), so its
absence means `installer.sh -d` cannot have completed — a conclusive negative about
the thing being verified.

## Residual, recorded rather than silently branched

One genuine could-not-look case stays a MISS: `docker` present but its daemon down or
socket permission-denied, where `image inspect` fails for a reason unrelated to the
image. Splitting that out means parsing docker's stderr, adding a branch untestable
from this sandbox, and it is arguable in the other direction too (a dead engine means
emba cannot run either, so STILL_BLOCKED is the more useful answer for liveness).
Left as-is on purpose; revisit if a real deployment hits it.

## Evidence

Pre-fix vs post-fix on identical input, pre-fix bundle reconstructed rev-pinned from
`8223e059` (`/tmp/fworker-wq3407/falsify.py`):

```
pre-fix 8223e059   rc=2  VERIFY ERROR
fixed (worktree)   rc=1  VERIFY FAIL (1)
```

```
$ python3 -m pytest harness/tests/test_wq3407_emba_deps_exit_code.py \
                    harness/tests/test_liveness_ops_verified.py -q
14 passed
```

Four-case live probe (`/tmp/fworker-wq3407/probe.py`), all PASS: base `--verify` → 0
`VERIFY OK`; `--verify --deps` on this docker-free box → 1 `MISS DEPS MISSING`;
shimmed docker reporting the image present → 0 `DEPS OK`; shimmed docker reporting it
absent → 1. The shimmed cases probe from outside this box's default population
(no container engine), so the probe could have failed — and against the pre-fix file
it does.
