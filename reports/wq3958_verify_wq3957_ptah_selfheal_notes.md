# wq#3958 verify-pass of wq#3957 — ptah offline-on-tailscale selfheal page

PR: http://100.121.17.5:3000/lumen/claude-foreman/pulls/1912 (merged 70630e4011)
Author model: claude-sonnet-5. Verifier: claude-sonnet-5@high (fresh instance).

## What shipped

One line added to `ops/wq3354-fleet-selfheal/selfheal.yaml:112`, a `nodes.notes.ptah`
entry (same mechanism as the existing `hathor`/`nephthys` entries), carrying forward:
- ptah did not return from a Frank-approved kernel reboot 2026-10-15 02:54 UTC
  (6.18.50 -> 6.18.56, wq#3866 apt upgrade)
- no LAN ping, no tailnet, no smart plug assigned (no remote power path)
- ptah is a live inference node, not a permanent-off or self-idle-sleeper case, so the
  page should keep firing (neither `nodes.ignore` nor `nodes.sleepers` applies)
- physical check already queued as wq#3949

## Checks performed

1. **Diff scope** — `git show 70630e4011` touches only the one YAML line; no code paths
   changed.
2. **YAML validity** — loaded the file with `yaml.safe_load`; parses cleanly, `notes`
   dict now has keys `['hathor', 'nephthys', 'ptah']`, no duplicate-key or quoting
   issues (the string uses backticks and `--`, no embedded double quotes).
3. **Key correctness** — `sh_nodes.py:185` looks up `ncfg.get("notes", {}).get(node)`
   where `node` is the `lumen_state` node name (confirmed via `node_for()`); `ptah` is
   the correct key, matching the convention of the sibling `hathor`/`nephthys` entries.
4. **Factual claims cross-checked against the live tickets**:
   - wq#3866 (done): "52 packages upgraded... Frank-approved reboot at 02:54 UTC did
     NOT come back" — matches the note's timestamp and kernel versions exactly.
   - wq#3949 (open, lane frank-hw): "Frank approved the reboot; issued 2026-10-15
     02:54:11 UTC... no ping on 192.168.8.125 (ARP FAILED), tailnet offline... No smart
     plug is mapped to ptah" — matches the note's IP, ARP-fail claim, and
     no-remote-power claim verbatim. wq#3949 is genuinely queued (status: open), so the
     note's "physical check already queued as wq#3949" is not fabricated (instinct 8
     check: the referenced ticket exists and says what the note says it says).
5. **Test suite** — `pytest ops/wq3354-fleet-selfheal/tests/` : 66 passed, 2 failed.
   The 2 failures (`test_langfuse_secret_bearing_names_get_the_chmod_action`,
   `test_every_shipped_rule_is_well_formed_and_the_original_five_survive`) are in
   `test_shipped_rules.py` (leakwatch class), introduced by an unrelated, much older
   commit `d9998c22` ("fix(leakwatch): cover /home/lumen/langfuse env, compose and bak
   files") that predates wq#3957's commit in history — confirmed via
   `git log --oneline -- tests/test_shipped_rules.py` and `-- selfheal.yaml`, neither of
   which wq#3957's diff touches. Pre-existing, out of scope for this ticket.
   The CLASS 6 node/notes tests specifically
   (`test_node_offline_ticket_folds_in_configured_known_issue_note` and siblings in
   `test_resource_classes.py -k node`) pass: 3 passed.

## Attempted to break it

- Checked for a second `ptah` key / indentation drift that would make YAML silently
  drop or misparse the entry — none; `yaml.safe_load` round-trips a single `ptah` key.
- Checked whether `ptah` could collide with an existing `nodes.ignore` /
  `nodes.sleepers` entry (which would make the notes entry unreachable, since
  `sh_nodes.py:170/173` short-circuits before the notes lookup for ignored/sleeper
  nodes) — `ptah` appears in neither list.
- Considered whether "no action needed" in the original wq#3957 ticket detail
  (confidence 0.72, stale node-state snapshot) contradicts the fix — it doesn't; that
  line was the *input* pre-investigation snapshot that wq#3957 was dispatched to
  resolve, and the PR's own investigation (wq#3866/wq#3949 cross-refs) supersedes it
  with live evidence, which is what the note now encodes.

## Verdict: CONFIRM

The diff is correct, minimal, consistent with the existing `nodes.notes` convention,
passes the relevant test suite, and every factual claim in the added note is backed by
a real, currently-open or closed ticket with matching details. No contradiction found.
