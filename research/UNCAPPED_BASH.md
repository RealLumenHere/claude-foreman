# wq#951 — the 688 uncapped Bash results: where they came from, what still leaks

Seeded by wq#888 (`research/SMARTCRUSHER_HOOK.md` §5), which found 688 Bash tool
results above the wq#729 2000-byte cap totalling 3,403,898 B — 70% of all Bash
result bytes in a fleet that is supposed to cap Bash at 2000 — and asked two
questions it had no quota to answer.

## VERDICT

**(a) Every one of them predates the cap, and 20% of the class would still leak
today.** The corpus is 47 sessions spanning 2026-08-15 .. 08-19; wq#729's cap
landed on main 2026-08-21 05:08 UTC. Not one post-cap transcript exists anywhere
on disk, so the date split alone proves nothing about the present. Replaying
every over-cap command back through the live guard does: 44.4% of the 3.4 MB
would still be exempted.

**(b) Yes — claude-code honours `BASH_MAX_OUTPUT_LENGTH` natively.** Read out of
the 2.1.233 binary and confirmed by a two-arm live session. Default 30000
characters, upper limit 150000, no lower limit.

**And the thing neither question asked:** the guard's background-job exemption
was matching `2>&1`. Of the 338 commands `SKIP_CAP` exempted, 245 were claimed by
the `&` clause and **not one was a background job** — every match was a
file-descriptor duplication. That single false positive was worth 875 KB, 24.3%
of the 3.6 MB, and guard.sh's own cap wrapper emits `2>&1`, so the clause was
firing on the idiom it writes. Fixed in this branch; residual leak drops from
44.4% to 20.1%.

---

## 1. The date split — the corpus is entirely pre-cap

`research/uncapped_bash_probe.py`, over both `_runs` roots (wq#888 used the
work-queue root alone; adding `/home/lumen/foreman/_runs` brings 8 more sessions,
which is why the totals here are 728/3.60 MB rather than 688/3.40 MB):

```
corpus: 47 sessions, 3977 Bash results, 728 over the 2000 B cap, 3,601,617 B

wq#729's cap landed on main 2026-08-21 05:08 UTC (56c9d51, deployed from /home/lumen/foreman)
  sessions BEFORE the cap    47  (728 over-cap results)
  sessions AFTER the cap      0  (0 over-cap results)
  pre-cap  session span: 2026-08-15 .. 2026-08-19
```

The guard is deployed straight from the primary tree
(`worker-settings.json` → `/home/lumen/foreman/harness/guard.sh`), so landing on
main *is* deployment — no separate rollout to date against.

The gap matters more than the split: **the fleet keeps no post-cap tool-result
transcripts.** `fworker.py` moved to per-ticket `_runs/wq{id}/attempt-*.json`,
which records classification, exit code and usage but no message content. So
there is currently no way to observe the cap working — or not — in production,
and no way to re-measure this without a replay. That is why §2 replays instead of
waiting for fresh data.

## 2. The replay — what today's guard actually does with those commands

Session dates say what the corpus *is*; they cannot say what the guard *would
do*. So the probe feeds every over-cap command back through the live `guard.sh`
as a real PreToolUse envelope (events redirected to a scratch dir; the guard is
otherwise a pure decision function) and classifies the verdict it gets now.

Against `main`'s guard:

| verdict | results | bytes | share |
|---|---:|---:|---:|
| capped | 383 | 1,971,265 | 54.7% |
| **exempt (`SKIP_CAP`)** | **338** | **1,600,253** | **44.4%** |
| blocked | 7 | 30,099 | 0.8% |

So wq#729 catches a bare majority of the class it was written for, and 1.6 MB —
44% — still walks through. wq#888 put the `SKIP_CAP` share at 104 results
(the `cd`/`export` command heads); the replay puts it at 338. Command *heads*
undercount it badly, because the clause fires on a verb or an `&` anywhere in the
string, not just in first position.

Splitting the 338 by which clause claims them is where the real finding is:

| claimed by | results | bytes |
|---|---:|---:|
| `&` clause, fd-duplication only | 170 | 875,083 |
| shell-state verb only | 93 | 459,363 |
| both | 75 | 265,807 |
| `&` clause, genuine background job | **0** | **0** |

## 3. `2>&1` is not a background job

```sh
grep -qE '(^|[^&])&([^&]|$)'
```

The clause means "a backgrounded job — piping it would subshell the `&`". The
regex means "any `&` that is not part of `&&`". `2>&1` is an `&` that is not part
of `&&`. So is `>&2`, `<&0`, `&>file`. Every one of the 245 matches in the corpus
was one of those; the corpus contains no over-cap background job at all.

It is worse than an ordinary false positive, because the fix guard.sh applies is

```sh
{ $RAW_CMD
} 2>&1 | head -c $CAP_BYTES
```

— the clause was declining to cap commands for containing the exact redirect the
capper writes. Any worker that typed `2>&1` (and workers type it constantly, the
prompt having taught them to narrow their own output) silently opted out of the
cap.

The fix is to exclude `&` adjacent to a redirect:

```sh
grep -qE '(^|[^&<>])&([^&>]|$)'
```

`&&` was already excluded by the leading class; this adds `>`/`<` before and `>`
after, killing `2>&1`, `1>&2`, `>&2`, `<&0` and `&>f`. A bare `&` inside a quoted
string still skips — over-cautious in the safe direction, unchanged. Replaying
the same corpus through the fixed guard:

| verdict | results | bytes | share |
|---|---:|---:|---:|
| capped | 553 | 2,846,348 | 79.0% |
| exempt (`SKIP_CAP`) | 168 | 725,170 | 20.1% |
| blocked | 7 | 30,099 | 0.8% |

**875,083 B recovered, one character class, no behaviour change for any real
background job** — the `&` clause now claims nothing in the entire corpus, which
is the correct answer for a corpus with no backgrounded over-cap command in it.
`harness/tests/test_guard.py` gains five fd-duplication cases to `CAPPABLE`.

## 4. `BASH_MAX_OUTPUT_LENGTH` — honoured, and it closes the rest

### What the binary does

`claude.exe` 2.1.233, offset 296274557:

```js
function wRt(){ return ZKe("BASH_MAX_OUTPUT_LENGTH", process.env.BASH_MAX_OUTPUT_LENGTH, Cga, kga).effective }
var kga = 150000, Cga = 30000;
```

`ZKe(name, raw, default, upperLimit)`, defined immediately above it:

- unset → `default`
- unparseable or `<= 0` → `default`, with a warning
- `> upperLimit` → clamped to `upperLimit`
- otherwise → the value verbatim

**No lower bound.** `BASH_MAX_OUTPUT_LENGTH=2000` is a valid, effective setting.

The truncator (offset 296498237) is head-truncation on the assembled result, by
*characters*:

```js
let r = wRt();
if (e.length <= r) return { truncatedContent: e, ... };
let n = e.slice(0, r), o = <lines after r>;
let i = `${n}\n\n... [${o} lines truncated] ...`;
```

The Bash tool's own description interpolates the same getter — "If the output
exceeds `${wRt()}` characters, output will be truncated before being returned to
you" — so lowering the variable also lowers the number the model is told.

One corroborating detail from §1: the largest single result in 3977 is 29,649 B,
just under the 30000 default. The native cap was already the binding limit on the
whole corpus; wq#729 was the second cap, not the first.

### Live confirmation

`research/native_bash_cap_test.py` — two headless Haiku sessions, same 8000-char
command, real `worker-settings.json` so guard.sh is live, command ending in
`2>&1` so `SKIP_CAP` holds the `head -c` rewrite off and claude-code is the only
thing that can truncate:

```
payload: 8000 chars, guard.sh live (SKIP_CAP via `2>&1`)

  A-unset  BASH_MAX_OUTPUT_LENGTH=unset  result=  8000 chars  guard-rewrote=False
  B-2000   BASH_MAX_OUTPUT_LENGTH=2000   result=  2229 chars  guard-rewrote=False
```

2000 characters of payload plus a 229-character truncation notice. Confirmed.

### Why it closes `SKIP_CAP` for free

A native cap truncates the *result*; the `head -c` rewrite truncates the
*pipeline*. That difference is the whole reason `SKIP_CAP` exists — you cannot
pipe `cd /tmp` without subshelling the `cd` — and it evaporates when the cap
moves out of the command. Set `BASH_MAX_OUTPUT_LENGTH` in the worker environment
and every one of the residual 168 results (725,170 B, all shell-state verbs) is
capped too, with no way for a command shape to opt out.

## 5. Recommendation

- **Landed here:** the `2>&1` fix (875 KB, 24.5% of the class), the replay probe,
  the live test, this record.
- **Next, wq#954:** move the cap to `BASH_MAX_OUTPUT_LENGTH` in `fworker.py`'s
  worker env and delete the `head -c` rewrite and `SKIP_CAP` from `guard.sh`.
  Not folded in here because it is a fleet-wide behaviour swap with two decisions
  attached: the truncation unit changes from stdout bytes to whole-result
  characters, and dropping the `{ ...\n} 2>&1` wrapper stops merging stderr into
  stdout. Both are probably improvements; neither should ride in on an
  investigation ticket.
- **Also next:** the fleet cannot observe its own tool-result sizes any more.
  Either restore a content transcript under `_runs/wq{id}/`, or have `fworker.py`
  record per-call result byte totals in the usage sidecar. Every measurement in
  this document is a replay of a corpus that stops on 2026-08-19.
- **Not worth doing:** nothing here needs compression. wq#888's conclusion holds —
  the bytes are a correctness problem, not a compression problem.

## Reproduce

```sh
python3 research/uncapped_bash_probe.py                                  # this branch's guard
WQ951_GUARD=/home/lumen/foreman/harness/guard.sh \
  python3 research/uncapped_bash_probe.py                                # the deployed guard
python3 research/native_bash_cap_test.py                                 # two Haiku sessions
python3 -m pytest harness/tests/test_guard.py -q
```
