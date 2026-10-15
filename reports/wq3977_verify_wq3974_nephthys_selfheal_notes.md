# wq#3977 verify-pass of wq#3974 — nephthys offline-on-tailscale selfheal page

PR: http://100.121.17.5:3000/lumen/claude-foreman/pulls/1921 (merged d25ef85914)
Author model: claude-sonnet-5. Verifier: claude-sonnet-5@high (fresh instance).

## What shipped

One line changed in `ops/wq3354-fleet-selfheal/selfheal.yaml:111`, the existing
`nodes.notes.nephthys` entry (same mechanism as the sibling `hathor`/`ptah` entries),
updated to carry forward:
- occurrence count bumped 3x -> 5x, with per-occurrence ticket backrefs: 3 logged by
  wq#2776 (as of 2026-09-28), a 4th 2026-10-01 as wq#3307/wq#3316, a 5th 2026-10-15 as
  wq#3974/wq#3976
- added a note that BUILD/selfheal has no power-cycle execution path (fleet-exec power
  verb unexposed, plug-ctl not on the BUILD allowlist), so each occurrence gets its own
  wq#3976-pattern `[frank-hw]` physical ticket, deps-linked off the node-offline ticket
  rather than a Frank-gate (CLAUDE.md Tier-0 #3)
- kept the existing instruction not to add nephthys to `nodes.ignore`

wq#3974 itself was also set `deps: 3976` (confirmed via `wq show 3974`) so the
self-heal sweep stops re-filing this specific outage while the physical fix (wq#3976,
AC-cycle plug P2) is pending.

## Checks performed

1. **Diff scope** — `git show d25ef85914` touches only the one YAML line (1
   insertion, 1 deletion, 1 file); no code paths changed.
2. **YAML validity** — loaded the file with `yaml.safe_load`; parses cleanly, `notes`
   dict has keys `['hathor', 'nephthys', 'ptah']`, no duplicate-key or quoting issues.
3. **Key/lookup correctness** — `sh_nodes.py:170-186`: `nephthys` is in neither
   `nodes.ignore` nor `nodes.sleepers` (grepped both lists directly), so the
   `ncfg.get("notes", {}).get(node)` lookup at line 185 stays reachable and the note
   keeps riding along with the page, not silencing it — matching the ticket's explicit
   instruction.
4. **Factual claims cross-checked against live tickets** (instinct 8: referenced value
   must actually exist and say what the note claims):
   - wq#2776, wq#2851, wq#3307, wq#3316 all exist and are the AER-storm/power-loss
     tickets the note claims them to be.
   - wq#3976 (open, lane `frank-hw`): title and detail match the note's "5th
     occurrence... AC-cycle plug P2" claim verbatim, including the "no power verb on
     the BUILD allowlist" reasoning copied into the note.
   - wq#3974 notes field: "PR ... merged (d25ef85914 on main)... Physical fix queued
     as its own ticket wq#3976... deps=3976" — matches `deps: 3976` on the ticket
     record itself.
5. **Live re-probe** — ran `python3 /home/lumen/bin/fleet-ssh-check --nodes nephthys
   --quiet`: exit 1 (still offline), consistent with the original claim and with the
   physical fix (wq#3976) still being open/unactioned. This is the falsifiable half of
   the probe: it could have come back 0 if nephthys had been power-cycled already, and
   it didn't.
6. **Test suite** — `pytest ops/wq3354-fleet-selfheal/tests/`: 66 passed, 2 failed.
   The 2 failures (`test_langfuse_secret_bearing_names_get_the_chmod_action`,
   `test_every_shipped_rule_is_well_formed_and_the_original_five_survive`, both in
   `test_shipped_rules.py`) are pre-existing: checked out the commit immediately before
   wq#3974's (`628e39b0`), loaded that revision's `selfheal.yaml` through
   `leakwatch.load_rules`, and the rule count was already 5 (test wants >=6) — same
   root cause and same two failing tests as reported independently in the prior
   `wq3958_verify_wq3957_ptah_selfheal_notes.md` verify-pass, which traced it to the
   older commit `d9998c22` ("fix(leakwatch): cover /home/lumen/langfuse env, compose
   and bak files"), confirmed still at HEAD via `git log --oneline -- tests/
   test_shipped_rules.py`. Unrelated to wq#3974's diff; out of scope for this ticket.
   Logged to `/home/lumen/kb/ISSUE-LOG.md` row 1342 for visibility since it still
   wasn't logged there despite being caught twice now.
   The CLASS 6 node/notes-specific tests pass cleanly:
   `test_resource_classes.py -k node` -> 3 passed.

## Attempted to break it

- Checked for a second `nephthys` key or indentation drift that would make YAML
  silently drop or shadow the entry — none; `yaml.safe_load` round-trips a single
  `nephthys` key under `nodes.notes`.
- Checked whether `nephthys` had been (incorrectly) added to `nodes.ignore` or
  `nodes.sleepers` as a shortcut instead of the proper deps-linked physical ticket —
  it has not; grepped both lists directly, nephthys appears in neither.
- Checked whether the occurrence-count free text is parsed anywhere by code expecting
  a specific format (e.g. a regex counting "Nx so far") that the new wording could
  break — `sh_nodes.py` only does `ncfg.get("notes", {}).get(node)` and folds the
  string verbatim into the ticket body; no parsing, so free-text rewording is safe by
  construction.
- Checked whether wq#3974's `deps: 3976` could leave this outage silently unaddressed
  forever if wq#3976 is never actioned — it won't: deps only suppresses *this specific*
  node-offline ticket's re-filing by the sweep while a dependency is open; it doesn't
  touch `nodes.ignore`/`nodes.sleepers`, so a *future* fresh offline episode (after this
  one resolves and recurs) would file its own new ticket normally, per the standing
  "do NOT add to nodes.ignore" instruction preserved in the note.

## Verdict: CONFIRM

The diff is correct, minimal, consistent with the existing `nodes.notes` convention,
passes the relevant test suite (modulo a pre-existing, unrelated, now-logged failure),
and every factual claim in the updated note is backed by a real, currently-open or
closed ticket with matching details. The underlying outage is independently
reconfirmed live (`fleet-ssh-check` still exits 1). No contradiction found.
