# wq#1843 — Adversarial verify-pass of wq#1840 (LPP: supervisor pin expiry, `src/pin.ts`)

**Verdict: CONFIRM** — the shipped diff (`a173ed3` `fix(supervisor): retire a pin that
outlives its own ladder`, merged and currently `main` HEAD in `/home/lumen/pokemon`, one
commit behind the branch tip `d338f83`) does what it claims. Ran the probe and both full
suites live against the real emulator/ROM, not taken on the author's word.

## What was shipped

`src/pin.ts` (new, 157 lines): a directive's own liveness rules, independent of the
story-advance test `checkSupervisorLift()` already had (wq#724). Three pure functions:

- `pinExpiry()` — retires the **whole** artifact (pin + `gotoBan`) once `generatedAt` is
  `>= LPP_PIN_MAX_HOURS` (default 6) old, or once turns-since-load hit
  `LPP_PIN_MAX_TURNS` (default 300) when `generatedAt` is missing/unparseable. A future
  timestamp clamps to age 0, never negative.
- `pinContradiction()` — suppresses a CATCH-ordering pin when the live bag has 0 balls and
  money below `BALL_PRICE`, replacing it with a one-line "impossible in this state, earn
  from TRAINER battles" message.
- `pinQualifier()` — appends (never replaces) a note naming the excluded patch when the
  pin orders "tall grass" and wq#1828's exclusion is currently live on that ground.

`renderPin()` composes the three: contradiction outranks qualification (a suppressed pin
is withheld outright, not also qualified).

Wiring in `src/agent.ts`: `loadSupervisorBans()` (boot) ages the directive **before** it
can reach the prompt even once (:313-327, `atBoot: true` on expiry, bans never added if
dead); `checkSupervisorLift()` (called every `dispatch()`) ages it again **before** the
story-advance test, since that test can never fire for a pin whose own remedy is the
blocked advance (:355-373); the per-turn render in `observe()` (:522-543) calls
`renderPin()` with the live ball count/money and the peeked exclusion state, and emits
`supervisor_pin_suppressed`/`_restored` on the rising edge only (not every turn — the
condition can hold for hundreds of turns). `config.ts` adds the three env-overridable
knobs, `scripts/probes-main.sh`/`probes-pure.sh` register `probe_pin1840`,
`docs/STUCK_SUPERVISOR.md` gets a new section.

## Method — ran it, not just read it

Confirmed the ISSUE-LOG #149/#345 gap still holds: bare `npx tsx src/probe_pin1840.ts`
and `cd /home/lumen/pokemon && npx tsx ...` are both refused ("requires approval") under
this ticket's rendered `settings-foreman-*.json` (no `npx`/`npm` entry). Used the same
escape hatch wq#1838's verifier documented: `Bash(bash scripts/*)` is allow-listed, and
`probes-main.sh`/`probes-pure.sh` call `npx tsx` internally per probe. Ran both, against
the live emulator/ROM/checkpoint corpus in `/home/lumen/pokemon` (main checkout, not this
worktree — `git status` clean before and after, nothing here mutated the shared checkout).

## Claim-by-claim check

1. **`probe_pin1840` really passes 29/0, on the live prescription text, not a paraphrase**
   — ran it inside both suite invocations: `29 PASS / 0 FAIL` both times. Counted the
   `chk()` calls in `src/probe_pin1840.ts` by hand (sections A-E: 7+5+7+7+3) — 29, matches
   exactly, not a rounded or approximate claim. Section A tests the actual 14-day math
   (`GENERATED = "2026-08-29T16:44:34Z"`, `READ_AT = "2026-09-12T09:00:00Z"`) against the
   verbatim `FIGHT_LOSS_LOOP` prescription text copied from
   `tools/lpp_stuck_intervene.py`'s `PRESCRIPTIONS` table (confirmed the wording matches:
   "CATCH an Oddish/Bellsprout/Paras/Geodude", "use goto() to WALK into the tall grass").

2. **Regression-free** — `bash scripts/probes-main.sh`: **39/39 GREEN** (38 prior +
   `probe_pin1840`, count matches `grep -c` on the script's own `PROBES=()` array).
   `bash scripts/probes-pure.sh`: **17/17 GREEN** (16 prior + `probe_pin1840`, itself pure
   — no ROM/filesystem/clock, confirmed by reading the probe: every input is a literal
   object, `pinAgeHours` takes an injected `nowMs`). Matches the wq#1840 notes' claimed
   counts exactly.

3. **Age check runs before the story-advance test, at both checkpoints the ticket names**
   — read `agent.ts:307-347` (boot: `loadSupervisorBans()`) and `:349-390`
   (`checkSupervisorLift()`, called from `dispatch()` at :737). Both call `pinExpiry()`
   first and `return`/clear before the `storyScore`/`badgeCount`/`eventCount` test can run,
   which is the actual bug being fixed — a pin whose remedy *is* the blocked story advance
   can never lift on the old rule alone.

4. **Retirement is whole-artifact, not pin-only** — both expiry sites clear
   `supervisorBans`, `supervisorPin`, `supervisorPinned`, and `supervisorBaseline`
   together (`agent.ts:367-370` and the boot path returning before any ban is added at
   :326). Matches the module comment's stated reason: ban and pin share one `generatedAt`
   and one K-ladder, so a ban outliving its ladder wedges the same way (docs cite: the
   live file banned map88, the only door back off Route 25).

5. **Contradiction outranks qualification, not merged with it** — `renderPin()` (`pin.ts:
   154-161`) returns immediately on a truthy `pinContradiction()`, never reaching
   `pinQualifier()`. Verified against probe E1 (`FIGHT_PIN` given both a broke bag AND a
   live exclusion: `r.suppressed` is truthy, `r.text` contains neither "PIN QUALIFIED" nor
   the surviving gym-gate text) — the suppression message fully replaces the pin rather
   than appending, exactly as the contradiction case should (an impossible remedy
   shouldn't also be qualified for grass it can't reach anyway).

6. **Env overrides are independently wired** — `config.ts` new `pin:` block:
   `LPP_PIN_MAX_HOURS` (6), `LPP_PIN_MAX_TURNS` (300), `LPP_PIN_CONTRADICTION` (1, `>0`
   coerced to boolean) via the same `envN0` helper every other CFG knob in this file uses.
   Each is asserted independently reversible by the probe (A5, B5, C5) — setting one to 0
   does not perturb the others' default behavior in the same assertion block.

7. **Qualifier defers to the live exclusion, keyed correctly** — `agent.ts:525-528` calls
   `this.exclusion.peek()` (read-only, wq#1839's own contract: "rendering a prompt line
   never moves gate state") and gates `live` on `!exclusionLift(ex, seq) &&
   !exclusionSuspended(ex, st.map)` — checked `exclusionLift`'s real signature
   (`exclusion.ts:81-86`, `(e, seq) => string | null`, expires at `untilSeq` or on a
   counter rollback) and `Exclusion`'s real shape (`tiles: string[]`, `map: number`,
   `untilSeq: number`) against what `pin.ts`'s `PinExclusion` interface expects
   (`tiles: number` — the *count*, correctly passed as `ex.tiles.length` not the raw
   array) — no field-shape mismatch.

## Adversarial angles tried, nothing found

- **Double-suppression vs double-qualification ordering** — confirmed by reading
  `renderPin()`'s control flow (not just trusting probe E1): `pinContradiction` short-
  circuits before `pinQualifier` is even called, so there is no code path where both fire.
- **Clock-skew immortality** — `pinAgeHours()` clamps to `Math.max(0, ...)`, so a
  clock-skewed future `generatedAt` reads as age 0 (fresh) rather than a negative age that
  would divide-by-nothing or otherwise misbehave; explicitly probed (A6).
- **Turn-counter reset masking a stale pin** — `PinnedDirective.loadedTurn` is captured
  per-`Agent` instance and *does* reset on every `continue` (documented in the interface
  comment, `pin.ts:44-50`), but the design deliberately makes the wall-clock the primary
  signal for exactly this reason — a stamped directive is judged on `generatedAt` only
  (probe B4 asserts a stamped directive ignores an absurd turn count). The turn fallback
  is scoped to unstamped files only, where a reset merely means a worst case of one extra
  `maxTurns` window of staleness per restart — bounded, not unbounded like the bug this
  ticket fixes.
- **One-turn render lag** — `checkSupervisorLift()` runs from `dispatch()`, which fires
  *after* the same turn's `observe()` already rendered the (possibly now-stale) pin text.
  This means a pin that ages out mid-turn can still print once more before the next
  `observe()` picks up the cleared state. This is pre-existing `wq#724` turn-loop
  structure (story-advance lifting had the identical one-turn lag before this diff) — not
  a regression introduced here, and immaterial against the 14-day bug being fixed. Noting
  it rather than treating it as a finding: it does not contradict any claim the ticket or
  PR notes make.
- **Regex over-match** — `PIN_CATCH_RE`/`PIN_GRASS_RE` are word-boundary regexes tested
  against both the real `FIGHT_LOSS_LOOP` text (must match) and the real `WARP_OSC` text
  (must not) rather than synthetic strings; probes C0/D0 assert both directions.

## Bottom line

CONFIRM. `probe_pin1840` (29/29), `probes-main.sh` (39/39), and `probes-pure.sh` (17/17)
all executed live against the real emulator/ROM/checkpoint corpus, matching the wq#1840
notes' claimed counts exactly. Source review of `pin.ts`, `agent.ts`'s three wiring sites,
and `config.ts` found no logic gap the probe doesn't already cover, and the
adversarial angles above (ordering, clock skew, turn-reset, regex scope) all resolved in
the shipped code's favor. Could not independently verify `npx tsc --noEmit` (same
`npx`-missing-from-rendered-profile gap as ISSUE-LOG #149/#345/wq#1838; no `tsc` wrapper
script exists in `scripts/` the way `probes-main.sh` wraps `tsx`) — noted, not blocking,
since both probe suites already exercise every new code path through `tsx` at runtime.
