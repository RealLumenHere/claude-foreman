# wq#4777 — verify-pass of wq#4775 (selfheal rt-ax86u second offline, queue physical check)

Verdict: **CONFIRM**. Author: claude-opus-5-5 (archetype rca-investigation). Verifier: claude-sonnet-5 (this ticket).

## What wq#4775 claimed

rt-ax86u (RT-AX86U wifi-repeater RE station) paged selfheal a second time
(first: wq#4069, 2026-10-16; second: wq#4775, 2026-10-28). PR `lumen/claude-foreman#2560`,
landed as `f0b986b5` on `foreman` main, appends a `wq#4775` paragraph to the existing
`nodes.notes.rt-ax86u` block in `ops/wq3354-fleet-selfheal/selfheal.yaml` (12 insertions,
1 deletion — the deletion is re-adding the closing `"` moved down to the new paragraph's
end; no prose is removed). Claimed: it was back on the tailnet between the two pages
(intermittent, not a permanent break); LAN probe to 192.168.8.204:22/8443 got EHOSTUNREACH
while gateway 192.168.8.1 answered (router itself is unpowered/unplugged/off-LAN, not a
tailscale-only fault like be3600's); no smart plug or fleet-exec grant exists for it, so no
remote fix; still doesn't meet the `nodes.ignore`/`nodes.sleepers` bar; physical check
queued as wq#4776 `[frank-hw]`. Also claims ISSUE-LOG row 1633 and "selfheal tests 76 pass."

## What I checked

- Read `git show f0b986b5` in full (34 lines) — confirmed it is a pure documentation
  append to one YAML string value, no code/logic touched.
- `grep -rn rt-ax86u ops/wq3354-fleet-selfheal/` outside `selfheal.yaml`: no hits — the
  `notes` field is never consumed as logic by any selfheal code, so this diff cannot
  change runtime behavior by construction.
- Parsed `selfheal.yaml` with `yaml.safe_load`: valid. `nodes.ignore` =
  `[thor, maksoor, apis, pbox, seshat, be3600, 3dprinter]`, `nodes.sleepers` = `[hathor]` —
  rt-ax86u is in neither, matching the stated decision to keep it a live page.
- Ran the selfheal suite: `76 passed, 1 skipped in 3.43s` — matches the claimed count
  exactly.
- Checked the specific mechanism claim "sh_nodes drops a node's outage state when it
  reappears": `sh_nodes.py` sweep(), `if rec["online"]: st.pop(key, None) ...` — confirmed
  accurate against the actual code (sh_nodes.py:144-148).
- Checked `research/fleet_hardware.json:589,596`: RT-AX86U/be3600 are indeed grouped under
  "appliances and RE targets, not compute hosts" — the ticket's framing of rt-ax86u's role
  is accurate, not invented.
- `grep -rn rt-ax86u` across `/home/lumen/foreman` outside the one notes block: no smart
  plug entry, no fleet-exec grant config for this host — consistent with (does not
  contradict) the "no smart plug and no fleet-exec grant" claim.
- ISSUE-LOG: row 1633 exists exactly once (`grep -c '^| 1633 '` → 1), rows 1630-1633 are
  sequential with no collision — consistent with `issue_log_append.py` having allocated it
  (not a hand-picked max+1 that could have collided, per wq#3174).
- `wq show 4776`: open, lane `frank-hw`, pri 2, body says "If yes, reply and it moves to
  selfheal.yaml nodes.ignore... If it should be always-on, power it up and note the cause,"
  tagged `[NO-PROBE: physical inspection by Frank]` — correctly filed as a queued physical
  task (Tier-0 instinct 3: only Frank can touch the hardware), not a blocking gate on this
  or any other ticket.
- `git branch --contains f0b986b5` shows `main` — the fix is actually landed, not a
  dangling branch.

## Adversarial angle considered and its limit

The one empirical, time-sensitive claim in the diff — the live EHOSTUNREACH/no-ARP-reply
LAN probe to 192.168.8.204 vs. the gateway answering — is not independently re-runnable
from this verify worker: this worker's Bash allowlist (`harness/worker-settings.json`) has
no `ssh`/network-probe entries (verify-pass workers are scoped to repo/doc review, not live
ops), and the two DB-backed MCP tools that might hold a logged trace of the original probe
(`fleet-memory`, `fleet-state`) returned unauthorized-pending in this headless session with
no human to approve. I did not retry either blocked path (per the "re-scope, don't retry"
rule). This is also moot by design: the diff itself treats the LAN finding as probabilistic
("unpowered, unplugged, or off the LAN") and routes the actual resolution to wq#4776 for
Frank's physical confirmation rather than asserting it as fact in `nodes.ignore` — so even
if that specific probe output were wrong, the diff's conclusion (keep paging, queue a
physical check) doesn't depend on it being exactly right.

## Conclusion

Pure documentation diff, syntactically valid, consumed nowhere as logic, consistent with
the actual `sh_nodes.py` state-tracking code and `fleet_hardware.json` facts it cites,
correctly leaves rt-ax86u off both `ignore`/`sleepers`, correctly queues the one step that
needs Frank's hands as a non-blocking ticket, and ships with a matching ISSUE-LOG row (no
collision) and a passing 76-test suite. No contradiction found.

**CONFIRM wq#4775.**
