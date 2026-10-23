# wq#4568 — judgment: ccc GitHub mirror push-block guard

**Ticket:** wq#4568 (opus-5@high). **Upstream:** wq#4550 / PR#2399 (fix), wq#4551
(duplicate report), wq#4565 (verify-pass, CONTRADICTED), kb/ISSUE-LOG.md row 1529.
**Still open:** wq#4563 (`frank_gated`) — make `RealLumenHere/ccc` private or delete it.

## Decision

**Option (a) + (b): harden `guard.sh`.** Both evasions are closed in this branch. The
residual exposure was *not* acceptable, for one reason that decided it on its own:

wq#4563 is `frank_gated` with **no timeline**. It cannot have one — it needs a visibility
change on Frank's personal GitHub account, and `agent-scope.conf`'s human-only visibility
wall forbids any agent from doing it (`[NO-PROBE]` is recorded on the ticket for exactly
that reason). "Accept the residual given wq#4563's own timeline" presumes a timeline that
does not exist. Meanwhile `guard.sh:169-177` is the *sole* backstop: `worker-settings.json`'s
deny list and `action_gate.py`'s regex table are both force-push-only, so nothing else in
the stack names this org/repo. Accepting the residual means an indefinite window in which a
Class C, never-public repo can be pushed to a publicly readable mirror by a worker that did
nothing unusual — it `cd`'d into a checkout, which is ordinary, and the Bash tool's
documented persistent-cwd semantics did the rest.

The cost of the other side of the trade is ~15 lines reusing a pattern already in this file.
`guard.sh:501` reads `.cwd` and matches it against `PRIMARY_TREES` for *the same threat
shape* — a destructive git verb whose command text names no primary because cwd already is
one. That the ccc wall did not do the same was an oversight, not a judgment call that had
been made and should stand.

## Changes

`harness/guard.sh`:

- **cwd wall** (precomputed before the scan loop, since it does not vary with `$target`):
  `CCC_TREES=${FOREMAN_CCC_TREES:-/home/lumen/ccc}`, split on `:` and matched against
  `.cwd` with the same `case "$tree"|"$tree"/*` shape as line 501's `PRIMARY_TREES` loop
  (so subdirectories count). Sets `CCC_CWD_HIT`.
- **case-insensitive org/repo literal**: the mirror match is now `grep -qiE`, because
  GitHub repo routing is case-insensitive — `github.com/reallumenhere/ccc` and
  `github.com/RealLumenHere/ccc` are the same repo. The *filesystem* path match stays
  case-sensitive (`grep -qE '/home/lumen/ccc\b'`); Linux paths are, and widening it would
  block on paths that cannot exist.
- The block still requires a `git … push` in the command text. Every push out of the ccc
  tree is in scope regardless of remote name: ccc is never-public, so the risk is content
  leaving, not which remote is typed. Read-only git in that tree stays allowed.
- **Prose exemption** (not in the ticket's menu — forced by a live failure, below): the
  wall moved out of the shared `RAW_CMD`/`NORM` loop into its own block scanning
  `CCC_SCAN_TARGETS`, and wq#3861's quote-blanking exemption now covers both the wall and
  one more exempt command. `PROSE_EXEMPT_RE` admits a lone `/home/lumen/bin/wq` call (as
  before) or a lone `python3 /home/lumen/foreman/harness/issue_log_append.py` call.

The block reason now names `wq#4568` and echoes the matched cwd.

### The prose exemption was not optional

The doctrine-mandated `issue_log_append.py --solution "…"` call closing out *this ticket*
was blocked live by the pre-fix wall. The solution text necessarily quotes the mirror's
org/repo and the words for uploading commits, so it matched. That is the wq#3861 /
rows 41/324/447/451 / wq3854 class exactly: **reporting an issue about a wall must not be
walled**, and making the literal match case-insensitive widens the prose over-match rather
than narrowing it. Row 1529's author hit the same thing and worked around it by
paraphrasing ("the verb for uploading commits") — a workaround every future ticket
touching this wall would have to rediscover.

The gate is wq#3861's, unchanged: a lone call to one of those two absolute paths, no
newline/CR, no `$`/backtick/backslash that could smuggle a second command past a naive
scan, and nothing separator-shaped or an unbalanced quote surviving the blanking. An
argument of `issue_log_append.py` cannot execute anything, so a real push string quoted
inside one pushes nothing. `test_ccc_prose_exemption_is_not_a_bypass` holds that line with
5 rows: a surviving `;` or `&&`, the prefix appearing anywhere but the start, a `$(…)`
substitution, and an unbalanced quote each fall back to the full unblanked scan and block.

## Evidence

### Falsification baseline — the probes can fail

wq#4550's own `MUST_BLOCK` rows never varied cwd or case, so they could not have caught
either gap (ISSUE-LOG #1529; the wq#2630 confirm-only trap). Each probe here was run
against **both** the pre-fix guard (`git show beb14cff:harness/guard.sh`) and the post-fix
guard, and must exit 0 on the former and 2 on the latter:

```
OK  probe1 cwd-only push:                    pre-fix exit=0 (want 0), post-fix exit=2 (want 2)
OK  probe2 case-variant URL:                 pre-fix exit=0 (want 0), post-fix exit=2 (want 2)
OK  probe3 out-of-population tree override:  pre-fix exit=0 (want 0), post-fix exit=2 (want 2)
OK  probe4 issue-log append prose:           pre-fix exit=2 (want 2), post-fix exit=0 (want 0)
OK  probe5 exemption is not a bypass:        pre-fix exit=2 (want 2), post-fix exit=2 (want 2)
```

Probes 1 and 2 are wq#4565's two reported envelopes verbatim. This reproduces the
contradiction independently before fixing it — the pre-fix column *is* wq#4565's finding.
Probe 4 is inverted on purpose: it is the append call that actually failed on me, so its
pre-fix `exit=2` is the live false positive and its post-fix `exit=0` is the fix. Probe 5
pins the exemption's boundary — blocked on both sides, so the exemption did not open a hole.

**Probe 3 is the out-of-population probe (wq#2630).** The fix takes a scope parameter
(`CCC_TREES`, default `/home/lumen/ccc`), so a probe drawn only from that default could
only confirm. Probe 3 overrides `FOREMAN_CCC_TREES` to `/home/lumen/mirror-of-ccc` — a tree
outside the default the fix was designed around — and the cwd wall has to follow it there.
Its paired allow case (same override, cwd back in the worker's worktree, still exit 0)
is what keeps probe 3 evidence rather than a tautology: the wall tracks the configured
scope instead of blocking everything.

### Committed regression tests

`harness/tests/test_guard.py` — `CCC_MUST_BLOCK` (7 rows), `CCC_MUST_ALLOW` (9 rows) and
`CCC_EXEMPTION_MUST_BLOCK` (5 rows), run by `test_ccc_mirror_push_blocked`,
`test_ccc_mirror_allow_cases` and `test_ccc_prose_exemption_is_not_a_bypass`. Rows are
`(tool_input, cwd, env_extra)` so cwd and the scope override are both part of the corpus
that future changes are checked against — the dimension wq#4550's tests lacked. The
pre-existing wq#4550 rows are kept, so the widening is shown not to have regressed them.

```
$ python3 -m pytest harness/tests/test_guard.py -q
81 passed in 60.57s
```

## What this does not do

This is still an interim backstop, and wq#4563 remains the real fix. A guard hook covers
Bash tool calls in this harness; it is not a server-side branch protection. wq#4563 stays
open and `frank_gated` — unchanged by this ticket. wq#4550 is left `done` and unmodified
per archetype 8.
