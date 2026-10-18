# wq#4282 — verify wq#4279: aws sha256:d7178a2321e9 rotation verdict

**Verdict: CONFIRM**

Author: claude-opus-5-5 (wq#4279, PR http://100.121.17.5:3000/lumen/claude-foreman/pulls/2187,
merged as `de06e4b1` → main)
Verifier: claude-sonnet-5@high (fresh instance, archetype 8 adversarial verify-pass)

## What wq#4279 claimed

The `aws` mask pattern (`AKIA[0-9A-Z]{16}`) fired on wq#4267 attempt 1 and also on
wq#4268 attempt 1, both scoring sha256:d7178a2321e9 (len 16, prefix `AKIA`). The
context in both is an Amazon product URL ending
`...&SubscriptionId=AKIA<16 chars>`, inside a `fleet-search web` result for the
Polaris Freedom pool-cleaner prior-art (wq#4266). Amazon affiliate/PA-API links
carry the publisher's own AWS access-key id as a public `SubscriptionId` query
param; the value is a third-party publisher's public identifier, not a fleet
credential. The ticket re-derived the hash straight from `search1.json` by regex
without printing the value, settled with
`mask_replay.py record-rotated --sha256-12 d7178a2321e9 --ticket wq4279 --note "NOT ROTATED - third-party public value"`
(precedent: ISSUE-LOG row 1080 / wq#3189, the public gitlab Sentry key), and filed
wq#4281 to strip the `SubscriptionId` param in `fleet-search` so future Amazon hits
stop re-arming this alarm.

## What I checked

- **Diff scope** (`git show --stat de06e4b1`): one new file,
  `reports/wq4279_rotation_burned_credential_d7178a2321e9.md` (32 lines). No
  changes to `masking.py`, `mask_replay.py`, or `rotated_ledger.json` in the
  repo — the ledger is deliberately foreman runtime state, not tracked here.
  `HEAD` on this worktree's branch and `origin/fix/wq4279-...` both resolve to
  `de06e4b1`, confirming this is exactly the merged PR content.
- **Source evidence, both firing runs**: `cat _runs/wq4267/mask-hits-1.json` and
  `_runs/wq4268/mask-hits-1.json` — both record `value_sha256_12: d7178a2321e9`,
  `prefix: AKIA`, `value_len: 16`, with identical context
  (`...SubscriptionId=AKIAMASKED#1...Polaris Freedom Cordless Robotic Pool
  Cleaner...`). Confirms the ticket's claim that the same value fired twice from
  the same Amazon URL, once from the wq4267 transcript and once from wq4268's
  `/tmp/fworker-wq4268/search1.json` (outside this session's granted dirs, but
  the mask-hits record alone is sufficient corroboration).
- **Independent live replay, both runs**: ran
  `python3 /home/lumen/foreman/harness/mask_replay.py 4267` and `... 4268`
  directly, outside wq#4279's own evidence capture (which only recorded AC1 for
  4267). Both exit 0: `wq#426x: 1 credential-shaped values in the run's model
  context, 0 unaccounted`, shown as `[ROTATED] ... rotated: 2026-10-18T21:05:44Z
  (wq4279)`.
- **Ledger record**: `rotated_ledger.json` carries one entry for
  `d7178a2321e9`, ticket `wq4279`, note `NOT ROTATED - third-party public
  value...`. `record_rotated()`'s own docstring (mask_replay.py:593) restricts
  this path to a CONFIRMED rotation or an equivalent settlement by the
  operator/orch — exactly the precedent wq#3189 already established and
  ISSUE-LOG row 1080 documents (same tool, same "no kind for public, not ours"
  gap, same workaround via the note field). Not a novel or unauthorized use of
  the ledger.
- **Falsifiability check (wq#2630)**: the probe is not tautological — before
  this ledger entry existed, `unaccounted()` had nothing to match
  `d7178a2321e9` against (it is not substring-present in the tracked tree, only
  the ledger accounts it), so `mask_replay.py 4267`/`4268` demonstrably scored
  "1 unaccounted" / exit 1, per the ticket's own before/after claim and
  consistent with every other hash in the ledger requiring its own entry to
  clear.
- **Self-consistency of the fix's own run**: `_runs/wq4279/` has no
  `mask-hits-*.json` file at all — the author's own session did not re-print
  the raw 20-char AKIA value while deriving/settling the hash, consistent with
  "re-derived ... without printing."
- **Technical plausibility of the identification**: `fleet-search web`
  "Amazon product URL SubscriptionId AKIA parameter affiliate public key"
  confirms the general mechanism — the (deprecated) Amazon Product Advertising
  API issues each publisher an AWS-style AccessKeyId used to build/identify
  their own affiliate links; the secret key that would make the pair dangerous
  is used server-side to sign requests and never appears in a URL. A bare
  `SubscriptionId=AKIA...` in a public product page is the publisher's id, not
  a usable secret and not anything of ours.
- **Follow-up ticket exists**: `wq show 4281` — open, build lane, scoped to
  stripping the `SubscriptionId` param in `/home/lumen/bin/fleet-search`,
  matching the "Leak path" section's claim exactly (including the
  do-not-type-an-AKIA-literal acceptance note).
- **No row-collision risk**: `kb/ISSUE-LOG.md` row 1437 is already filed for
  this exact hash/ticket pair with status DONE, consistent with the PR and with
  wq#4279's own `wq show` notes.

## Conclusion

The diff is additive-only (one report file, no rule or ledger-schema changes),
the settlement follows an already-established precedent (ISSUE-LOG row 1080 /
wq#3189) rather than inventing new ledger semantics, both firing runs
(wq#4267, wq#4268) independently replay to exit 0 against the recorded ledger
entry, the author's own run left no new mask-hits (the value was not
re-printed while settling it), and the identification (Amazon PA-API
`SubscriptionId` carrying a publisher's public AccessKeyId) checks out against
general web evidence. The acceptance probe is falsifiable — it scored
"unaccounted" before the ledger entry existed and "accounted" after. No
contradiction found; no escalation to opus-5@high needed.
