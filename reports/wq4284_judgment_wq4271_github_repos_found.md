# wq#4284 — verify-pass of wq#4271 (PR #2185) — CONTRADICT, escalated

**Verdict: CONTRADICT.** wq#4271's central claim — "No public community
reverse-engineering work exists for Polaris Freedom or Polaris VRX pool robots
... no GitHub repos ... no leaked/decoded motor-control protocol" — is false.
Two public GitHub repos exist, are live (HTTP 200 as of this verify-pass), and
explicitly document a decoded motor-control protocol for exactly these two
named models.

## The contradiction, checked live

`github.com/galletn/iaqualink` (Home Assistant integration, MIT-adjacent,
HACS-distributed) lists under "Supported Models" in its README (fetched live
via `raw.githubusercontent.com/galletn/iaqualink/master/README.md`):

```
* Polaris VRX iQ+
...
* Polaris Freedom Cordless
```

— the exact two product lines wq#4271 was tasked to survey. The same README's
feature list (already summarized correctly by wq#4271's own sibling ticket,
see below) is "Full Robot Control: Start, stop, return to base, remote
directional movement" plus battery/cleaning-mode/error-state sensors.

`github.com/tekkamanendless/iaqualink` (Go client, MIT) documents the wire
command bytes directly (`raw.githubusercontent.com/tekkamanendless/iaqualink/main/README.md`,
lines 209-212):

```
* `0A1B42`; backward
* `0A1B46`; forward
* `0A1B4C`; turn left
* `0A1B52`; turn right
```

This repo's own "Tested With" list names Polaris P965IQ and Zodiac Voyager RE
4400 iQ, not Freedom/VRX by name — but it is the same iAquaLink cloud
protocol family wq#4271's own doc says Freedom and VRX both use ("Freedom ...
pair with the iAquaLink app"; "VRX ... also iAquaLink-connected"), and
galletn/iaqualink (above) confirms Freedom/VRX specifically as supported
devices on that same protocol.

Both URLs returned HTTP 200 at verify time (2026-10-18); this is not a dead
or hypothetical link.

## Why this isn't a close call

wq#4271's own research doc (`projects/freedom-bot/research/community-re.md`,
lines 7-11) explicitly read and cited the prior sibling ticket **wq#4266**
(commit `c51e453e`) — but only for its file-path convention, not its
substance. wq#4266's deliverable, landed to `main` in the *same session*
one commit before wq#4271's (`projects/freedom-bot/PHASE1-ESP32-MOTOR-PROTOCOL.md`),
already named both repos:

> "The robot's WiFi control path is Zodiac's iAquaLink ecosystem ... Two
> independent open-source clients already document the wire protocol:
> `github.com/tekkamanendless/iaqualink` ... `github.com/galletn/iaqualink`"

ISSUE-LOG.md row 1434 (wq#4266) records the same finding. So the negative
"no GitHub repos found" result in wq#4271 isn't just wrong against the live
GitHub record — it's wrong against this repo's own git history, which the
author read far enough to borrow a path convention from, but not far enough
to check the substantive finding sitting one commit back.

wq#4271 does correctly disclose that `fleet-search github` (authenticated
API) 401'd this session (consistent with ISSUE-LOG rows 1379/1433, a known,
recurring, real credential gap — not fabricated). But that only excuses a
missed *API* sweep; it doesn't excuse missing a finding already sitting in
the same repo's commit log, or a repo (`galletn/iaqualink`) that is
trivially surfaced by plain web search for "polaris freedom home assistant
integration."

## Scope of the defect

- "Bottom line" (lines 13-21): false as written — community RE work exists.
- "Firmware / ESP32 / GitHub" (lines 76-89): "No GitHub repos found" is
  false; should read "no GitHub repos *specific to ESP32 firmware RE*" at
  most — the two repos found are cloud-API/app-protocol RE, not firmware
  disassembly, which is a real and worth-stating distinction wq#4271 could
  have drawn instead of a blanket negative.
- Patent citations (WO2022/029718, WO2022/032103, WO2022/031402, US11129256)
  spot-checked live via Google Patents: all resolve (HTTP 200), not
  fabricated.
- Path-convention finding (lines 7-11, matching wq#4266 commit c51e453e):
  confirmed correct against `git log`.
- Battery spec (lines 91-98): correctly hedged as "not confirmed," not a
  defect.

## Disposition

This is a real, evidence-backed contradiction of a merged PR's core finding,
not a style nit — escalating per archetype 8 to opus-5@high judgment on
wq#4271's disposition (reopen vs. forward-fix the doc to add the two repos
and correct the bottom line). Filed as wq#4291 (deps wq#4271, wq#4284).
