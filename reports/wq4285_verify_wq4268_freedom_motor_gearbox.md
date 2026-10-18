# wq#4285 — verify-pass of wq#4268 (PR#2186)

Verdict: **CONFIRM**

Author: claude-sonnet-5 (fworker-wq4268). Verifier: claude-sonnet-5@high (this
ticket), fresh instance, no escalation triggered.

PR: http://100.121.17.5:3000/lumen/claude-foreman/pulls/2186 — merged as
`fb32b84ac7`, single new file `research/WQ4268_FREEDOM_MOTOR_GEARBOX_RESEARCH.md`
(239 lines, docs-only, no code).

## What the ticket asked

Desk research on the Freedom (Polaris/Zodiac pool robot) drivetrain: (1) what
motor(s), (2) installed gearbox ratio, (3) what ratio gives walking-speed
indoor movement, (4) whether a drop-in replacement gearbox exists.

## How I tried to break it

Research deliverable, not code — no runnable probe. The adversarial pass is:
reproduce the cited sources independently, check the arithmetic, check the
`kb/ISSUE-LOG.md` citations, and try to find something the author missed that
would change a conclusion.

1. **Reproduced load-bearing citations independently** via `fleet-search`:
   - `fleet-search read` on the Google Patents page for US7849547B2 — confirmed
     verbatim: "...a high speed drive motor can produce a speed of about 60
     feet per minute in a belt-driven pool cleaner while a conventional
     (lower) speed motor will produce a cleaner speed of about 30 feet per
     minute..." Matches §3's quote exactly, patent title ("Directional control
     for dual brush robotic pool cleaners") matches.
   - `fleet-search web` for AquaClean spec → `aquacal.com/product/aquaclean/`
     lists `Moving Speed: 36ft/min` verbatim, matching §3's cited figure.
   - `fleet-search web` for the Crouzet part number → "82 720 013" turns up
     independently in a Crouzet micromotor datasheet-archive listing (24V PWM
     gear motor) — the part number is real, not invented, consistent with the
     doc's own "medium confidence, single-source forum report" hedge on the
     RPM figure attached to it.
   - `fleet-search web`/`read` on the troublefreepool thread
     (`polaris-9300-failed-troubleshooting-motor-box.143771`) — thread exists,
     title and URL match exactly what §1 cites (direct fetch hit a Cloudflare
     challenge page, but the search index's own snippet confirms the thread
     and its subject matter).
   - Arithmetic check: 3–3.5 mph → 264–308 ft/min (exact); 264/60=4.4,
     308/30≈10.3, so "4.4x to 10.3x" is correct, and the "~5-9x" callout is an
     honest inward rounding, not a misrepresentation. 7S Li-ion full-charge
     7×4.2V=29.4V and nominal 7×3.7V=25.9V, 5S nominal 5×3.7V=18.5V — the §1
     battery-chemistry math is correct.

2. **Checked the `kb/ISSUE-LOG.md` citations rather than trusting them**:
   `grep -n "^| 325 |"` and `grep -n "^| 717 |"` both resolve to rows that say
   what the doc claims they say (row 325 = wq#1717 GitHub-401-no-token, OPEN;
   row 717 = wq#2601 SearXNG-engines-suspended/no-`BRAVE_API_KEY`) — not
   fabricated references.

3. **Tried to find something that would overturn a conclusion**: searched for
   a Freedom-specific primary source the doc might have missed. Found one —
   see "Finding, non-blocking" below — but it does not contradict anything the
   doc actually states; the doc's hedges (battery-voltage conflict "unresolved",
   "no teardown... found") remain accurate even with this source in hand.

4. **Checked epistemic hygiene**: every number that depends on an unmeasured
   installed ratio is explicitly marked as derived/placeholder (§3's "don't
   treat the 50:1 in this sentence as a finding" is unusually careful for a
   research doc). The one genuine dead end (§2, the ratio itself) is stated as
   a dead end rather than papered over with a guess, which is the correct call
   per the no-guessing doctrine — I could not find a public source stating the
   ratio either.

## Finding, non-blocking

An official Fluidra/Zodiac Owner's Manual for the actual cordless Freedom/
Freedom SC product exists on the manufacturer's own CDN
(`cdn.fluidrausa.com/.../polaris/cleaners/h0748900.pdf` and a sibling
`...-freedom-sc-owners-manual.pdf`), surfaced by a single `fleet-search web`
query the author doc never ran (something like "Freedom SC owners manual
especificaciones"). This is a real, product-specific (not sibling-proxy)
primary source the research pass missed. I could extract only the charger's
**AC input** spec from it via search-engine snippets (100-240V, 0.6A max;
direct PDF fetch returns the file but `fleet-search read` can't extract text
from it — likely image-heavy) — I did not get battery DC voltage, motor, or
gearbox figures out of it, so I can't say it actually resolves §1's 29.4V-vs-
18V conflict or §2's missing ratio. It doesn't contradict any claim in the
doc — the doc's "unresolved"/"not found" hedges stay true even accounting for
this manual. But it's a real gap in the prior-art search step (doctrine: "the
world has often already solved it — one search away"), and whoever does the
physical bench pass that this doc recommends should pull this manual first;
it may have a spec table page OCR/manual-reading would reach where automated
extraction failed. Not filing an ISSUE-LOG row for this — it's a research
completeness note, not a tool/fleet defect.

## Conclusion

No contradiction found. Every independently-checked citation reproduces
exactly, the arithmetic is correct, the `kb/ISSUE-LOG.md` references are real,
and the one genuine research dead end (installed gearbox ratio) is honestly
reported as a dead end rather than guessed. One non-blocking completeness gap
(missed OEM manual) noted for follow-up but it doesn't change the doc's
conclusions. Confirming on the board.
