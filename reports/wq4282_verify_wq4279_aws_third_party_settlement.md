# wq#4282: verify-pass of wq#4279 (PR #2187) — CONFIRM

Archetype 8 adversarial verify-pass. Author: claude-opus-5-5. Verifier: claude-sonnet-5
(fresh instance, this ticket). Verdict: **CONFIRM** — no contradiction found, no
escalation to judgment.

## Claim under test
wq#4279 settled a pri-1 AWS-credential rotation alarm (sha256:d7178a2321e9, prefix
`AKIA`, len 16) as a third-party public value — not a fleet credential — and recorded
it in `rotated_ledger.json` with a `NOT ROTATED` note instead of rotating anything.

## What I checked (independent of the author's report)

1. **Raw evidence, not the report's paraphrase.** Read
   `_runs/wq4267/mask-hits-1.json` and `_runs/wq4268/mask-hits-1.json` directly. Both
   carry the identical `value_sha256_12: d7178a2321e9`, prefix `AKIA`, len 16, and an
   identical masked context: an Amazon product URL
   (`...&linkCode=osi&...&ascsubtag=...&SubscriptionId=AKIAMASKED#1`) next to a
   "Polaris Freedom Cordless Robotic Pool Cleaner" snippet — a `fleet-search web` hit
   from wq#4266/4267/4268's pool-robot prior-art research, unrelated to any fleet
   system.
2. **Mechanism check, not just trust.** `fleet-search web "Amazon SubscriptionId AKIA
   affiliate link public key not secret"` returned corroborating hits (e.g. a
   WordPress Amazon-affiliate-plugin support thread) confirming Amazon's
   PA-API/affiliate widget links embed an AWS Access Key ID in a `SubscriptionId`/
   `AWSAccessKeyId`-style query param for unsigned, client-side product rendering —
   the same shape class as ISSUE-LOG row 1080's public GitLab Sentry DSN key in a CSP
   header. The claim that this is a publisher-side, by-design-public value is
   independently plausible, not just asserted.
3. **Reproduced the replay myself.**
   `python3 -I harness/mask_replay.py 4267` → exit 0, `[ROTATED] aws ... d7178a2321e9
   ... rotated: 2026-10-18T21:05:44Z (wq4279)`, 0 unaccounted.
   `python3 -I harness/mask_replay.py 4268` → exit 0, same record, 0 unaccounted.
   Both match the report's claimed post-settlement state exactly.
4. **Ledger entry, read directly (not via the report).** `rotated_ledger.json`'s last
   row: `value_sha256_12: d7178a2321e9`, `ticket: wq4279`, note text matches the PR's
   claimed note verbatim, consistent with the precedent row (`09c067d6e975`, wq#3189,
   same "NOT ROTATED - third-party public" pattern).
5. **Tried to break "not ours."** Grepped the foreman tree for the hash
   (`d7178a2321e9`) outside the report/ledger — zero hits, i.e. the value never
   appears anywhere in tracked fleet config, confirming there is no fleet-side AWS key
   this could actually be. No contradicting evidence found.
6. **Follow-through checked.** wq#4281 (strip `SubscriptionId` from `fleet-search`
   output to stop recurrence) exists, correctly scoped, `claimed` — not a dangling
   promise. wq#4279 itself is `done` with notes matching the report and PR link
   (http://100.121.17.5:3000/lumen/claude-foreman/pulls/2187). ISSUE-LOG row 1437
   present and consistent with all of the above.

## Attempted breaks that did not land
- Considered whether the key could coincidentally belong to the fleet: ruled out by
  (5) above — no tracked reference anywhere, and the context is a third-party retail
  product page unrelated to any Lumen AWS usage.
- Considered whether `record-rotated`'s note conflates "rotated" with "not rotated"
  in a way that could hide a real burn: the ledger schema has no boolean "rotated"
  field, only `rotated_at`/`note`; the note text is explicit (`NOT ROTATED - ...`) and
  `mask_replay.py`'s own `[ROTATED]` label just means "this sha is settled in the
  ledger," not a claim that a secret was spun. This is the same pattern already used
  and accepted for ISSUE-LOG row 1080 — not a new shortcut invented for this ticket.
- Considered whether the hash was re-derived correctly (no second printing of a raw
  key): the report describes a regex extracted straight into sha256 without an
  intermediate print; I could not re-run that exact command (the source
  `/tmp/fworker-wq4268/search1.json` is outside this ticket's granted directories and
  is another worker's ephemeral tmp), but the mask-hits files from two independent
  runs (wq#4267's live transcript, wq#4268's file read) already agree on the same
  12-hex value and same context, which is the cross-check that matters here.

## Verdict
CONFIRM. No re-implementation performed, per instructions.
