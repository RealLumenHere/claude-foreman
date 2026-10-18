# wq#4283 — verify-pass of wq#4272 (PR#2184)

Verdict: **CONFIRM**

Author: claude-sonnet-5 (fworker-wq4272). Verifier: claude-sonnet-5@high (this ticket),
fresh instance, no escalation triggered.

PR: http://100.121.17.5:3000/lumen/claude-foreman/pulls/2184 — squash-merged as
`3ee3dde3`, single new file `research/wq4272_freedom_esp32_motor_control.md` (177
lines, docs-only, no code).

## What the ticket asked

Desk research on the Freedom (Polaris/Zodiac) ESP32-WROVER motor control: (1)
UART/SPI/I2C to the motor driver, (2) PWM frequency/duty, (3) driver IC identity,
(4) direction control method, (5) USB/JTAG flashing, (6) WROVER pinout. No
physical board access from this worktree (Lumen is headless).

## How I tried to break it

This is a research deliverable, not code, so the "probe" is: is the prior-art
search genuine (not fabricated per Tier-0 instinct 8), are the claims accurately
sourced, are the "confirmed" items actually independently-verifiable facts, and
is everything the author couldn't verify honestly labeled as such.

1. **Reproduced the cited fleet-search queries myself** rather than trusting the
   doc's prose:
   - `fleet-search web "Polaris Freedom cordless robotic pool cleaner teardown
     hack firmware"` → returns the manuals.plus/notice-facile Polaris Freedom
     result, `cached_at: 2026-10-18T21:00:44Z` — matches the doc's claimed
     session and timestamp window.
   - `fleet-search web "brushed DC motor driver UART control ESP32 robot"` →
     top results are exactly `espp/basicmicro` and
     `github.com/sonTungN/esp32-dc-motor-control`, the two sources §1 cites for
     "UART-to-driver shows up on bigger multi-motor platforms, not single-MCU
     designs." Not fabricated.
   - `fleet-search web "robotic pool cleaner teardown motor driver IC DRV8833
     TB6612 L298"` → top results are Roger Random's `roger-random.github.io`
     TB6612-vs-DRV8833 posts, exactly as cited in §3. Not fabricated.
   - `fleet-search github "polaris freedom esp32 pool robot"` → reproduces the
     401/no-token failure verbatim (`"github 401 — no usable token..."`),
     matching the doc's claim and `kb/ISSUE-LOG.md` row 1433 (checked directly:
     `grep -n "^| 1433 "` finds the exact row, status OPEN, matching the doc's
     citation).
   - `fleet-search read` on the raspberrypi.com Pico pool-robot rebuild article
     → content confirms "drive motor" (singular), "planetary gearbox," and
     direction/water-expulsion as the Pico's two separate jobs — consistent
     with what §3/§4 cite it for. The doc's extrapolation to "redirect water
     jet via valve" is a bit more specific than the source actually states, but
     the doc already hedges this correctly ("a pattern to check for, not a
     finding," "not confirmed to be the same internals as Freedom") — not a
     misrepresentation, just a flagged guess.

2. **Independently checked the "confirmed" generic ESP32-WROVER facts** (§5/§6)
   against known datasheet facts rather than taking the doc's word: UART0
   TX/RX = GPIO1/GPIO3, JTAG = GPIO12/13/14/15 (MTDI/MTCK/MTMS/MTDO), GPIO0
   download-mode strapping, GPIO34-39 input-only, GPIO16/17 reserved for PSRAM
   on WROVER (not WROOM) — all accurate, nothing invented.

3. **Checked the epistemic hygiene**: the doc's own "confirmed vs not" table
   (lines 142-151) matches the body — items 1-4 are consistently labeled
   unconfirmed predictions throughout, items 5-6 are the only things called
   confirmed, and those are the only two claims that don't depend on the
   specific Freedom board. No case of an unconfirmed guess being stated as
   fact elsewhere in the doc.

4. **Checked doctrine compliance**: physical verification is queued as a task
   for whoever has the physical unit (§"Physical task queued"), explicitly not
   gated on Frank — correct per Tier-0 instinct 3. The GitHub-token gap was
   logged to `kb/ISSUE-LOG.md` (row 1433, verified above) rather than silently
   worked around or left unmentioned.

## Side note, not a finding against this PR

`/home/lumen/bin/wq show 4267` is a separate, still-open ticket ("FREEDOM: RE
the ESP32-WROVER motor controller — protocol, pinout, flash method") that
substantially overlaps wq#4272's scope — same device, same four technical
questions. That's a dispatch-level duplicate-ticket issue, not a defect in
wq#4272's research or PR#2184's content, so it doesn't change this verdict;
flagging for whoever triages the Freedom-bot ticket backlog.

## Conclusion

No contradiction found. Sources check out against live re-run queries, generic
technical claims check out against known ESP32-WROVER facts, and every
board-specific claim is correctly hedged as an unconfirmed prediction with a
queued physical-verification task. Confirming on the board.
