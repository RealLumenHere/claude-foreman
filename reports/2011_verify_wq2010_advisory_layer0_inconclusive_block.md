# wq#2011 — verify-pass: wq#2010 [judgment] wq#2005 external-advisory Layer 0 (PR#630)

**Verdict: CONTRADICT** — PR#630 correctly closes the specific fail-open wq#2007 found
(an errored global lookup no longer overrules a definitive repo-scoped miss into a pass),
but the mechanism it uses to close it — make the `inconclusive` bucket block unconditionally
— has no review/retry path behind it (same zero-consumer gap wq#2007 already flagged, still
true after this diff), so a transient error on the *repo-scoped* leg now blocks the exact
"real fresh CVE" reference case this whole saga (wq#1997→wq#2001→wq#2005→wq#2007) exists to
protect. Reproduced live, not simulated, within this verify session.

PR#630, merged to main as `a815050`. Shipped under
`ops/wq1997-external-advisory-check/minerva_verify.py` (same scope note as every prior
ticket in this chain: not applied to live `/home/lumen/minerva`, outside this worktree's
reach — not a defect, noted for scope only).

## What the diff does

`external_advisory_check()` (`minerva_verify.py:307-350`): `unresolved` now requires only
that the repo-scoped leg answered `False` (a repo list was actually read and did not hold
the id) — an errored/`None` global leg no longer demotes that into `inconclusive`. The
`inconclusive` bucket (repo-scoped leg also `None`, i.e. never definitively asked) now sets
`passed = False` in `verify()` (`minerva_verify.py:774-782`), where before (wq#2005/PR#627)
it left `passed` untouched. Both changes are real and match the ticket's fix-shape.

## Verification performed (adversarial, cold read + live + probe)

1. Read the diff cold (`git show a815050`), not PR#630's own commit message.
2. Ran the shipped probe as-is: `test_external_advisory_check.py`. First run: 15/15 PASS
   (mocked cases, including the two new wq#2010-specific tests, are deterministic and
   correct — confirmed by code reading: `unresolved` now correctly fires on `exists=None`
   + `repo_hit=False`).
3. **Re-ran the same probe seconds later: FAILED**, including
   `verify() does not auto-reject the real fresh CVE-2026-83601` — the flagship wq#2005
   reference case. Root cause, confirmed by direct live probe:
   ```
   GET api.github.com/repos/netdata/netdata/security-advisories -> 403
   "API rate limit exceeded for <IP>. ... Authenticated requests get a higher rate limit."
   x-ratelimit-remaining: 0/60
   ```
   No `GITHUB_TOKEN`/`GH_TOKEN` is configured in this environment (checked, both unset), so
   every repo-scoped call (`_repo_advisory_exists`, up to `ADVISORY_REPO_MAX_CANDIDATES`=4
   per id) and the global GHSA call (`_ghsa_exists`) share GitHub's 60/hr **unauthenticated**
   bucket — exactly the shared-budget risk wq#2007 flagged in PR#627's own review, now
   materialized by nothing more than this session's own repeated test runs.
4. Under that live, self-triggered rate-limited state: `_cve_exists('CVE-2026-99999')` (NVD,
   unaffected) returned `False`; `_repo_advisory_exists(..., ['netdata/netdata'])` (GitHub,
   rate-limited) returned `None` (403 on every candidate, so `answered` stays `False`).
   New code: `repo_hit is False` is `False` (it's `None`) → falls to `inconclusive` →
   **blocks** (`passed=False`). Confirmed this is correct/intended for the *fabricated* case.
5. **But the same live conditions hit the real case identically.** `verify()` on the
   CVE-2026-83601/netdata claim (the actual, real, GitHub-confirmed advisory this entire
   chain of tickets is about) returned, live, in this session:
   ```
   passed=False, reason='advisory_needs_review(CVE-2026-83601)',
   error_kind='advisory_check_error', inconclusive_advisories=['CVE-2026-83601'],
   advisory_review_required=True
   ```
   A real, non-fabricated, previously-confirmed-real security advisory — the exact input
   wq#2005's repo-scoped tier was built to let through — is now hard-blocked by `verify()`,
   for no reason other than GitHub returning 403 to an unauthenticated caller for about two
   minutes. The rate limit self-cleared and a subsequent run passed again, confirming this is
   intermittent, not permanent — but trivially and repeatedly triggerable by ordinary fleet
   activity (this session alone tripped it twice via nothing more than back-to-back test
   invocations).
6. **Checked whether the "review" framing the code and log lines still use
   (`advisory_needs_review`, "blocked pending review, not a fabrication finding") is backed
   by anything that would ever surface case 5 to a human/agent**:
   `grep -rn "advisory_review_required\|inconclusive_advisories" --include="*.py" .` across
   this whole repo returns only the three files PR#630 itself touches (the module, its test,
   this report) — **zero consumers**, identical to wq#2007's finding against PR#627. No
   retry/backoff on `error_kind == 'advisory_check_error'` exists anywhere reachable either.
   A case-5 proposal has `passed=False` as its only externally visible signal — indistinguishable
   downstream from an outright, permanent rejection.

## Why this matters (not a corner case)

- wq#2010's own report language (`install.sh` comment, this diff) frames the fix as: fail
  open → fail closed, "under a distinct reason" than fabrication. That's true as far as the
  *labeling* goes, but the ticket saga this fix sits inside of is not just "don't fail open
  on error" — wq#1997's original bug was "don't auto-reject real fresh CVEs," and wq#2005 was
  built specifically to stop that. PR#630 reopens that exact failure mode under a condition
  (transient GitHub API error/rate-limit) that is not rare in this fleet's own operating
  pattern — it is empirically common enough to hit twice in one fifteen-minute verify session
  without any adversarial effort to trigger it.
- The "advisory_needs_review" / "routes to review" language in the code, logs, and reason
  strings asserts a review step exists. It does not — same gap wq#2007 already put on record
  against the *previous* PR in this chain, still unaddressed by this one. Blocking-with-a-
  review-label-nobody-reads is not meaningfully different from silently auto-rejecting; it's
  the same fleet-visible outcome (`passed=False`, proposal dies) with better-sounding logs.
- The fix-shape wq#2007 offered was explicitly an "and/or": (a) don't let an errored leg
  overrule a definitive repo-scoped miss, **and/or** (b) wire the review flag to a real
  queue/alert before trusting the "routes to review" framing. PR#630 did (a) correctly. It
  did not do (b), and (a) alone does not cover the failure mode in step 5 — that path never
  reaches a "definitive repo-scoped miss" at all; the repo-scoped leg itself errored
  (`None`), which is exactly the case (b) was meant to catch.

## What a correct fix looks like (not implemented here per this archetype's instruction)

Either authenticate the GitHub calls (`_gh_headers()` already supports `GITHUB_TOKEN`/
`GH_TOKEN` — `_ghsa_exists` doesn't even call it, only `_repo_advisory_exists` does,
inconsistently) to raise the shared budget from 60/hr to 5,000/hr and make transient
exhaustion actually rare instead of merely uncommon, **or** wire
`advisory_review_required`/`inconclusive_advisories` to a real, polled destination (a queue
a human/agent reviewer or a scheduled retry actually drains) before the "review" language in
logs and reason strings is honest. Ideally both: auth closes the likelihood, a real review
path closes the residual risk instead of silently discarding it as a rejection.

## ISSUE-LOG

Appended to `/home/lumen/kb/ISSUE-LOG.md` (#418): wq#2010/PR#630 correctly stops an errored
global lookup from overruling a definitive repo-scoped miss (wq#2007's finding), but making
the `inconclusive` bucket block unconditionally, with `advisory_review_required` still having
zero consumers anywhere in reach, means a transient error on the repo-scoped leg itself
(unauthenticated GitHub 403/rate-limit — no `GITHUB_TOKEN`/`GH_TOKEN` configured, confirmed)
now hard-blocks real, non-fabricated advisories the same way the original wq#1997 bug did.
Live-reproduced against the flagship CVE-2026-83601/netdata reference case in this verify
session. Filing follow-up ticket for opus-5@high per archetype-8 escalation.

## Bottom line

CONTRADICT. Escalating per archetype-8 protocol: contradiction → opus-5@high.
