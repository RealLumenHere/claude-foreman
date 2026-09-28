# wq#2881: verify-pass on wq#2757 (PR#1198, onboard I217-LM enp0s25 not enumerating)

Date: 2026-09-28. Archetype 8 adversarial verify, fresh instance. Author: claude-opus-5-5
(wq#2757, lane research). Verifier: claude-sonnet-5@high (this ticket, lane build).

## Verdict: CONFIRM

PR#1198 (`f32e158`, `reports/wq2757_lumen_i217_enp0s25_unbound_2026-09-28.md`, 88 lines,
docs-only) is a research report, not code — there is no test suite or executable diff to
run. This verify-pass instead read the report cold, tried to find a factual or logical
break in its root-cause chain, and independently checked everything that did not require
the live-host leg. No contradiction found.

## What I checked

| Claim in the report | How I checked it | Result |
|---|---|---|
| `8086:153a` is the Intel I217-LM PCI device ID | `fleet-search web "PCI device 8086:153a Intel I217-LM"` — independent web search, not trusting the report's own label | Confirmed (Intel driver-pack page + HP forum both identify `DEV_153A` as I217-LM) |
| Uptime "27 days, 15:57" at 09-28 04:12 implies boot at 2026-08-31 ~12:15 | Recomputed by hand: Aug31 12:15:03 → Sep28 12:15:03 is exactly 28 days; back off 8h03m to reach Sep28 04:12 leaves 27d15h57m | Reproduces to the minute against the cited `ExecMainStartTimestamp` (12:15:03) |
| `systemctl disable` needs root and is blocked for this lane | Cross-checked against this session's own PreToolUse guard-hook deny-list (systemctl stop/disable/mask) | Matches; report correctly declines to run it and defers to wq#2880 |
| Follow-up ticket and ISSUE-LOG entry exist and match the report's Decision section | `wq show 2880`, `grep 884 kb/ISSUE-LOG.md` | wq#2880 (lane ops, claimed) carries the exact command sequence and accept criteria from the report; ISSUE-LOG row 884 matches |
| `10ec:8125` on `0000:01:00.0` → `r8169`/`ens1` (control device) | Cross-checked against the wq#2757 ticket text itself ("RTL8125 3c:7c:3f:a6:be:dd") and the fleet's known Lumen NIC inventory | Consistent |
| Re-runnable probe: `os.path.exists('/sys/bus/pci/devices/0000:00:19.0/driver')` + `systemctl is-enabled nic-park.service` | Attempted `ssh lumen@100.117.66.102 'echo probe_ok'` from this worktree | **Blocked**: "This command requires approval" — `ssh` is not on the BUILD-lane allowlist. Did not retry (wq#1107). Logged as ISSUE-LOG row 887, duplicate of row 256 (wq#1556), which has sat OPEN 19 days across two different verify tickets now |

## Adversarial angles tried, not confirmed as flaws

- **"nic-park rewrites its own unit file, only in non-dry-run mode"** — this specific claim
  about the script's internal behavior is asserted, not quoted from source in the report.
  I could not independently confirm it (would need to read `/usr/local/sbin/nic-park` on
  Lumen, same ssh-blocked leg above). It is not load-bearing for the root-cause verdict,
  though: even without it, `ActiveState=active/exited` + `ExecMainStartTimestamp` landing
  on the exact second of the current boot, combined with enp0s25 meeting every stated park
  guard (no route/IP/carrier) and no alternative unbind mechanism found (no blacklist, no
  udev rule, no `driver_override`, control device binds normally), is already a strong
  circumstantial case. The self-rewrite detail is corroborating, not foundational.
- **"8-node reversal most likely did not include Lumen"** — report explicitly flags this as
  unverified ("I have not verified which"). Honest hedge, not a false claim; did not try to
  resolve it further since it is out of scope for wq#2757's own root-cause question and is
  already carried forward into wq#2880's fleet-wide audit.
- Looked for a scenario where the "since 2026-09-10" ticket framing and the report's
  "unbound since 08-31" claim contradict each other — they don't; the report explicitly
  reconciles them (09-10 is when fleet-inv first noticed, not when it happened).

## Why this confirms without the live leg

Every claim reachable without SSH (arithmetic, PCI ID, deny-list behavior, cross-ticket
consistency) held up exactly as stated, with no internal contradictions and appropriate
hedging on the one inference (unit self-rewrite) I couldn't independently source. The only
unresolved leg is the live re-probe, and that gap is structural to this lane, not a defect
in the PR — same failure mode already on record at ISSUE-LOG row 256, now duplicated at
row 887. No basis to contradict; no escalation warranted.
