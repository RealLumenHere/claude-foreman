# wq#4819 — verify-pass of wq#4817 (selfheal: fleet node sobek offline on tailscale for 30 min)

Verdict: **CONFIRM**. Author: claude-sonnet-5. Verifier: claude-sonnet-5@high (this ticket, fresh instance).

## What wq#4817 claimed

sobek (dedicated PG vault standby, streaming replica of Ma'at + parked VW promote
payload) paged selfheal: offline on tailscale since 2026-11-03T00:20:00.1Z, 30+ min.
PR `lumen/claude-foreman#2583`, commit `cdbc4372`, adds a single `nodes.notes.sobek`
entry (19 insertions) to `ops/wq3354-fleet-selfheal/selfheal.yaml` — no code touched.
Claimed facts: `sobek.power_plug` is unmetered/no Tapo plug (UNVERIFIABLE, no probe,
same as every sobek.* fact); `lumen_state.py history sobek availability` shows
online+stable since 2026-09-24 (frank+ts-verify) then `available` since 2026-09-29,
with no toggle history — this is the *first* selfheal page for this node, not a
repeat; `sobek.role` is vault standby and `sobek.pg_role` confirms an active
streaming replica (sobek_slot, timeline 7, lag 0) as of the 2026-09-28 rebuild
(wq#2918) — live infra, not idle hardware; no Frank power_policy/auto_sleep fact and
no off-decision exist for sobek; conclusion: stays a live, paged fleet node, not
added to `nodes.ignore`/`nodes.sleepers`.

## What I checked

- `git show cdbc4372 -- ops/wq3354-fleet-selfheal/selfheal.yaml`: diff is exactly the
  one `nodes.notes.sobek` string described, 19 insertions, no code touched. This
  worktree's HEAD already carries the commit (the verify branch is cut on top of it).
- `grep -n "notes" ops/wq3354-fleet-selfheal/sh_nodes.py`: `nodes.notes` IS consumed
  as logic (unlike a dead comment) — `sh_nodes.py:185`
  (`note = ncfg.get("notes", {}).get(node)`) folds it into the ticket body as
  `known issue: {note}` the next time sobek pages (`sh_nodes.py:186-187`). A plain
  grep for the literal string `sobek` in the `.py` files finds nothing, which is
  expected: the consuming code is generic and keyed by node name at runtime, not
  hardcoded per node.
- `python3 -I -c "yaml.safe_load(...)"`: the file parses cleanly; the note string
  (1510 chars, containing an apostrophe in "Ma'at" and nested `'...'` quoting inside
  a double-quoted YAML block scalar) round-trips with no escaping errors.
- `ops/wq3354-fleet-selfheal/selfheal.yaml`: `nodes.ignore` = `[thor, maksoor, apis,
  pbox, seshat, be3600, 3dprinter, neith, khnum, wadjet]`, `nodes.sleepers` =
  `[hathor]` — sobek is in neither, matching the claimed "stays a live fleet node"
  conclusion; no overreach into ignore/sleepers for a node with no remote power-read
  path.
- Ran the selfheal suite (`pytest tests/ -q` from
  `ops/wq3354-fleet-selfheal/`): `79 passed, 1 skipped in 3.28s` — no regression from
  the notes-only diff.
- Cross-checked every factual claim against live `lumen_state.py` data (not just
  taking the note's prose on faith):
  - `lumen_state.py history sobek availability` →
    `2026-09-24 -> 2026-09-29: online + stable ... [frank+ts-verify-2026-09-24]` then
    `2026-09-29 -> NOW: available [verify:node_liveness]` — matches the claim
    verbatim: no toggle history, so this is genuinely the first selfheal page on
    record for sobek.
  - `lumen_state.py current sobek` →
    `sobek.power_plug = none — UNMETERED, on no Tapo plug (the p5 assumption was
    wrong...)` — matches the note's quoted text word-for-word.
  - `sobek.pg_role = STANDBY — streaming replica of Ma'at (sobek_slot active,
    timeline 7, pg_is_in_recovery=t, lag 0), rebuilt 2026-09-28 via
    bin/vault-sobek-rebuild.sh (wq#2918, ISSUE-LOG #847)` — matches the claimed
    timeline 7 / lag 0 / wq#2918 rebuild exactly.
  - `sobek.role = Dedicated vault standby (PG streaming replica + parked VW promote
    payload)...` — matches.
  - `grep -ic "power_policy\|auto_sleep\|off-decision"` over `lumen_state.py current
    sobek` output → `0` hits, confirming the claim that no Frank off-decision or
    sleep policy exists for this node.
- `cat .../fleet-selfheal/selfheal.yaml` on the deployed live path
  (`/home/lumen/agents/fleet-selfheal/`) was out of my `--add-dir` scope (blocked by
  the path guard, not retried); `install.sh --verify` is not allowlisted for this
  worker (`bash <path>` only matches `Bash(bash scripts/*)`, and the bare path isn't
  allowlisted either — both forms refused, not retried a second time). Neither gap
  matters for this specific diff: `install.sh --verify` only `vfile`-compares the
  bundle's `.py`/unit files against the deployed destination, never `selfheal.yaml`
  (seed vs. live is reconciled separately by `selfheal_merge.py`, which only adds
  missing keys and never touches operator-edited values) — so it would not have
  exercised this diff even if it had run.

## Adversarial angle considered and its limit

The one thing I cannot independently verify is whether `sobek.power_plug`/`role`/
`pg_role` are themselves accurate descriptions of physical reality — they're all
flagged `[UNVERIFIABLE — no probe]` in `lumen_state` itself, and the note correctly
inherits that uncertainty rather than overstating it. What I *can* and did verify is
that the note accurately transcribes what `lumen_state` currently says, that the
mechanism which will surface this note on a future page actually exists and works,
and that the classification decision (not ignore/sleeper) is consistent with the
absence of any qualifying fact. No contradiction found.

## Conclusion

Pure documentation diff, syntactically valid, actively consumed by `sh_nodes.py:185`
(not dead text), every quoted fact verified word-for-word against live
`lumen_state.py` output, correctly leaves sobek off both `ignore`/`sleepers`, no code
touched, 79/80 test suite green.

**CONFIRM wq#4817.**
