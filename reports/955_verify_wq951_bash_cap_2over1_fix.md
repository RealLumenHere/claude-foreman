# wq#955 — verify-pass: wq#951 guard.sh `2>&1`/SKIP_CAP fix (PR #145)

**Verdict: CONFIRM** — the shipped regex fix is correct, the shipped test additions pass, and
the claimed residual-leak numbers reproduce independently against the live corpus. One
secondary observation below (not a contradiction of this ticket's claim).

PR: http://100.121.17.5:3000/lumen/claude-foreman/pulls/145 (merged as `0114d79`).

## What the diff does

`harness/guard.sh`'s Bash-cap SKIP_CAP logic exempted any command containing an `&` not
part of `&&`, meant to catch real backgrounding (`cmd &`). The regex
`(^|[^&])&([^&]|$)` also matched `2>&1` — treating fd-duplication as backgrounding — and
guard.sh's own cap wrapper emits `{ cmd } 2>&1 | head -c N`, so the clause fired on the
idiom it writes itself. Fix narrows both sides of the pattern to exclude `<`/`>` adjacency:
`(^|[^&<>])&([^&>]|$)`.

## Verification performed

1. **Read the diff cold** (`git show 0114d79` against a fresh checkout of `origin/main`,
   not the stale `/tmp/wq952-review` checkout another session had mid-rebase on a
   different PR — confirmed I was looking at the right commit before trusting any result).
2. **Ran the shipped test suite**: `harness/tests/test_guard.py`, 36/36 pass, including the
   5 new fd-dup regression cases (`make build 2>&1`, `pytest -q 2>&1 | tail -20`,
   `python3 script.py >&2`, `./run.sh &>/tmp/out.log`, `diff a b 1>&2`) and the pre-existing
   real-background cases (`some-long-job.sh &`, `sleep 5 & wait`).
3. **Adversarial probe** (13 hand-built cases beyond the shipped suite, run directly against
   the shipped `guard.sh` binary via its actual PreToolUse envelope, not just regex tracing):
   fd-dup-only forms (`>&2`, `<&0`, `&>file`, `3<&-`), fd-dup combined with a *real* trailing
   background (`cmd 2>&1 &`, `cmd 2>&1&`, `nc -l 1234 <&0 &`), bare backgrounding
   (`sleep 5 &`, `(cmd1 && cmd2) &`), and the logical-AND case (`cmd1 && cmd2`). All matched
   expected classification. The one apparent mismatch (`curl ... "?a=1&b=2" 2>&1` still
   exempted) is the pre-existing, documented, intentionally-conservative quoted-`&` skip
   (`CAPPABLE_BUT_CONSERVATIVELY_SKIPPED` in the test file) — unchanged by this diff, not a
   new defect.
4. **Reran `research/uncapped_bash_probe.py`** (the shipped reproduction script) against the
   live `_runs` corpus (`/home/lumen/agents/work-queue/_runs` +
   `/home/lumen/foreman/_runs`, 47 sessions). Result: 728 over-cap results / 3,601,617 B,
   residual leak after the fix = **168 results / 725,170 B = 20.1%** — matches
   `research/UNCAPPED_BASH.md`'s claimed 44.4% → 20.1% drop and wq#951's done-notes almost
   exactly (the corpus grew by a handful of sessions since wq#951 ran it, which the doc
   itself explains: 688/3.40 MB vs this run's 728/3.60 MB, work-queue-only vs both roots).

## Observation (not a contradiction)

`research/native_bash_cap_test.py` (shipped in this same PR, in support of the doc's claim
(b)) drives its probe command as `python3 -c "print('x'*8000)" 2>&1`, and its docstring
states this `2>&1` is what "trips guard.sh's SKIP_CAP background clause... so the `head -c`
rewrite is NOT applied." That was true before this diff; after it, it's false — I confirmed
directly (fed the exact command through the fixed `guard.sh`) that it now gets the
`head -c 2000` wrapper applied, same as any other capped command. Re-running that script
today, per its own docstring's `Reproduce:` line, would no longer isolate claude-code's
native truncation the way it claims to — guard.sh's own rewrite would contaminate arm A.

Not flagging this as a contradiction of wq#951's claim: (b) itself isn't false, only the
one script's self-contained reproducibility is now stale, and wq#954 (deps: 951, already
claimed) plans to delete guard.sh's entire SKIP_CAP/head-c mechanism outright — at which
point this staleness resolves itself rather than needing an independent fix. Worth a note
if wq#954's implementer touches this file, not worth a ticket on its own.
