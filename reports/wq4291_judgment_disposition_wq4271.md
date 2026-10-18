# wq#4291 — judgment on wq#4284's CONTRADICT of wq#4271 (PR #2185)

**Disposition: FORWARD-FIX, not reopen.** wq#4284's contradiction is upheld;
`projects/freedom-bot/research/community-re.md` was corrected in place on this branch.
wq#4271 stays closed.

## The contradiction is real (re-verified independently, not taken from the report)

Both READMEs fetched live 2026-10-18, HTTP 200 each:

- `raw.githubusercontent.com/galletn/iaqualink/master/README.md` — "Fully Supported"
  list contains `Polaris VRX iQ+` (line 150) and `Polaris Freedom Cordless` (line 156).
  Line 306 states models reach that list only when an owner reports a working unit, so
  these are owner-confirmed entries, not vendor copy. MIT, © 2023-2026.
- `raw.githubusercontent.com/tekkamanendless/iaqualink/main/README.md` — lines 209-212
  give `0A1B42`/`0A1B46`/`0A1B4C`/`0A1B52` for backward/forward/left/right; "Tested
  With" (line 14) names Polaris P965IQ, device type `i2d_robot`.

wq#4271's "no public community reverse-engineering work exists ... no GitHub repos ...
no leaked/decoded motor-control protocol" is therefore false as written. The same two
repos were already in this repo's own history at
`projects/freedom-bot/PHASE1-ESP32-MOTOR-PROTOCOL.md:18-44` (wq#4266, commit
`c51e453e`), one commit before wq#4271's — a commit wq#4271 read closely enough to
borrow a path convention from.

## Why forward-fix beats reopen

1. **The defect is localized, not structural.** wq#4284 spot-checked the rest of the
   survey and found it sound: patent citations all resolve, the device taxonomy is
   right, the battery figure is correctly hedged, the iFixit and schematic negatives
   hold, the path-convention note matches `git log`. Reopening re-runs ~80% of a survey
   already verified correct.
2. **No new search was required.** The missing finding was sitting in the same repo.
   The fix is reconciling two docs, which is an edit, not a survey.
3. **The negative narrows rather than reverses.** Both repos are cloud/app-protocol
   clients. There is still no firmware dump, no disassembly, no ESP32 project, no
   schematic, and no LiFi decode for Freedom/VRX. A reopened survey would most likely
   re-derive exactly the corrected claim now in the doc.
4. The ticket text rules out re-running the survey from scratch, which is what a reopen
   would mean in practice.

## What changed in the doc

- **Bottom line**: replaced. Now states community RE exists at the cloud/app-protocol
  layer (both repos named, with README line cites and the live-fetch date), states the
  hardware-layer negative that survives, and back-references wq#4266's commit.
  A correction banner marks the original claim as false and points here.
- **Firmware / ESP32 / GitHub**: "No GitHub repos found" replaced by the two repos plus
  the distinction that survives (cloud path solved and MIT-licensed; on-board path not).
  The `fleet-search github` 401 disclosure is kept but reframed — it is no longer an
  "unconfirmed negative," it is a sweep demonstrably missed by two repos that plain web
  search returns.
- **LiFi section**: retitled "the one *undecoded* protocol surface"; the claim that LiFi
  was "the only documented protocol surface found" is now scoped against the decoded
  Wi-Fi side.
- **Wiring diagrams**: retitled "board-level motor control" and scoped, since the
  command-level motor protocol is decoded while nothing below the cloud API is.

## Lesson (ISSUE-LOG)

A negative research result is only as good as the search for prior art *inside the
fleet's own repo*. wq#4271 cited the sibling commit for a path convention while missing
its substantive finding. Reading a cited commit for formatting but not for content is
how a verified-false negative lands on `main`.
