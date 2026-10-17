# wq#4133 verify-pass: CONFIRM wq#4113 (wq add/edit accept malformed --deps that deps_met() can never resolve)

Archetype 8 adversarial verify of PR #254 (http://100.121.17.5:3000/lumen/lumen-bin/pulls/254),
author claude-sonnet-5, verifier claude-sonnet-5@high (fresh instance). Shipped as commit
29060120ae8265433dd5b5a22ce13b2fc68e5d24 on the lumen-bin repo (`/home/lumen/bin`), already
merged — confirmed via `git log --oneline` on that commit showing it as HEAD-reachable and
`wq show 4113` recording the same commit hash in its own done-notes.

## Claim under test

wq#4113: `deps_met()` (`wq:1065`) does `SELECT status FROM tasks WHERE id=?` against an
int-keyed column. A `--deps` clause like `wq3941` (wq-prefixed) or one naming a nonexistent
task id never resolves, so `claim --id` refuses forever with a plausible-looking "deps not
met" even once the real dependency is done (live repro: wq#3950/#3980/#4071). Fix adds a
write-time guard (`bad_dep_clauses`) mirroring the existing `--gate` guard's fail-closed,
`--force`-overridable shape, plus `norm_dep_id`/`normalize_deps` to accept and normalize a
`wq`/`wq#`-prefixed numeric id to the bare id instead of refusing it outright. Wired into
both `cmd_add` and `cmd_edit`. New `tests/test_wq4113_dep_id_guard.py` (11 cases).

## What I checked (independently, not re-running the author's reasoning)

1. **Read the diff cold against the live file**, not just the PR's own framing:
   `norm_dep_id`/`bad_dep_clauses`/`normalize_deps`/`print_bad_deps` (`wq:1141-1189`) and
   both call sites (`wq:1630-1637` in `cmd_add`, `wq:3644-3651` in `cmd_edit`, gated on
   `a.deps is not None` so an edit that doesn't touch `--deps` is unaffected). Confirmed
   `deps_met()` itself (`wq:1065-1070`) is untouched — correct, since the fix's job is to
   guarantee only normalized (bare-digit) deps ever reach that stored column, not to change
   how it's read.
2. **Ran the shipped test suite cold:** `python3 -m pytest tests/test_wq4113_dep_id_guard.py -q`
   → **11 passed**, including `test_deps_met_resolves_the_normalized_dep_after_it_is_done` —
   the ticket's own end-to-end acceptance probe (add a child depending on `wq<dep_id>`,
   confirm `claim --id` is blocked, mark the dep done via raw SQL, confirm `claim --id` then
   succeeds). This is a real probe: before the fix it fails loudly (`claim` never unblocks),
   and the test suite exercises the `--force` escape hatch, the `--gate`-shaped malformed vs.
   missing split, and both `add`/`edit` call sites — not just a cwd/default-population subset.
3. **Wrote 6 of my own adversarial probes** against the real `wq` binary (`/tmp/fworker-wq4133/probe/test_adversarial.py`, `test_selfdep2.py`), run via `python3 -m pytest -q -s`:
   - Mixed clause (`"<real-id>,wq9999"`) — refused, correctly names only `wq9999` as the bad one. Pass.
   - Uppercase prefix (`WQ<id>`) — `norm_dep_id` lowercases only for the `startswith` test and
     slices the *original*-case token, so `WQ5` → `5`. Pass.
   - Leading/trailing whitespace inside the clause (`" wq 5 "`) — stripped correctly by the
     existing `.strip()` calls. Pass.
   - Decimal-shaped dep (`"12.0"`) — `isdigit()` is false, refused as `not an id`. Pass.
   - Leading-zero dep (`"0<id>"`) — accepted (digit string), SQLite's INTEGER/TEXT affinity
     comparison resolves it to the same row on both the write-time existence check and the
     later `deps_met()` read. Pass.
   - **Self-dependency** (`wq edit <id> --deps <id>`): accepted silently (the id exists —
     it's the row's own id — so neither `malformed` nor `missing` fires), then `claim --id`
     refuses forever: first with "deps not met" (status not yet done), and if status is
     forced to `done` directly via `wq edit --status done` (bypassing claim), `claim` then
     refuses with "already done" instead. So a self-dependent ticket is permanently
     unclaimable through the normal `claim` path — structurally the same "clause
     `deps_met()` can never resolve" class the PR fixes for wq-prefixed/nonexistent ids.
4. **Checked whether self-dependency is a regression introduced by this PR** (it would be
   a CONTRADICT if so): `git show 29060120^:wq | grep -n deps` shows **zero** write-time
   validation of `--deps` existed before this patch — any value, including a task's own id,
   was accepted unchecked. Self-dependency was already reachable pre-patch and is outside
   wq#4113's stated scope (`wq show 4113`'s detail: "validate deps as comma-separated
   integers (and that the ids exist) ... or normalise a leading 'wq'/'wq#'"); nothing in the
   ticket or PR claims to catch circular/self references. Not attributable to this PR —
   noted below as a related gap for a future ticket, not grounds to contradict wq#4113.
5. **SQL-injection / query-shape check:** every value reaching `c.execute(...,(d,))` either
   passed `d.isdigit()` or the query is never run (malformed clauses short-circuit before the
   `SELECT`). No clause reaches the database as anything but a parameterized digit string.
6. **`--force` path:** confirmed `normalize_deps()` still runs unconditionally even when the
   guard is skipped via `--force`, and for a non-digit clause it stores the value as-typed
   (matches the docstring's documented "only reachable via --force" case) — consistent with
   `test_add_force_overrides_the_refusal`/`test_edit_force_overrides_the_refusal`.

## One pre-existing gap noted, not attributable to this PR

Self- or cross-task circular `--deps` (`A` depends on `B` depends on `A`, or a task
depending on itself) is accepted by `bad_dep_clauses` because the referenced id(s) exist —
the guard only checks "is this a digit?" and "does a row with this id exist?", not "is this
dependency graph satisfiable?" This predates wq#4113 (no `--deps` validation existed at all
before this patch) and is outside its stated scope. Worth a follow-up ticket; not a defect
in the diff under test.

## Verdict

**CONFIRM.** The guard and normalization logic match their stated scope exactly: a
wq/wq#-prefixed numeric dep normalizes to the bare id (verified end-to-end through a real
`claim --id` unblock, plus 6 of my own adversarial probes including uppercase-prefix,
mixed-clause, and leading-zero cases); a non-numeric or nonexistent-id clause is refused at
write time with `--force` as the documented escape hatch; `deps_met()` itself is untouched
and correctly relies on the write-time guarantee. 11/11 shipped tests pass cold, 6/6 of my
own adversarial probes behave as the diff's own logic predicts. No escalation to
opus-5@high.

Verify repo: `/home/lumen/foreman`. Fix lives in the separate `lumen-bin` repo
(`/home/lumen/bin`, commit 29060120ae8265433dd5b5a22ce13b2fc68e5d24) — same cross-repo shape
as wq#4113 itself (see `/home/lumen/kb/ISSUE-LOG.md` row 1392). No code changes made by this
verify-pass; adversarial probe scripts left under `/tmp/fworker-wq4133/probe/` (scratch,
outside both repos).
