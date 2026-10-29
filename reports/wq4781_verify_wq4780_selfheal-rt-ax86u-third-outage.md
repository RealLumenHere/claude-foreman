# wq#4781 — verify-pass of wq#4780 (selfheal rt-ax86u third offline, daily-pattern note)

Verdict: **CONFIRM**. Author: claude-opus-5-5. Verifier: claude-sonnet-5 (this ticket).

## What wq#4780 claimed

rt-ax86u (RT-AX86U wifi-repeater RE station) paged selfheal a third time (wq#4069
2026-10-16, wq#4775 2026-10-28, wq#4780 2026-10-29 LastSeen 16:30Z). PR
`lumen/claude-foreman#2564`, commit `cf7e2667` (merged to `main`, content-identical to
the paragraph now present in `origin/main`), appends one paragraph to the existing
`nodes.notes.rt-ax86u` block in `ops/wq3354-fleet-selfheal/selfheal.yaml` (8 insertions,
1 deletion — the deletion only re-anchors the closing `"`). Claimed: it came back on the
tailnet between pages again (intermittent); the last two drops are ~24h apart at about the
same time of day (10-28 16:00Z, 10-29 16:30Z), suggesting a daily power-down or daily
upstream-link drop rather than random faults; re-probed from lumen at 17:02Z:
192.168.8.204:22/8443 EHOSTUNREACH, 100.120.105.55:22 timeout, gateway 192.168.8.1:80 open
(same state as wq#4775); classification unchanged (not `nodes.ignore`/`nodes.sleepers`);
pattern added to wq#4776 `[frank-hw]`, still open. Also claims ISSUE-LOG row 1637 and
"selfheal tests 76 pass."

## What I checked

- `git diff origin/main...pr-2564-verify` (fetched `refs/pull/2564/head`): confirmed the
  diff is exactly the one paragraph described, 8 insertions/1 deletion, no code touched.
  `git show origin/main:ops/.../selfheal.yaml` shows the identical paragraph already
  landed on `main` — the PR is actually merged, not a dangling branch.
- `grep -rn rt-ax86u ops/wq3354-fleet-selfheal/ --include=*.py`: no hits — the `notes`
  string is never consumed as logic, so this diff cannot change runtime behavior by
  construction (same finding as wq#4777's verify of wq#4775).
- `yaml.safe_load`: valid. `nodes.ignore` = `[thor, maksoor, apis, pbox, seshat, be3600,
  3dprinter, neith]`, `nodes.sleepers` = `[hathor]` — rt-ax86u in neither, matching the
  stated "classification unchanged" claim.
- Ran the selfheal suite: `76 passed, 1 skipped in 3.14s` — matches the claimed count
  exactly.
- Re-ran the live network probe myself (this worker's allowlist has `Bash(curl *)`,
  unlike wq#4777's verifier which had no network-probe path at all):
  - `curl telnet://192.168.8.204:22` → "No route to host" (EHOSTUNREACH), matches.
  - `curl telnet://192.168.8.204:8443` → "No route to host" (EHOSTUNREACH), matches.
  - `curl telnet://192.168.8.1:80` → "Established connection" (gateway open), matches.
  - `curl telnet://100.120.105.55:22` → "Connection timed out", matches.
  All four match the PR's stated 17:02Z probe results, hours later — the node is still
  down in the same way, which is itself consistent with the claimed daily-drop pattern
  rather than a one-off.
- Verified the "came back between pages" mechanism against the actual state file
  (`/home/lumen/agents/fleet-selfheal/state.json`): `tickets["node-offline:rt-ax86u"]` now
  points at `id: 4780`, and `sh_nodes.py` sweep() only opens a new ticket when
  `f"node-offline:{node}" not in ctx.section("tickets")` — the prior key would still have
  pointed at 4775 unless the node went `online` in between and `st.pop(key, None)` /
  `ctx.resolve(...)` ran (sh_nodes.py:146-149). wq#4780 existing as a distinct ticket id is
  direct evidence that resolve-on-reappear fired between the two outages, not just an
  assumption.
- Time-of-day math: 10-28 16:00Z → 10-29 16:30Z is 24h30m — "~24h apart, about the same
  time of day" is accurate. The claim is correctly scoped to "the last two" only; it does
  not try to fit wq#4069's 04:50Z first occurrence into the same pattern, which would have
  been overreach.
- `wq show 4776`: still open, lane `frank-hw`, now carries a note timestamped
  `2026-10-29 17:02:54` with the wq#4780 pattern ("check for a daily power-down... or a
  daily WiFi-backhaul drop") — matches the claim, correctly filed as a non-blocking queued
  physical task (Tier-0 instinct 3), not a gate.
- ISSUE-LOG: row 1637 exists exactly once and matches the claimed content (drops ~24h
  apart, LAN EHOSTUNREACH, mitigated/awaiting wq#4776).

## Adversarial angle considered and its limit

Unlike wq#4777's verify of wq#4775 (which could not re-run the live LAN probe because its
worker had no network tool on the allowlist), this worker does have `curl`, so I actually
re-ran the probe rather than taking it on faith — and it reproduced the same failure mode
hours later. The one claim I cannot independently confirm is whether rt-ax86u was *truly*
back online at some point between 2026-10-28 16:00Z and 2026-10-29 16:30Z (vs., e.g., a
sweep bug that cleared ticket state without a real reconnect). But the ticket-id evidence
(4780 being a fresh id rather than a reopened 4775) is strong circumstantial support, and —
same as the prior verify — the diff's actual conclusion (keep paging, don't ignore/sleeper
it, route the physical question to wq#4776) does not depend on this detail being exactly
right either way.

## Conclusion

Pure documentation diff, syntactically valid, consumed nowhere as logic, independently
reproduced live-probe evidence, consistent with the ticket-dedup mechanism in
`sh_nodes.py`, correctly leaves rt-ax86u off both `ignore`/`sleepers`, correctly extends
the existing non-blocking physical-check ticket (wq#4776) with the new pattern, and ships
with a matching ISSUE-LOG row and a passing 76-test suite. No contradiction found.

**CONFIRM wq#4780.**
