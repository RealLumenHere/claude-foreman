# wq#3536 verify-pass: wq#3506 [selfheal] fleet node thor offline on tailscale for 30 min — CONTRADICT

**Verdict: CONTRADICT.** PR 1616 (http://100.121.17.5:3000/lumen/claude-foreman/pulls/1616,
merged as `2cefea39`) blanket-adds `thor` to `selfheal.yaml nodes.ignore`, reasoning that thor
"sleeps/roams off tailnet by design, not a fleet outage" (TLP/ASPM/s2idle power management). That
reasoning is accurate for the *sleep* case but the fix is not scoped to it — `sh_nodes.py`'s class-6
detector has no cause-awareness at all, so the blanket ignore also silences the exact outage class
that `kb/ISSUE-LOG.md` row 1175 (wq#3508, same file, five days earlier) explicitly decided must keep
ticketing: `thor.charger_status` is a **diagnosed, still-unresolved** DEFICIENT USB-C PD source that
has already caused 5 unplanned "hard deaths" (battery hits 0% while EC reports `status=charging`).
Those hard deaths present on the wire identically to a benign sleep — `tailscale status` just goes
offline for >=1800s — so this fix cannot tell them apart, and now ticket-suppresses both.

## What the PR does

Diff (`2cefea39`, 1 file, +5/-1), confirmed via `git show 2cefea39`:

```
   nodes:                  # CLASS 6
     enabled: true
     offline_after_s: 1800
-  ignore: []                      # lumen_state node names meant to be off
+  ignore:                          # lumen_state node names meant to be off
+    - thor                        # wq#3506: Frank's active laptop (TLP/ASPM/s2idle,
+                                   # 85% charge cap) — sleeps/roams off tailnet by
+                                   # design, not a fleet outage; never remote-managed
+                                   # per CLAUDE.md Tier-0 #2 either way
```

`sh_nodes.py:116` — `if node is None or node in ignore or rec["host"] in ignore:` — is a flat
identity check against the configured set. There is no branch anywhere in the module that
consults `power_state`/`charger_status`/any other fact before suppressing; `ignore` means "never
file for this node again, for any reason," full stop (confirmed by reading the whole file, 149
lines, and by the generic test `test_node_back_online_resolves_and_ignore_list_suppresses` which
exercises precisely this unconditional-suppression contract).

## The contradiction, with evidence

`kb/ISSUE-LOG.md:1324`, row 1175 (wq#3508, filed 2026-10-07, same selfheal module, five days
after wq#3506's charger diagnosis):

> Did not add thor to selfheal.yaml nodes.ignore: lumen_state shows thor.charger_status as a
> diagnosed-but-unfixed DEFICIENT USB-C PD source with 5 prior hard deaths (2026-09-27 to
> 2026-10-01) — this is Frank's primary active workstation with a real unresolved hardware fault,
> not a node meant to be off, so **future real outages should keep ticketing**.

That decision is not stale. Read live today (2026-10-07) via `lumen_state.py current`:

```
thor.charger_status = DEFICIENT — USB-C PD SOURCE (diagnosed 2026-10-01, research-only, no
changes applied). All 5 hard deaths since 2026-09-27 = battery hit 0-1% while EC reported
status=charging (upower history-charge: 1%/3-4min decline for hours before each death). Source
delivers ~30W-ish; under load (perf profile PL1 25W + display) net drain -> 0% -> EC cuts power;
cold+unresponsive = flat battery not a hang. Battery itself healthy: 54.0/62.1Wh=87%, 298 cycles.
...  (since 2026-10-01)  [UNVERIFIABLE — no probe]
```

"research-only, no changes applied" and `since 2026-10-01` — unchanged six days later, the same
day wq#3506 filed and PR 1616 merged. The fault this earlier decision was guarding against is
still live. wq#3506's own commit message cites only the power-saving facts
(`thor.power_mgmt` — TLP/ASPM/s2idle/85pct) and never mentions `thor.charger_status` or
ISSUE-LOG row 1175, which is either a missed prior-art read (the doctrine's own "read
kb/ISSUE-LOG.md — your issue may already be logged" step) or a read-and-silently-overridden
decision; either way the PR's own text gives no indication the charger fault was weighed.

## Probe — falsifiable, both directions

Wrote a probe (`/tmp/fworker-wq3536/probe_thor_ignore.py`) that drives `sh_nodes.sweep()` with
the **real shipped `selfheal.yaml`** (not a synthetic node name), thor's real
`lumen_state.py`-sourced `tailscale_ip` (100.112.151.100), and a fake `tailscale status --json`
peer reporting thor offline for exactly `offline_after_s`. This stands in for a hard-death outage
exactly as it would appear on the wire — the detector cannot distinguish it from sleep:

```
$ python3 /tmp/fworker-wq3536/probe_thor_ignore.py
tickets filed: []
PASS: real selfheal.yaml ignore list suppresses the thor ticket
tickets filed (pre-fix control): [{'title': '[selfheal] fleet node thor offline on tailscale for
30 min', 'detail': 'thor (tailscale host thor, 100.112.151.100) offline 30 min, LastSeen
2026-10-06T21:20:00Z\n  power_mgmt = TLP 1.8 auto AC/BAT\nIf a node is meant to be off, add it to
selfheal.yaml nodes.ignore.\n\nFiled automatically by fleet-selfheal (wq#3354) class nodes.',
'lane': 'build', 'pri': 'p1'}]
PASS: control confirms the probe is falsifiable (pre-fix config still tickets)
```

The negative control (same scenario, `thor` removed from `ignore`) files the ticket — proving the
probe is not vacuous (wq#2630): it can fail, and with the real `selfheal.yaml` shipped by PR 1616
it does not — thor's offline alerting, including for a hard death, is silenced today.

## Things checked that are NOT contradictions

- **YAML is well-formed and the node-name match is correct.** `yaml.safe_load` parses cleanly;
  `thor.tailscale_ip = 100.112.151.100` in live `lumen_state` matches the ticket's cited IP, so
  `node_for()` resolves to `"thor"`, which is exactly the string added to `ignore`. No casing or
  typo bug.
- **`thor.hardware` confirms it genuinely is a laptop** (Samsung Galaxy Book Pro 360 13 /
  NP930QDB-KE4US), and `thor.power_mgmt` matches the commit message's TLP/ASPM/s2idle/85pct claims
  verbatim — the sleep-is-normal half of the PR's reasoning is accurate, just not the whole story.
- **Test suite: 38/38 pass** (`pytest ops/wq3354-fleet-selfheal/tests -q`), unaffected by this
  change — there is no existing test asserting the real shipped `ignore` list's *contents*, so the
  suite was never positioned to catch this; it only covers the generic mechanism (with a fake
  node), which behaves exactly as designed.
- **The truncated `kit /home/lumen/fleet/thor-powe` in wq#3506's own ticket body** (no ellipsis,
  hard-cut at 200 chars — the wq#3508 bug pattern) is a timeline artifact, not a regression: `git
  log --follow` shows the `_trunc()` fix landed at `1601d418` (2026-10-07 07:20:14), *after*
  wq#3506 was filed (2026-10-06 21:48:40) and after PR 1616 merged (07:17:19). Not in scope here.
- **"never remote-managed per CLAUDE.md Tier-0 #2 either way"** (from the commit message) is true
  but answers the wrong question: Tier-0 #2 forbids *remote actuation* on thor, it says nothing
  about the value of *alerting* on it. `sh_nodes.py` never actuates anything for any node (it only
  files a ticket with diagnostic facts) — the ticket's value is informing a human, which is exactly
  how the charger fault was originally diagnosed (wq#3508 traces back to this same detector). The
  commit's own justification conflates "can't fix it by hand remotely" with "no point knowing
  about it," which is the reasoning error underlying the contradiction above.

## Disposition

`wq#3536` closes with verdict **CONTRADICT**, escalated to opus-5@high per ticket instructions
(archetype verify-pass policy: escalate_on_contradiction). Not re-implemented — verify-pass is not
a re-implementation gate (precedent `reports/872_verify_wq853_fleet_maint_run.md`,
`reports/1268_verify_wq1253_runtime_file_split.md`). The follow-up judgment pass should decide how
to narrow wq#3506's fix — e.g. gate the `thor` ignore on `thor.charger_status` no longer being
DEFICIENT, or otherwise distinguish a known-sleep offline from a hard-death offline — without
re-opening the original ticket-spam-for-sleep problem wq#3506 was filed to fix.
