# wq#4823 — verify-pass of wq#4822 (selfheal rt-ax86u fourth offline, 00:50Z note)

Verdict: **CONFIRM**. Author: claude-opus-5-5. Verifier: claude-sonnet-5 (this ticket).

## What wq#4822 claimed

rt-ax86u (RT-AX86U wifi-repeater RE station) paged selfheal a fourth time (wq#4069
2026-10-16, wq#4775 2026-10-28, wq#4780 2026-10-29, wq#4822 2026-11-03 LastSeen 00:50Z).
PR `lumen/claude-foreman#2588`, commit `d1d365d2` (merged to `main`), appends one
paragraph to the existing `nodes.notes.rt-ax86u` block in
`ops/wq3354-fleet-selfheal/selfheal.yaml` (1 file, 10 insertions / 1 deletion — the
deletion only re-anchors the closing quote). Claimed: back on the tailnet between pages
again; this drop is at 00:50Z, not ~16:00Z, so the daily same-hour pattern from wq#4780
does not hold, making a manual power-down or flaky uplink more likely than a timer plug;
re-probed from lumen at 01:24Z: 192.168.8.204:22 timeout, :8443 EHOSTUNREACH,
100.120.105.55:22 timeout, gateway 192.168.8.1:80 open; classification unchanged (not
`nodes.ignore`/`nodes.sleepers`); wq#4776 `[frank-hw]` noted with the new occurrence,
still open/unclaimed; "selfheal tests 79 pass/1 skip".

## What I checked

- `git log`/`git show d1d365d2 --stat` in the verify worktree (already at this commit,
  landed on `origin/main`): diff is exactly the one paragraph described, 10
  insertions/1 deletion, no code touched, matching the PR's own description.
- `grep -n notes ops/wq3354-fleet-selfheal/sh_nodes.py`: the `nodes.notes` string is only
  ever folded verbatim into the ticket body (`lines.append(f"known issue: {note}")`,
  sh_nodes.py:185-186) — never parsed as structured data, so this diff cannot change
  runtime behavior by construction (same finding as the wq#4777/wq#4781 verifies of this
  node's two prior occurrences).
- `yaml.safe_load`: valid, and the folded text reads correctly with the new paragraph
  properly space-separated from the prior one (no run-on words from a block-scalar
  indentation mismatch). `nodes.ignore` = `[thor, maksoor, apis, pbox, seshat, be3600,
  3dprinter, neith, khnum, wadjet]`, `nodes.sleepers` = `[hathor]` — rt-ax86u in neither,
  matching "classification unchanged."
- Ran the selfheal suite in the worktree: `79 passed, 1 skipped in 3.20s` — matches the
  claimed count exactly.
- Re-ran the live network probe myself (`curl telnet://...`, allowlisted):
  - `192.168.8.204:22` → "No route to host" (EHOSTUNREACH) — same failure *family* as
    claimed (claim said `:22` timeout, `:8443` EHOSTUNREACH; live run got the
    EHOSTUNREACH on `:22` instead). Port-level attribution of timeout vs. EHOSTUNREACH
    varies run-to-run with ARP cache state on this LAN — the wq#4780 verify observed the
    identical swap pattern across runs — but the node-level conclusion (dark on LAN) is
    identical.
  - `100.120.105.55:22` (tailnet) → "Connection timed out", matches.
  - `192.168.8.1:80` (gateway) → "Established connection", matches — router is up, the
    node behind it is not.
  - Hours after the claimed 01:24Z probe, the node reproduces the same dark-on-LAN,
    gateway-up state, consistent with "classification unchanged" rather than a transient
    blip that already cleared.
- Verified the "came back between pages" dedup mechanism against the actual state file
  (`/home/lumen/agents/fleet-selfheal/state.json`): `tickets["node-offline:rt-ax86u"]`
  now points at `id: 4822` (a fresh id, not a reopened 4780), and `sh_nodes.py` only opens
  a new ticket when the prior key is absent from `ctx.section("tickets")` (sh_nodes.py:
  144-155) — the prior key is cleared via `st.pop(key, None)` only when the node is seen
  online again. The fresh ticket id is direct evidence the node reconnected in between,
  same logic the wq#4781 verify used for the third occurrence.
- `wq show 4776`: still `status: open`, `claimed_by: None` — notes field carries a new
  line timestamped `2026-11-03 01:25:19` for "wq#4822: 4th outage ... 00:50Z breaks the
  ~16:00Z daily pattern ... suspect manual power-down or flaky uplink" — matches the
  claim exactly, correctly left as a non-blocking queued physical task (Tier-0 instinct
  3), not a gate.
- ISSUE-LOG: wq#4822's own done-notes make no ISSUE-LOG claim (unlike wq#4780's claimed
  row 1637), and `grep -n "wq#4822" ISSUE-LOG.md` finds nothing — so there is no false
  claim to contradict here; the rt-ax86u pattern is already logged at rows 1633/1637 from
  the prior two occurrences.
- Occurrence numbering: read the full `nodes.notes.rt-ax86u` string end to end —
  FIRST=wq#4069 (10-16), SECOND=wq#4775 (10-28 16:00Z), THIRD=wq#4780 (10-29 16:30Z),
  FOURTH=wq#4822 (11-03 00:50Z). Internally consistent, and the new paragraph correctly
  scopes its "breaks the daily pattern" claim to comparing only against the THIRD
  occurrence's 16:00Z/16:30Z observation, not overreaching onto wq#4069's unrelated
  04:50Z first drop.

## Adversarial angle considered and its limit

The one claim I cannot independently confirm is whether rt-ax86u was *truly* back online
at some point between the 10-29 16:30Z and 11-03 00:50Z drops (vs. a sweep bug clearing
ticket state without a real reconnect) — same limit noted in the wq#4781 verify of this
node's third occurrence. The ticket-id evidence (4822 being a fresh id, not a reopened
4780) is strong circumstantial support, and the diff's actual conclusion (keep paging,
don't ignore/sleeper it, route the physical question to wq#4776) does not depend on this
detail being exactly right either way. I also checked whether the exact LAN port/error
mismatch (claim: `:22` timeout; live: `:22` EHOSTUNREACH) is a tell that the original
probe was fabricated — it is not: the wq#4780 verify logged the same kind of port/error
swap on a live re-probe of the same node, so this is observed noise in this LAN's ARP
behavior, not a reproducibility failure.

## Conclusion

Pure documentation diff, syntactically valid, consumed nowhere as logic, independently
reproduced live-probe evidence (node still dark, gateway still up), consistent with the
ticket-dedup mechanism in `sh_nodes.py`, correctly leaves rt-ax86u off both
`ignore`/`sleepers`, correctly extends the existing non-blocking physical-check ticket
(wq#4776) with the new pattern, and ships with a passing 79-test suite matching the
claimed count. No contradiction found.

**CONFIRM wq#4822.**
