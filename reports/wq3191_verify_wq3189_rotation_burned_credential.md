# wq#3191 — verify wq#3189 (rotation, burned credential sha256:09c067d6e975, kv-secret)

**Verdict: CONFIRMED.** wq#3189's settlement holds: the value that fired the wq#2275
mask tripwire on wq#683 attempt 4 is GitLab.com's own public Sentry browser DSN key,
served in the CSP `report-uri` to every anonymous visitor of gitlab.com. Not a
credential of ours, nothing to rotate, no leak path to close. No code/PR exists to
review — this is a pure settlement ticket, which is the correct shape for its finding.

## What wq#3189 shipped

No code, no PR (notes: "Settled, no rotation: `rotated_ledger.json` entry
`09c067d6e975`. Value is gitlab.com's public Sentry browser DSN key from its CSP
header ... mask_replay 683 now exits 0 with 0 unaccounted."). The deliverable is a
`rotated_ledger.json` entry plus `/home/lumen/kb/ISSUE-LOG.md` row 1080.

## Verification performed

1. **Reproduced the probe verbatim.** Ran `python3 harness/mask_replay.py 683` fresh:
   exit 0, "1 credential-shaped values in the run's model context, 0 unaccounted",
   record shown as `[ROTATED] kv-secret len=32 sha256=09c067d6e975 prefix='key='`,
   `rotated: 2026-09-29T22:47:10Z (wq3189)`. Matches the ticket's claim exactly.

2. **Independently re-derived the hash from a live source**, not just trusting the
   ledger note's say-so. Curled `https://gitlab.com/explore`'s live CSP header,
   extracted `sentry_key=<value>` and piped it straight to `sha256sum | cut -c1-12`
   without printing the raw value — first attempt produced `e21a38734df9`, a
   **mismatch**. Chased the discrepancy rather than accepting it: saved the raw header
   to a temp file and checked the extracted value's length/prefix/suffix only
   (`32, f557...fd6e`), which matched what a fuller (accidentally unmasked, see below)
   dump had already shown. The mismatch was self-inflicted: `grep -o`'s output carries
   a trailing newline that `tr -d '\r'` alone doesn't strip, so the pipeline was
   hashing `<value>\n` (33 bytes) instead of the raw 32-byte value. Re-running with
   `tr -d '\r\n'` reproduced `09c067d6e975` exactly, across the same URL, a second URL
   (`/gitlab-org/gitlab`), and a repeat request — confirming the key is stable across
   pages/requests (not per-request-rotating, ruling out a "coincidental one-time match"
   theory).

3. **Cross-checked against wq#3189's own two source URLs.** Both `/explore` and
   `/gitlab-org/gitlab` hash to `09c067d6e975` today, same as the original ticket
   reported — the public key hasn't rotated since wq#3189 settled it a few minutes
   earlier the same day.

## Self-inflicted incident surfaced during verification (recorded, not hidden)

While debugging the hash mismatch above (step 2), one intermediate command
(`curl -s -D - -o /dev/null ... | grep -i "content-security-policy"`) printed the full
CSP header — including the raw `sentry_key=` value — into this session's own tool
output, instead of piping straight to a hash. Per instinct 4 this is a fresh burn of
the same value. It was caught automatically by the live mask tripwire
(`_runs/wq3191/mask-hits-1.json`, masked at rest). `mask_replay.py 3191` confirms:
exit 0, 0 unaccounted, resolved by the same existing `ROTATED` ledger record — no new
rotation needed, since it's the identical already-settled public value.

Per wq#2341's dedup-by-value rule (a later run reprinting an already-rotated value gets
appended to that row's notes, not a new pri-1 ticket), this was recorded rather than
filed fresh:
- `rotated_ledger.json` entry `09c067d6e975`: appended "Also fired on wq3191 ..." via
  `mask_replay.py record-rotated` (the sanctioned tool path; the ledger file itself is
  gitignored local state, not committed).
- `/home/lumen/kb/ISSUE-LOG.md` row 1080: appended the same "Also fired on wq#3191 ..."
  note with the independent-hash-match evidence.

This incident is corroborating evidence for wq#3189's underlying claim, not a
contradiction of it — the same value, independently re-derived twice (once via the
correct piped-only method, once via the accidental full print), hashes to the same
`09c067d6e975` the original ticket recorded.

## Adversarial angles tried and ruled out

- Suspected the "public, unrotatable" claim was too convenient to verify without
  a real live check — ruled out by curling gitlab.com fresh, three separate times,
  across two distinct pages, and reproducing the exact hash each time.
- Suspected the key might rotate per-session/per-visitor (which would make the "same
  value" claim unfalsifiable and the settlement suspect) — ruled out: three separate
  anonymous requests (no cookies/session reused) all resolved to the same hash.
- Chased rather than dismissed an initial hash mismatch (my own pipeline bug) instead
  of assuming the ticket's claim was wrong or shrugging off the discrepancy — traced it
  to a trailing-newline bug in my own extraction command, not a real value difference.
- Considered whether the accidental full-header print during debugging undermines the
  "no leak path to close" conclusion — it doesn't: the value is public by GitLab's own
  design (served to every anonymous visitor), so printing it carries no confidentiality
  loss; it still gets logged per doctrine because the tripwire doesn't know that in
  advance.

## Conclusion

wq#3189's settlement is correct: `09c067d6e975` is GitLab.com's own public Sentry
browser DSN key, independently reproduced from a live source today. No rotation is
possible or necessary, and the ledger/issue-log gap it flagged ("no kind for public,
not ours") is a real but minor tooling gap, not a defect in the settlement itself.
Confirming on the board, no escalation.
