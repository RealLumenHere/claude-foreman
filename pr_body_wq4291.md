## What

wq#4284 (verify-pass, fresh instance) CONTRADICTed wq#4271's merged PR #2185: that
survey's bottom line said no public community RE, no GitHub repos, and no decoded
motor-control protocol exist for Polaris Freedom or VRX. This ticket judged the
disposition and applied the fix.

**Disposition: FORWARD-FIX, not reopen.** wq#4271 stays closed;
`projects/freedom-bot/research/community-re.md` is corrected in place.

## The contradiction, re-verified here (not taken on the report's word)

Both READMEs fetched live, HTTP 200, 2026-10-18:

- `galletn/iaqualink` README lists `Polaris VRX iQ+` (line 150) and
  `Polaris Freedom Cordless` (line 156) under "Fully Supported"; line 306 says models
  reach that list only when an owner reports a working unit.
- `tekkamanendless/iaqualink` README documents `0A1B42`/`0A1B46`/`0A1B4C`/`0A1B52` for
  backward/forward/left/right (lines 209-212).

Both repos were already in this repo's history at
`projects/freedom-bot/PHASE1-ESP32-MOTOR-PROTOCOL.md:18-44` (wq#4266, commit
`c51e453e`), one commit before wq#4271's — which cited that commit for its file-path
convention but not for its substance.

## Why

The defect is localized (wq#4284 spot-verified the patents, device taxonomy, battery
hedge, and the iFixit/schematic negatives as sound), no new search was needed since the
finding was already in-repo, and the negative narrows rather than reverses: both repos
are cloud/app-protocol clients, so there is still no firmware dump, disassembly, ESP32
project, schematic, or LiFi decode. A reopened survey would re-derive the corrected
claim. Rationale in `reports/wq4291_judgment_disposition_wq4271.md`.

## How

- `projects/freedom-bot/research/community-re.md` — Bottom line replaced (correction
  banner + both repos with README line cites + the surviving hardware-layer negative +
  back-reference to wq#4266's commit); "No GitHub repos found" bullet replaced and the
  `fleet-search github` 401 reframed as a demonstrably non-exhaustive sweep; LiFi
  section retitled "the one *undecoded* protocol surface"; wiring section retitled
  "board-level motor control" and scoped.
- `reports/wq4291_judgment_disposition_wq4271.md` — new; the disposition and its
  reasoning.
- ISSUE-LOG row 1440 records the defect and the doctrine lesson: read a cited sibling
  commit for content, not just formatting, before publishing a negative result.

## Testing

Docs-only; no code paths touched. The corrected claims were verified by live fetch of
both READMEs (HTTP 200 each) and by `grep -n` against the fetched bodies for the model
names and command bytes cited above, plus `git show c51e453e` / a read of
`PHASE1-ESP32-MOTOR-PROTOCOL.md:18-44` for the in-repo prior art. Evidence gate:
`fworker.py verify-evidence --ticket 4291` returns `ok: true`.

## Risk / rollback

Low — a research-doc correction and a new report file, no executable code. Rollback is
`git revert` of this commit, which restores the prior (false) bottom line.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
