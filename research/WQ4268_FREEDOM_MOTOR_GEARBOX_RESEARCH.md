# wq#4268: Freedom pool-robot motor/gearbox research — ID the drivetrain, size an indoor-speed ratio, survey drop-in options

Goal (per ticket): the Freedom's drivetrain is geared for crawling across a submerged
pool floor; on dry hardwood it's reportedly the wrong ratio. Identify (1) the motor(s),
(2) the installed gearbox ratio, (3) what ratio would give walking-speed indoor movement,
(4) whether a drop-in replacement gearbox exists for the motor housing.

## Naming collision, resolved first

"Polaris Freedom" is two unrelated products from two unrelated companies, and the first
round of web search returned both tangled together:

- **Polaris Industries' "Freedom" personal watercraft** (2003-2005ish jet ski, gas
  engine, Polaris-brand ATV/PWC division) — has its own factory service manual ("Polaris
  Freedom 2004 Workshop/Watercraft Repair Manual") covering engine, hull, and a
  **vehicle transmission/gearbox** completely unrelated to pool-cleaning. Several
  `fleet-search web` hits (the "SONAR OFF"/"MFI"/compass instrument-panel manual, the PWC
  repair-manual listings) are this product and are **not** relevant to this ticket.
- **Polaris (pool-products brand, owned by Fluidra) "Freedom" robotic pool cleaner** —
  cordless, battery-powered, tracked ("bulldozer-style treads"), the thing the ticket is
  actually about. Also sold as **Freedom LT** and **Freedom Plus** variants. A third,
  older "Polaris Freedom" (65/165/Freedom) is a **suction-side** cleaner with no onboard
  motor at all (driven by the pool pump's suction) — also irrelevant to a motor/gearbox
  question.

Every finding below is about the cordless tracked robotic Freedom family and its
drivetrain relatives in the wider Polaris robotic line (9300/9350/9550/9650/Alpha/
P825/P8050 — same drivetrain architecture, better-documented in repair forums than the
newer Freedom specifically).

## Prior-art search note (doctrine-mandated first step)

Ran `fleet-search web/reddit/github` per the RE doctrine before reasoning from scratch.
Status, so the gaps below aren't mistaken for missed effort:

- All 5 SearXNG general engines (brave, duckduckgo, google cse, startpage, wikipedia)
  are suspended/CAPTCHA'd fleet-wide right now — already tracked, not re-logged
  (`kb/ISSUE-LOG.md` row 717 / wq#2601, and the no-`BRAVE_API_KEY` state from wq#2595).
  `fleet-search web` fell back to the keyed DeepSeek-search backend for every query
  below, which worked and returned real results each time.
- `fleet-search github` is unauthenticated (`github 401 — no usable token`) — already
  tracked, not re-logged (`kb/ISSUE-LOG.md` row 325 / wq#1717, open).
- `fleet-search reddit` returned `ok:true, results:[]` for a repurpose/rover-themed
  query — consistent with the already-tracked empty-result-on-partial-outage bug
  referenced in `research/WQ3835_AMD_CPU_TIER_RESEARCH.md:23-26` (wq#2568/wq#2601), not
  re-logged.
- No GitHub/iFixit teardown of the cordless Freedom/Freedom LT drivetrain specifically
  was found. No prior art on this exists anywhere in this repo or `kb/ISSUE-LOG.md`
  either (checked before searching the web, per doctrine) — this is genuinely new
  ground for the fleet.

## 1. What motors does the Freedom use?

**Confirmed for the Freedom specifically:** dual independent drive motors, one per
track, battery-powered — "Dual traction motor enables brushes to rotate independently
for enhanced maneuverability and wall climbing" (Polaris FREEDOM product listing,
intheswim.com/lesliespool.com); "it's powered by a lithium-ion battery and uses two
drive motors and a pair of bulldozer-style treads to navigate the floor, walls, and
waterline" (PCMag's Freedom LT review). Battery/charger numbers found: 9.6Ah battery,
charger rated 100/220V AC in → **29.4V 2A DC out** (intheswim.com spec table); one
retailer listing for the same product (ASIN B0BX9DJS7R) separately states "Lithium
Battery Voltage: 18 Volts (DC)" (ubuy.co.no) — **these two numbers conflict** (29.4V is
exactly 7 Li-ion cells at full charge, 7S nominal ≈ 25.9V, not 18V; 18V nominal is a
5S-class pack that tops out nearer 21V). Flagging as an open discrepancy rather than
picking one — no teardown was found that resolves which is the actual pack chemistry/
cell count. Either way, the drive motors run in the **~18-30V DC** range, consistent
with the wider Polaris robotic line below.

**Not found for the Freedom specifically, but well-documented for the sibling
(wired, non-cordless) Polaris robotic line** that shares the same drivetrain
architecture (9300/9350 Sport, 9550, 9650, Alpha, P825, P8050) — useful as the closest
available proxy:

- Control box runs the drive motors at **24VDC, rated ≤3A total** ("powered by 24V dc,
  pulling no more than 3A according to the sticker on the control box" —
  rcgroups.com/forums/showthread.php?2507332; "All of the motors run on 24 volts... when
  the motors are connected the voltage drops to 0" on a dead unit —
  troublefreepool.com/threads/polaris-9300-failed-troubleshooting-motor-box.143771).
- The bare drive motor (before its gearhead) was identified by one owner as a
  **Crouzet 82 720 013**, spec'd by them as **4800 RPM at 24VDC** feeding into "a
  gearhead that, obviously, slowed down the rpm so that it could drive the wheels at a
  decent pace" (rcgroups.com, same thread). Crouzet's small 24VDC brushed-motor-plus-
  gearhead families (82xxx series) in the same voltage class run 0.5-8 Nm max output
  torque depending on the gearhead ratio fitted (RS-sourced 82330-series datasheets),
  consistent with a small motor meant to be geared way down, not with the raw numbers
  themselves — no public datasheet for the exact 82 720 013 part was found, only the
  forum owner's figure.
  **Confidence: medium — single-source forum report, not a manufacturer datasheet.**
- When the OEM motor dies, the most commonly cited hobbyist substitute part is a
  **Mabuchi RS-385PH-10280, 24V, 150mA** (~$9), independently measured by one owner at
  "150 mA [and] turns at a slow speed" on 8V bench power (troublefreepool.com, same
  thread) — a useful sanity check that the bare drive motor draws well under 1A even
  stalled-adjacent, with the gearbox doing essentially all of the torque multiplication.

## 2. What gearbox ratio is installed?

**Not found — this is the one dead end in this research, and it's a real one, not a
skipped search.** Every Polaris robotic-cleaner repair thread that reaches the gearbox
talks about *symptoms* (stripped gears, a 17-tooth gear, loose screws inside the
housing) and *part numbers for a whole sealed replacement unit*, never a numeric ratio:

- OEM sells the motor+gearbox as a single sealed, non-serviceable **"motor block"**
  per cleaner generation — **Type A** (`R0637800`, for 9300/9350 Sport, supersedes
  `R0516900`/`3264-032`/`3264-038`/`619007`/`POL-201-3780` — poolparthub.com), **Type C**
  (`R0632000`, for P825/P8050 Sport — premierpools.com), **Type D** (`R0638100`, for
  9550/9650iQ — lesliespool.com). Each is $599-750 and ships as a complete module;
  none of the three listings states a gear ratio, output RPM, or torque figure.
  Zodiac/Polaris does not appear to publish that number anywhere consumer-facing.
  **Confidence: high that it's undocumented publicly** (checked the 3 primary OEM-part
  retail listings plus 4 Polaris-cleaner-repair-shop pages, none carry a spec sheet).
- The RobotShop community thread (community.robotshop.com/forum/t/robot-pool-cleaner-
  polaris/43364) is the most detailed hands-on account: owners describe opening the
  gearbox and finding small spur gears (one references a **17-tooth gear** and Polaris
  gear-kit part **`R063900`**, "two for about $10"), swapping a known-good motor+gearbox
  from a donor unit, and in one case building a custom adapter plate to fit a generic
  replacement gearmotor — but nobody in that thread states the reduction ratio as a
  number (e.g. "30:1"). One poster even links a generic Chinese spur-gearbox gearmotor
  (hsdcmotor.com, model "41JFS3146") as a parts source; `fleet-search read` on that URL
  returned HTTP 200 but no extractable content (JS-rendered product page), so its
  stated ratio (if listed) couldn't be pulled either.
- **No teardown of the cordless Freedom/Freedom LT's drive unit was found at all** — it
  may use a different gearbox design entirely (worm, planetary, or a different spur
  ratio) than the older wired robotic line profiled above, since it's a newer, lighter,
  battery-powered product from the same corporate parent (Fluidra) rather than the
  same part.

**Bottom line for Q2: the installed ratio is real, undocumented, and would have to be
measured off a physical unit** — e.g. count output-shaft RPM at a known battery voltage
with the track removed, or count teeth through the gearbox if it's opened for the regear
anyway (§2's RobotShop thread confirms the gearbox is a small, openable spur-gear stack,
not a sealed potted unit, so counting teeth is mechanically feasible once a unit is in
hand). This research pass had no physical unit to measure and no public source stated
the number — don't let a future pass skip that measurement because this doc implies
a number; it doesn't have one.

## 3. What ratio would give walking-speed indoor movement?

Can't give an exact target ratio (see §2's gap), but the *multiplier* is derivable from
published speeds, independent of the unknown baseline ratio:

**Observed pool-robot track/wheel speeds across brands** (same general weight/size
class as Freedom, underwater, same crawl-speed design intent):
- AquaClean cordless robotic cleaner: **36 ft/min** (aquacal.com spec sheet)
- QOMOTOP cordless cleaner: **~52.5 ft/min** (tools.woot.com spec sheet)
- KIMLUD ABS pool robot: **~36 ft/min** (kimlud.com spec sheet)
- Marino/Swim-Fun pool robot: **~16 m/min ≈ 52.5 ft/min** (swim-fun.com / amazon.se spec
  sheets)
- US Patent 7,849,547 B2 (dual-brush robotic pool cleaner drive design): "a high speed
  drive motor can produce a speed of about **60 feet per minute**... while a
  conventional (lower) speed motor will produce a cleaner speed of about **30 feet per
  minute**" — i.e. the pool-cleaner industry's own engineering literature frames 30-60
  ft/min as the full normal range for this class of robot, which brackets every
  retail spec above.

So **~30-60 ft/min (0.34-0.68 mph)** is a well-supported cross-brand range for
"underwater crawl speed," not a single-model quirk.

**Human walking pace** is conventionally ~3-3.5 mph ≈ **264-308 ft/min**.

**Required speed multiplier ≈ 264÷60 to 308÷30 ≈ 4.4x to 10.3x**, call it **~5-9x**
depending which end of each range you anchor to. Holding the bare motor's unloaded RPM
and the track/wheel diameter fixed, matching that multiplier means **cutting the
installed reduction ratio by roughly the same ~5-9x factor** (e.g., if a physical
measurement per §2 finds the stock ratio is, hypothetically, 50:1, the walking-speed
target would land near 6-10:1 — but that arithmetic only becomes real once §2's
measurement is actually done; don't treat the 50:1 in this sentence as a finding, it's
a placeholder to show the method).

**The torque side is the real risk, and it's the part the ticket's "ratio is wrong"
framing is really pointing at, beyond just speed:**
- Cutting the ratio by ~5-9x cuts available output torque by roughly the same factor at
  a fixed motor (torque scales inversely with speed for a fixed-power gearhead, minus
  efficiency losses).
- Underwater, buoyancy approximately cancels the chassis's weight, so the wheels/tracks
  only fight hydrodynamic drag and light surface contact force — a low-torque motor
  design is *adequate specifically because* the load is nearly weightless.
- On a dry hardwood floor, the full chassis weight (Freedom ≈ 14.5-16.5 lb per the specs
  pulled above) sits on the tracks with no buoyancy offset, and rubber-tread-on-
  hardwood friction is a different (likely higher, and more variable with wax/finish)
  coefficient than rubber-tread-on-wet-pool-plaster/tile. **Re-gearing for 5-9x the
  speed while the actual required torque may be going up, not down, is the mechanism
  by which a naive "just make it faster" regear stalls the motor on carpet transitions
  or thresholds.** This is analysis, not a sourced claim — flagging it because it's
  the first thing whoever does the physical regear should bench-test (measure real
  stall margin at the target ratio under the robot's actual dry weight) before
  committing, not something this research pass can resolve without hardware in hand.

## 4. Are there drop-in replacement gearboxes for the motor housing?

**No confirmed drop-in (same housing, same bolt pattern, different ratio) product was
found for either the older Crouzet-based robotic motor-block or the newer cordless
Freedom's drive unit.** Every repair precedent found is "replace a dead unit with an
equivalent unit," never "re-gear the same housing for a different ratio":

- OEM "motor block" parts (§2) are sold as sealed assemblies per cleaner generation,
  not as a bare gearbox that bolts onto a given motor — there is no OEM catalog entry
  for "same motor, different gearhead ratio."
- The one concrete substitution precedent in the wild (RobotShop thread, §2) is a
  hobbyist **pulling the OEM motor+gearbox out of its housing and bolting in a generic
  small DC gearmotor via a hand-built adapter plate** — a repower, explicitly not a
  drop-in ("I have the same problem and build an adapter plate for new other motors").
  That confirms the *path* is viable (the housing is openable, the output couples to a
  track sprocket that can be re-mounted) but not that any off-the-shelf part bolts in
  without machining.
- For that repower path, the generic small-DC-gearmotor market (the class the
  RobotShop poster's hsdcmotor.com link belongs to) ships the same physical form factor
  across a wide range of stock ratios, which is exactly what's needed to deliberately
  pick a low ratio instead of guessing: **N20-class** (10-12mm body, 3-12V, stock ratios
  from 5:1 up to 1000:1 — e.g. Matha Electronics' N20 line: 30:1 → 500rpm/400 g·cm,
  298:1 → 100rpm/3500 g·cm at 12V) and **37mm-class metal gearmotors** (12V, e.g.
  Greartisan 37mm 12V: 4.5 kg·cm at 100 RPM) are both widely stocked, documented with
  real ratio/speed/stall-torque tables, and sized in the same rough envelope as a small
  pool-cleaner drive unit. None of this is Freedom-specific or verified-to-fit — it's
  the generic parts-market answer to "where would a from-scratch regear source its
  gearmotor," contingent on building the same kind of adapter the RobotShop hobbyist
  built, not a plug-and-play part number.

**Recommendation:** treat this as a repower (new gearmotor + custom adapter + possibly
a new motor driver for the different current draw), not a gearbox swap inside the
stock housing. Get a physical Freedom drive unit in hand first — opening it resolves
§2's missing ratio (count the gear teeth) and also answers the still-open battery-
voltage discrepancy from §1, both of which are needed before picking a replacement
gearmotor's voltage/ratio off a parts catalog instead of guessing.

## Open items

- §2's gearbox ratio is unmeasured — this is the load-bearing unknown for the whole
  ticket; every number in §3 is a multiplier derived without it, not an absolute target.
- §1's battery voltage conflict (29.4V charger output vs. a separate "18V" listing for
  the same ASIN) is unresolved.
- No teardown of the cordless Freedom/Freedom LT specifically exists in any source
  found; everything about the gearbox internals (§2, §4) is inferred from the older,
  wired Polaris robotic line sharing the same corporate drivetrain lineage, not the
  Freedom unit itself — flagged throughout, not stated as Freedom-confirmed.
- Next step is physical, not research: get a Freedom (or a dead donor unit, per the
  RobotShop thread's own recommended cost-saving move) open on a bench and measure
  output-shaft RPM at known battery voltage, no-load, with the track removed; that one
  measurement resolves §2 and makes §3's multiplier arithmetic concrete instead of
  bracketed.
