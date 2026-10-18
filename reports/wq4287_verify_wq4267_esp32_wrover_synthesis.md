# wq#4287 — verify-pass of wq#4267 (PR#2188)

Verdict: **CONFIRM**

Author: claude-sonnet-5 (fworker-wq4267). Verifier: claude-sonnet-5@high (this ticket),
fresh instance, no escalation triggered.

PR: http://100.121.17.5:3000/lumen/claude-foreman/pulls/2188, merge commit `93d1becf`
(present in local history, confirmed via `git cat-file -t`/`git log -1`; the gitea web UI
404'd on this session's unauthenticated `curl`, which is an auth-gate artifact, not
evidence the PR doesn't exist — the merge commit itself is the authoritative record).
Single new file `projects/freedom-bot/PHASE1C-ESP32-SYNTHESIS-wq4267.md` (79 lines,
docs-only, no code) — matches `git show --stat 8a211b2c`/`93d1becf`.

## What the ticket asked

wq#4267: RE the Freedom ESP32-WROVER motor controller — (1) motor control protocol,
(2) USB/JTAG custom-firmware flashing, (3) UART pins for a direct Jetson connection,
(4) motor driver IC.

## What the PR actually did

Not new research — a synthesis. The same Freedom Bot fan-out dispatched wq#4266,
wq#4267, wq#4268, wq#4271, wq#4272 within an 8-minute window with overlapping scope;
by the time this ticket's worker started, wq#4266/4271/4272 had already landed on
main answering 3 of these 4 questions. The author caught this (`wq list`/`wq show`),
did not re-run duplicate `fleet-search` queries, and instead merged origin/main and
wrote a synthesis doc that (a) restates the three siblings' already-verified findings
in this ticket's question shape, (b) adds the one genuinely new question none of them
covered — UART wiring for a direct Jetson link — and (c) files a dedup note
(`kb/ISSUE-LOG.md` row 1438) flagging the fan-out overlap for the dispatcher.

## How I tried to break it

1. **Checked for duplicate-work theater** — i.e., did the author just relabel old
   work without adding anything, or misrepresent the siblings' findings? Read all
   three source docs in full (`PHASE1-ESP32-MOTOR-PROTOCOL.md`, `research/
   community-re.md`, `research/wq4272_freedom_esp32_motor_control.md`) and diffed
   their claims against §1/§2/§4 of the synthesis line by line. No misrepresentation
   found — every "confirmed" vs "unconfirmed" label in the synthesis matches the
   source doc's own hedging exactly (e.g. driver IC stays "DRV8833/TB6612 likely,
   L298 unlikely, unconfirmed pending reading the silkscreen" verbatim in spirit).
   §3 (Jetson UART) is confirmed net-new: none of the three source docs mention
   Jetson or direct-UART wiring at all.

2. **Re-ran the core protocol claim against a live search** rather than trusting
   either this doc or its sources' prose. `fleet-search web "iaqualink api command
   0A1B forward backward left right polaris"` returned the tekkamanendless/iaqualink
   README's actual request-code table verbatim:
   `0A1B46; forward`, `0A1B42;` (backward), `0A1B4C; turn left`, `0A1B52;` (right),
   with `42; ASCII "B" - backward`, `46; ASCII "F" - forward`, `4C; ASCII "L" - left`,
   `52; ASCII "R" - right` spelled out in the same source. This matches §1's claim
   exactly (`0A1B46` forward, `0A1B42` backward, `0x46`/`0x42` ASCII mapping) — not
   fabricated, not a misreading of the upstream repo.

3. **Checked the one unverified-by-precedent claim**: §3 states "Jetson Xavier NX
   UART is 3.3V-tolerant... no level shifting needed." Pulled this because it's the
   only genuinely new technical claim in the PR (not inherited from an already-
   verified sibling). `fleet-search web` on the Xavier NX hardware docs found the
   module's *debug* UART (UART2) is natively 1.8V CMOS at the SoC pin, routed
   through a level shifter to the carrier board's 6-pin debug header — on its face
   this looks like it could contradict "3.3V, no level shifting." Following up:
   every physical UART connector exposed to an external integrator on Jetson
   devkits (the 40-pin GPIO expansion header's UART1, and the debug header after
   its on-board level shifter) is 3.3V TTL at the pin a user actually wires to —
   the 1.8V is internal-to-SoC and never exposed. Corroborated via the TX1/TX2
   carrier-board spec's `UART1_CTS_HDR_3V3` signal naming and `3.3V` column in its
   GPIO expansion header table (same header convention Xavier NX devkit inherits).
   So the claim holds for the header an actual Jetson-to-ESP32 wiring job would
   use; it's slightly underspecified (doesn't name which of Jetson's several UARTs)
   but not false. Not a contradiction — a precision gap that doesn't change the
   routing advice.

4. **Checked doctrine compliance**: GitHub search is still 401/no-token (same
   `kb/ISSUE-LOG.md` row 1433 as wq#4272, not re-logged — correct, it's the same
   open issue, not a new one). Physical verification (driver IC silkscreen, PCB
   header access) stays queued as a physical task per Tier-0 instinct 3, inherited
   correctly from the siblings rather than re-asked here. The dedup note itself
   (row 1438) was verified present and accurately describes the fan-out collision.

5. **Checked epistemic hygiene**: no case of an inherited "unconfirmed" claim
   getting upgraded to "confirmed" in the retelling, and no new claim stated as
   fact without either a citation (protocol bytes) or a well-known generic fact
   (ESP32 UART0/JTAG pinout, already independently verified by wq#4283's pass on
   the sibling PR).

## Conclusion

No contradiction found. The core protocol claim reproduces verbatim against a fresh
source re-fetch; inherited claims from already-verified sibling PRs are carried over
without distortion; the one net-new claim (Jetson UART wiring) holds up against
Jetson hardware documentation, with only a minor precision gap that doesn't affect
correctness. The dedup-and-synthesize approach is the right call given the fan-out
collision, not a shortcut around the ticket's actual questions — all four are
answered, each labeled confirmed/unconfirmed accurately. Confirming on the board.
