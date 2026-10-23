# wq#4594 — Judgment pass (archetype 8, opus-5@high escalation): wq#4590 ccc fs-path arm boundary charset

**Verdict: CONTRADICT.** wq#4590's diff does not land cleanly. It correctly fixes the
false-positive it was filed to fix (sibling-named paths like `/home/lumen/ccc-mirror`,
`/home/lumen/ccc.bak` no longer false-block), but the mechanism it uses — replacing a
bare regex `\b` word-boundary with an *enumerated* allow-list of boundary characters —
is narrower than the `\b` it replaced, and the gap it opens is a real security
regression: commands that shell-expand to an actual push of the protected, never-public
tree are now **allowed** by the post-fix guard where the pre-fix guard **blocked** them.
sonnet-5@high (me) found and reproduced this live; escalated per protocol; opus-5@high
independently reproduced it from scratch, found additional variants, and proposed a fix.
Both verifiers agree: CONTRADICT.

## What wq#4590 shipped

Commit `56db388e` (worktree `/home/lumen/worktrees/wq4594-a1`, repo `/home/lumen/foreman`).
`harness/guard.sh`'s never-public-ccc push-block wall has two OR'd regex arms matched
against Bash tool_input text, plus a cwd check. wq#4590 touched only the filesystem-path
arm, changing `grep -qE '/home/lumen/ccc\b'` to:

```
grep -qE '/home/lumen/ccc(['\''"/;[:space:]]|$)'
```

i.e. the literal path, followed by one of: `'`, `"`, `/`, `;`, whitespace, or end-of-string.
Comment and `harness/tests/test_guard.py` corpus additions (`CCC_MUST_ALLOW` rows for
`/home/lumen/ccc-mirror`, `/home/lumen/ccc.bak`; `CCC_MUST_BLOCK` rows for the real tree,
subdirectory, quoted, and semicolon forms) match this charset exactly. All 81 guard tests
pass (`python3 -m pytest harness/tests/test_guard.py -q` → `81 passed`).

## What the verifier found

A bare `\b` is a GNU-grep word-boundary: it fires at **any** transition between a word
char (`[A-Za-z0-9_]`) and a non-word char, which includes `)`, backtick, `&`, `|`, `>`,
`<` — none of which are in wq#4590's enumerated replacement set. So any command that
places one of those characters immediately after the protected path — most naturally via
shell command substitution, a backtick, or a no-space shell operator — now evades the
wall, while still shell-expanding to the exact push the wall exists to stop.

Reproduced live, independently, twice, using the same hook-JSON envelope
`harness/tests/test_guard.py`'s `run_guard()` helper feeds the real `guard.sh`:

**sonnet-5@high (`/tmp/fworker-wq4594/probe_guard.py` vs. `probe_guard_before.py`,
the latter run against `git show 56db388e^:harness/guard.sh`):**

| command | pre-wq4590 | post-wq4590 |
|---|---|---|
| `git -C /home/lumen/ccc push origin main` (control) | blocked (2) | blocked (2) |
| `git -C "$(printf /home/lumen/ccc)" push origin main` | blocked (2) | **allowed (0)** |
| `git -C $(echo /home/lumen/ccc) push origin main` | blocked (2) | **allowed (0)** |
| `git -C /home/lumen/ccc-mirror push origin main` (intended fix) | blocked (2) | allowed (0) |

**opus-5@high, independent repro (own probe + own `guard_before.sh` extraction),
found the same regression plus more variants:**

| command | before | after |
|---|---|---|
| `` git -C `echo /home/lumen/ccc` push origin main `` | 2 | **0** |
| `cd /home/lumen/ccc&&git push origin main` | 2 | **0** |
| `(cd /home/lumen/ccc&& git push origin main)` | 2 | **0** |
| `cd /home/lumen/ccc>/dev/null && git push origin main` | 2 | **0** |
| `echo /home/lumen/ccc\|xargs -I{} git -C {} push origin main` | 2 | **0** |
| `{ cd /home/lumen/ccc; git push origin main; }` | 2 | 2 (space before `;`, still caught) |
| `git -C /home/lumen/cc[c] push origin main` (glob) | 0 | 0 (misses on **both** — pre-existing, out of scope) |

Every bypass row blocks on the pre-wq4590 guard and allows on the post-wq4590 guard, with
the only code difference between the two runs being the line wq#4590 edited — this is a
regression introduced by this diff, not a pre-existing gap wq#4590 merely left untouched
(contrast with wq#4590 itself, which was an untouched sibling defect of wq#4569 and
correctly ruled out-of-scope for wq#4569 in the wq#4587 judgment). The glob case is the
one genuine pre-existing gap here (both versions miss it) and does not count against this
diff.

The `cd <path>&&git push ...` form with no space is an ordinary, unremarkable shell idiom
— not an exotic evasion technique — which is why this is scored as a real regression and
not a theoretical corner case.

## Why this is in scope (not a wq#4590-adjacent nitpick)

wq#4590's own stated liveness/acceptance was "tighten the filesystem-arm pattern ...
mirroring wq#4569's approach," and the shipped diff does exactly that — it is a faithful
implementation of its own ticket's spec. The defect is in the spec's chosen mechanism
(enumerate allowed boundaries) rather than a slip in executing it: enumerating a
boundary set is inherently narrower than the `\b` fallback unless the enumeration is
audited against the shell's actual metacharacter set, and it wasn't. That is exactly the
shape of bug this verify-pass exists to catch — "read the PR/diff cold, try to break it" —
and the break is real, reproducible, and strictly worse than the false-positive it fixed
(fails open vs. fails closed).

## Disposition

- wq#4590: does **not** stand as shipped. Noted on the ticket.
- ISSUE-LOG row 1562: this finding, status "open (follow-up ticket pending)".
- Follow-up ticket **wq#4595** filed (lane build, pri 2, archetype mechanical-edit,
  deps=4590): negate the boundary class instead of enumerating it —
  `/home/lumen/ccc([^[:alnum:]_.-]|$)` — per opus-5@high's proposal, independently
  regex-checked to still allow `ccc-mirror`/`ccc.bak`/`ccc_old` while blocking every
  bypass row above. Add those rows to `CCC_MUST_BLOCK` in `harness/tests/test_guard.py`.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
