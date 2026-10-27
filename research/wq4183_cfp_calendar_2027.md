# wq#4183 — Limpet CFP calendar, 2027 (first stable ISO tied to a conference)

Researched 2026-10-26 (orchestrator-side: the fworker lanes have no WebSearch grant). Strategy: community-strategy.md section 2 tier 4, the Kali pattern. NO SUBMISSION WITHOUT FRANK.

"Confirmed" = read on an organizer or listing page this session. "Projected" = my extrapolation from the previous editions' cadence; re-check the organizer page when the CFP opens, dates move.

| Venue | Event dates | CFP window | Status | Fit for a plug-a-board-in demo |
|---|---|---|---|---|
| hardwear.io USA 2027 | late May / early Jun 2027 (2026 training was 26-28 May) | 2026 edition: opened 8 Jan, closed 5 Mar. Projected 2027: opens ~Dec 2026-Jan 2027, closes ~early Mar 2027 | dates confirmed for 2026 only, 2027 projected | Best fit: hardware-security audience, has a Tool category (open-source tools and hardware), travel reimbursement for New Research only |
| REcon 2027 (Montreal) | June 2027, dates not yet posted | page says CFP "opening soon". 2026 edition: first round ended 14 Mar, closed 11 Apr. Projected 2027: first round ~mid Mar, close ~mid Apr | CFP dates unannounced | Strong fit (reverse engineering, single track, 40 percent training rebate offered to speakers in 2026) |
| Black Hat USA 2027 Briefings + Arsenal | ~31 Jul-5 Aug 2027 per third-party listings, official dates TBD (Mandalay Bay) | 2026 Briefings: opened 27 Jan, closed 23 Mar, notification 22 May. 2025: 5 Feb-2 Apr. Projected 2027: late Jan-late Mar. The Arsenal tool call usually closes earlier than the Briefings call | official 2027 CFP not open | Arsenal (tool demo) is the natural slot; Briefings need new research |
| DEF CON 35 | 5-8 Aug 2027, Las Vegas Convention Center West Hall | Each village runs its own CFP. From earlier years: main CFP closed ~1 May, Hardware Hacking Village ~25 May, IoT ~1 Jun, RF ~16 Jun, Recon Village (2025) 15 Apr-15 Jun, MEACS (2026) 22 Jun-21 Jul. Projected 2027: villages open Apr, close late May-June | dates confirmed, village windows projected | Hardware Hacking, IoT and RF villages are the live-demo venue; village CFPs are the latest-closing option |
| Black Hat Asia 2027 | Singapore, dates TBD | CLOSED 16 Oct 2026, notification by 5 Jan 2027 | confirmed | missed for this cycle |
| Black Hat Europe 2026 | 9-10 Dec 2026, London | CLOSED 22 Jul 2026 | confirmed | missed |
| hardwear.io NL 2026 | 19-20 Nov 2026 | CLOSED 20 Jul 2026 | confirmed | missed; NL 2027 projected to open ~Apr-May 2027, close ~Jul 2027 (a fallback if the ISO slips) |
| CCC Congress (Dec) | not verified for 2026/2027 | historically closes late Sep-Oct | no current data found | skip until a 2027 call appears |

## Recommended sequence

1. Earliest hard gate is hardwear.io USA: its call opens around the turn of the year and closes about early March 2027, so a New Research or Tool submission needs the first stable ISO or at least a reproducible demo by Feb 2027.
2. Black Hat Arsenal call (Feb-Mar 2027) and REcon (closes ~mid Apr) come next, then the DEF CON 35 village calls (late May-June 2027), which are the last chance for a live demo in Aug 2027.
3. Target: first stable ISO tagged by end of Feb 2027, public demo repo ready by April.
4. Calendar alerts: check organizer pages 15 Dec 2026, 1 Feb, 1 Mar, 1 Apr, 1 May 2027.

## Abstract (village / tool demo, ~150 words)

**Plug it in, watch it get a name.** Reverse engineering a board usually starts with an hour of squinting at part numbers and guessing which header is the UART. This demo starts the other way: we connect an unknown embedded board to Limpet, an open reverse-engineering appliance, and let it work. Within minutes its hardware abstraction layer fingerprints the target, finds the serial console and debug pins, identifies the SoC and flash, and pulls firmware, all live on stage with the board powered by the audience's own pick from the table. Every step is gated: the system proposes, shows its evidence, and waits before it ever energizes a pin, so a wrong guess cannot cook the board. We show where it is right, where it is wrong, and how to extend its drivers. Attendees leave with the tool, the board profiles, and a repeatable workflow they can run on their own bench.
