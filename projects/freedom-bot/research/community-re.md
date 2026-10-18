# Polaris Freedom / VRX Community RE — Survey (wq#4271)

Date: 2026-10-18
Method: `/home/lumen/bin/fleet-search` (SearXNG with DeepSeek-Search fallback — general
SearXNG engines were suspended/CAPTCHA'd for the whole session, see Tooling Notes),
plus `fleet-search reddit`, `fleet-search hn`, `fleet-search github`.
Filed at `projects/freedom-bot/research/community-re.md`, the repo-relative path used
by the prior freedom-bot ticket (wq#4266, commit `c51e453e`, which landed
`projects/freedom-bot/PHASE1-ESP32-MOTOR-PROTOCOL.md`) — not the literal absolute path
`/home/lumen/projects/freedom-bot/...` the ticket text suggested, which is outside this
worktree's sandbox and isn't where freedom-bot docs actually live.

## Bottom line

**No public community reverse-engineering work exists for Polaris Freedom or Polaris
VRX pool robots** — no teardown guides, no firmware dumps, no ESP32-specific writeups,
no GitHub repos, no wiring diagrams, no leaked/decoded motor-control protocol. This was
checked across general web search (w/ DeepSeek-Search fallback), Reddit, Hacker News,
and GitHub (API search itself failed — see Tooling Notes). This is itself the useful
result: a freedom-bot integration has to start from first-party sources (manuals,
patents, physical teardown) rather than reusing existing community RE.

## What the devices actually are

- **Polaris Freedom** / **Freedom Plus** / **Freedom LT**: cordless robotic pool
  cleaners made by Polaris, a Fluidra/Zodiac Pool Systems brand (not the powersports
  company — "Polaris" pool products and Polaris snowmobiles/ATVs/PWCs are unrelated
  brands that happen to share a name). Freedom and Freedom Plus pair with the
  **iAquaLink** app; Freedom LT is the first model on Fluidra's newer **Fluidra Pool**
  app. All run on lithium-ion batteries, 4 cleaning modes (floor / floor+walls+waterline
  / waterline-only / SMART Cycle), and self-park at the waterline for retrieval
  (Easy Lift System: expels water on lift-out).
- **Polaris VRX** / **VRX iQ+**: corded (not cordless), pressure/4WD robotic pool
  cleaners, also iAquaLink-connected, with Wi-Fi + in-app remote control and "Sensor
  Nav System" pool-adaptive navigation.
- Both lines are marketed by the same Polaris/Fluidra organization as the corded
  **Polaris 280/360/380** pressure-side sweepers, which are much older, dumber (no
  electronics — venturi/booster-pump driven) and have an established teardown/tune-up
  kit community (iFixit-style reviews, YouTube tune-up videos) — but that's mechanical
  maintenance, not RE of any onboard compute/comms.

## LiFi remote control (Freedom Plus) — the one real protocol lead

Freedom Plus's handheld remote talks to the submerged robot via **LiFi** (modulated
light pulses through the water) rather than RF, because RF does not propagate usefully
underwater. This is the only documented "protocol" surface found, and it is vendor
marketing copy, not a community decode:
- Functions confirmed from marketing/support copy: start/stop, steer, "call the cleaner
  up" (surface summon) via the Easy Lift System.
- No example of anyone having decoded the LiFi modulation scheme, sniffed it with a
  photodiode, or published a protocol doc.

### Related Zodiac/Polaris patent family (first-party, not community, but protocol-relevant)

Found via patent-office search aggregators, these describe the underwater
communication and cleaner-control concepts Polaris/Zodiac actually ship or planned:
- **WO2022/029718** — "Concepts and methods for communicating with automatic swimming
  pool cleaners" (Zodiac Pool Care Europe, filed 2021-08-06, priority 2020-08-06,
  appl. 63/061,910).
- **WO2022/032103** — "Concepts and methods for underwater communication with equipment
  such as automatic swimming pool cleaners" (Zodiac Pool Systems LLC, same filing date,
  appl. 63/061,916; inventor Phillip Newman). Classified under G01S 3/808 and G01S 5/18
  (direction/position-finding by sound/signal) — suggests acoustic or light positioning,
  not just one-way command.
- **WO2022/031402** — "Bypass mechanism for automatic pool cleaners" (Zodiac Pool
  Systems LLC, appl. 63/061,484) — mechanical, not comms.
- **US11129256** — cleaner/filter patent citing Jandy "AquaLink 8" and "iAquaLink 2.0
  Web Connect" installation manuals as prior art — useful pointer to the first-party
  manuals if the actual iAquaLink wire protocol (Wi-Fi/app side, as opposed to the LiFi
  underwater side) ever needs decoding.

These are worth pulling in full (Google Patents / WIPO PatentScope) if the project needs
the claimed mechanism rather than marketing description — not done here, link-level
survey only.

## Firmware / ESP32 / GitHub

- **No GitHub repos found** referencing Polaris Freedom, Polaris VRX, or any
  ESP32/firmware work tied to either. `fleet-search github` (authenticated GitHub API)
  itself failed with a 401 — no usable token in this environment (see Tooling Notes) —
  so this is a web-search-only negative result, not a confirmed-empty GitHub search;
  it should be re-run once a GitHub token is available.
- No hits for "polaris freedom esp32", "polaris vrx esp32 OR firmware OR teardown", or
  general firmware-dump-style queries. The ESP32-related hits that did come back
  (e.g. a 2025-12-27 congress talk on reverse-engineering the ESP32 Bluetooth stack)
  are generic ESP32 RE work with no connection to pool robots.
- No iFixit teardown page for Freedom or VRX (iFixit results returned were customer
  reviews of a mechanical *tune-up kit* for the much older, non-electronic Polaris 360,
  not a teardown of Freedom/VRX electronics).

## Battery

No Polaris-specific cell/BMS spec was found (no teardown has published one). For
context, generic robotic-pool-cleaner battery OEM listings (not confirmed as the part
used in Freedom/VRX) spec **18650 2S2P, 7.4V / 4400mAh**, with standard
overcharge/over-discharge/overcurrent/thermal protection and SMBus/I2C/UART BMS
communication — representative of the battery class these robots use, not a verified
Freedom/VRX teardown figure.

## Wiring diagrams / motor control

None found. No schematic, no motor-driver board photos, no PCB teardown for Freedom or
VRX turned up in web, Reddit, or HN search.

## "iRobot competitor protocol" keyword

This ticket's keyword list includes "irobot competitor protocol." Search under this
term surfaced the iRobot vs. SharkNinja ITC patent litigation (floor-cleaning robots,
unrelated hardware family to pool cleaners) — not a Polaris-specific result. No
pool-robot-vs-iRobot protocol comparison exists in the public record; iRobot does not
compete in the pool-cleaner category, so this keyword had no pool-robot-relevant yield.

## Tooling notes (for whoever picks this back up)

1. **GitHub search unauthenticated**: `fleet-search github "<query>"` returned
   `github 401 — no usable token (credential helper checked identity-map,
   github-frank.token, agent/.env and found none valid)`. Needs a token configured
   before a real GitHub sweep for Polaris/pool-cleaner RE repos can be considered done.
2. **SearXNG general engines down**: every `fleet-search web` call in this session
   returned `unresponsive_engines` for brave, duckduckgo, google cse, and startpage
   (suspended/CAPTCHA'd), falling back to the keyed DeepSeek Search engine. Results are
   usable but the engine diversity SearXNG is meant to provide was not available —
   re-running later with SearXNG healthy could surface things DeepSeek Search's ranking
   missed.
3. **Ticket path is repo-relative, not absolute**: the ticket text's
   `/home/lumen/projects/freedom-bot/research/community-re.md` reads as an absolute
   filesystem path, but freedom-bot artifacts actually live at
   `projects/freedom-bot/...` inside this `claude-foreman` repo/worktree (confirmed via
   wq#4266's commit). Future freedom-bot tickets should phrase the target as a
   repo-relative path to avoid this ambiguity.
