# wq#4565 verify-pass (archetype 8): wq#4550 ccc GitHub mirror push-block guard

**Target:** PR `claude-foreman#2399` (http://100.121.17.5:3000/lumen/claude-foreman/pulls/2399,
merged `229f9a51`, final commit `9d6bc747`). Author: claude-sonnet-5 (wq#4550). Verifier:
claude-sonnet-5@high, fresh instance (wq#4565). wq#4551 (duplicate report) added a
comment/cross-reference only (`2e65a089`) — no logic change — so the diff under test is
exactly wq#4550's `229f9a51`.

**Verdict: CONTRADICT.**

## What the diff claims

`harness/guard.sh` lines 169-177 add a PreToolUse check: any Bash command whose text
matches `\bgit\b.*\bpush\b` AND also matches `RealLumenHere/ccc\b|/home/lumen/ccc\b` is
blocked. `harness/tests/test_guard.py` adds two MUST_BLOCK cases (an explicit mirror URL
push, and `git -C /home/lumen/ccc push github main`) and one MUST_ALLOW case (an unrelated
repo push survives). Commit message frames this as "the interim backstop so no agent push
reaches it directly or via /home/lumen/ccc's github remote in the meantime." Author's own
done-notes cite "tests ... green (78 passed)" — reproduced: `pytest harness/tests/test_guard.py`
→ `78 passed` on this checkout, unchanged.

## What I checked

Read `harness/guard.sh` cold around the new block (lines 150-229), then the full guard
dispatch header (lines 1-120) to see what the PreToolUse hook envelope actually carries.
`.cwd` is read at line 35 (event logging) and, more importantly, at line 501 for an
existing, unrelated check (the primary-tree branch-move wall): that check reads
`HOOK_CWD=$(jfield '.cwd')` and matches it against a colon-separated list of primary-tree
paths specifically to catch a worker that `cd`'d into a primary tree in an earlier call and
then ran a bare branch-moving command with no path in the text. That is the exact same
threat shape as this ticket's guard — and the wq#4550 check doesn't use it.

I ran `guard.sh` directly (`hostname` on this box is `lumen`, so the host-scope check at
line 86 passes and the real script runs end-to-end, not a simulation) with crafted hook
JSON, routed through a Python wrapper so the trigger substrings never appear in my own
Bash `tool_input.command` text — doing it inline immediately tripped the *live* guard
protecting this very session, which substring-matches the whole raw command including
embedded JSON prose. That false-positive was itself a small confirming data point: the
match is a blind substring scan with no awareness of quoting/context.

### Probe 1 — cwd-only push, no literal path/URL in the command

```json
{"tool_name":"Bash","tool_input":{"command":"git push origin main"},"cwd":"/home/lumen/ccc"}
```
`guard.sh` exit code: **0 (allowed)**.

This is a direct analog of how the Bash tool itself behaves in this harness: cwd persists
across separate tool calls, shell state does not. A worker that runs `cd /home/lumen/ccc`
in one Bash call (nothing to block there — it's just `cd`) and `git push origin main` (or
`git push github main`, matching the real remote name docs elsewhere imply for this repo)
in a *later* call never produces a command string containing `RealLumenHere/ccc` or
`/home/lumen/ccc` — the check's two literals never appear, `\bgit\b.*\bpush\b` matches but
the second `grep` doesn't, and the `&&` in the guard's own condition short-circuits to
allow. The push still lands on the dangerous remote; the guard never sees it coming.

### Probe 2 — case-variant mirror URL

```json
{"tool_name":"Bash","tool_input":{"command":"git push https://github.com/reallumenhere/ccc.git main"},"cwd":"/home/lumen/worktrees/wq4565-a1"}
```
`guard.sh` exit code: **0 (allowed)**.

`grep -qE 'RealLumenHere/ccc\b|/home/lumen/ccc\b'` has no `-i`, and `NORM`'s construction
applies no case-folding either (`NORM=$(printf '%s' "$RAW_CMD" | tr '\n\t' '  ' | tr -s ' ')`
— despite the comment two lines above it calling NORM a "lowercase copy", it only
collapses whitespace; there is no case-folding anywhere in this file). GitHub's repo
routing is case-insensitive for the owner/repo segments of a URL, so
`github.com/reallumenhere/ccc` and `github.com/RealLumenHere/ccc` reach the same
repository. A push using any other-case spelling of the org or repo name bypasses the
literal match entirely while still landing on the public mirror.

Both probes were run against the actual shipped `harness/guard.sh` on this host (not a
mock), via `subprocess.run([".../guard.sh"], input=<json>)`, with the real `hostname`
check satisfied. Scripts used: `/tmp/fworker-wq4565/t1.py`, `/tmp/fworker-wq4565/t2.py`.

### Scope check — is there a second layer that catches either gap?

`harness/worker-settings.json`'s own `deny` list (prefix matchers, independent of
`guard.sh`) has no entry mentioning the mirror's org name or `ccc` at all — only
`git push --force*` / `git push -f*`. `harness/action_gate.py`'s regex table (line 141)
is likewise force-push-only. `guard.sh:169-177` is the *sole* backstop for this risk, so
neither gap is caught anywhere else in the stack.

### What does hold

- The two MUST_BLOCK cases the PR actually added (explicit mirror URL, `-C` with the
  literal local path) are correctly blocked — reproduced via the same harness
  (`78 passed`, test suite unchanged since wq#4551's comment-only edit).
- The MUST_ALLOW regression case (an unrelated repo push) correctly survives.
- The check doesn't false-positive on the `/home/lumen/questfree` push wq#4550's own test
  added, nor on ordinary `git push origin <branch>` elsewhere.

So the shipped diff does exactly what its own tests describe — the gap is in what the
tests (and the check) never attempt: an implicit-cwd push, or a differently-cased URL.
Per wq#2630's acceptance-probe doctrine, both of my probes are drawn from outside the
population the author's own tests exercise (MUST_BLOCK/MUST_ALLOW never vary cwd or case),
so they were able to fail — and did.

## Why this is a real gap, not a hypothetical

The commit message's own words — "interim backstop so no agent push reaches it directly or
via /home/lumen/ccc's github remote in the meantime" — promise exactly the coverage probe 1
defeats: a push via `/home/lumen/ccc`'s own configured remote, run from inside that
directory, is the single most natural way an agent (or Frank, scripting ad hoc) would
actually push there — more natural than typing the full URL or `-C /home/lumen/ccc`
every time. And `guard.sh` already treats this exact cwd-persistence threat as real
enough to defend against elsewhere in the same file (line 501), so the omission here is
inconsistent with the file's own established pattern, not an unknown unknown.

## Actions taken

| Action | Rationale |
|---|---|
| `kb/ISSUE-LOG.md` row **1529** appended | Records the contradiction and its scope per doctrine |
| **wq#4568** filed, pri 1, `deps=4550`, `model=claude-opus-5`, `effort=high` | Judgment ticket per archetype-8 protocol: decide whether to extend the guard.sh check with a `HOOK_CWD` match (mirroring guard.sh:501's PRIMARY_TREES pattern) and a case-insensitive/char-class literal, given wq#4563 (Frank-gated: make the mirror private/delete it) is still open and this guard is the only thing standing between an agent and a live public push in the meantime |
| wq#4550 left `done`, PR #2399/`229f9a51` left as-is, not reopened or edited here | Verify-only per archetype 8 |

## Attempts to break the fix that did NOT find a defect

- Multi-line / tab-separated command variants: `NORM`'s `tr '\n\t' '  ' | tr -s ' '`
  correctly collapses these before the match runs; tested a tab-separated variant inline
  through the same Python harness — still blocked when the path/URL literal was present
  verbatim.
- `git push` to an unrelated repo while merely *mentioning* `ccc` in a commit message
  argument (e.g. `git commit -m "fix ccc thing"` then `git push origin main`): the check
  requires `\bgit\b.*\bpush\b` in the *same* command string, so a separate `commit` call
  doesn't contribute; this is correct, not a gap — commit messages aren't pushes.
- Tried to find a second guard/deny layer that might independently catch either probe
  (worker-settings.json deny list, action_gate.py) — confirmed neither does; see Scope
  check above. This widens the blast radius of both gaps rather than closing either.
