# wq#2007 — verify-pass: wq#2005 [judgment] wq#1997 external-advisory Layer 0 (PR#627)

**Verdict: CONTRADICT** — PR#627 fixes the false-positive wq#2001 found (fresh real CVEs
wrongly hard-blocked) but in doing so removes fail-closed behavior on genuine API errors,
reopening the exact wq#1512 failure class (a fabricated CVE that scores citation=1.0/
judge=1.0 sails through) under an ordinary, non-corner-case condition: the external
advisory APIs being unreachable or rate-limited.

PR#627, merged to main as `589707f`. Shipped under
`ops/wq1997-external-advisory-check/minerva_verify.py` (same constraint as wq#1997/wq#2001:
not applied to live `/home/lumen/minerva`, outside this worktree's reach — not a defect,
noted for scope only).

## What the diff does

Adds a repo-scoped advisory tier (`repo_candidates()`, `_repo_advisory_exists()`) queried
via `GET /repos/{owner}/{repo}/security-advisories`, and splits the old binary
`unresolved` bucket into `unresolved` (hard block) vs `inconclusive` (sets
`advisory_review_required=True` + `inconclusive_advisories`, does **not** flip `passed`).
`external_advisory_check()`'s three-way branch (`minerva_verify.py:130-175`):

```python
if exists is False and repo_hit is False:
    unresolved.append(ident)      # hard block
else:
    had_error = had_error or exists is None
    inconclusive.append(ident)    # soft — passed untouched
```

## Verification performed (adversarial, cold read + live + adhoc probe)

1. Read the diff cold (`git show 589707f`), not PR#627's own description.
2. Ran the shipped probe as-is: `test_external_advisory_check.py` — 15/15 PASS, live
   network, confirms `CVE-2026-83601`/`GHSA-3qh4-842w-fvrm` (wq#2001's reference case) now
   resolves repo-scoped and does not block. The stated fix works as claimed.
3. **Adversarial case the shipped tests never exercise: what happens on a fabricated id
   when the global check errors, not just misses?** The branch above only reaches
   `unresolved` when `exists is False` *exactly* — `exists is None` (NVD/GitHub API error,
   timeout, or non-200/404 status per `_cve_exists`/`_ghsa_exists`, `minerva_verify.py:210-238`)
   always falls into the `else` branch regardless of `repo_hit`, even when `repo_hit` is
   also a definitive `False`. Reproduced live against the shipped module (not a mock of the
   check's own logic — patched only the two network-calling leaf functions, the same
   pattern the shipped test file itself uses to patch `semantic_citation_score`/
   `judge_entailment_uq`):

   ```python
   mv._cve_exists = lambda cve_id: None      # simulate NVD/GitHub unreachable
   mv._ghsa_exists = lambda ghsa_id: None
   mv.repo_candidates = lambda text: []      # no repo hint in the proposal text
   claim = "netdata carries CVE-2026-99999, a HIGH severity advisory."
   result = mv.verify(claim, claim, subject="probe_api_down", mode="semantic")
   ```
   Result: `passed=True`, `unresolved_advisories=[]`, `inconclusive_advisories=
   ['CVE-2026-99999']`, `advisory_review_required=True`, `error_kind=
   'advisory_check_error'`. A fabricated CVE — the wq#1512 reference shape, scoring
   citation=1.0/judge=1.0 same as always — now **passes** whenever the global oracle
   errors, with only a decorative flag as consolation.
4. **Checked whether `advisory_review_required`/`inconclusive_advisories` is wired to
   anything that would actually catch this** before crediting the report's "degrades to
   human/agent review" claim: `grep -rl "inconclusive_advisories\|advisory_review_required"`
   across every reachable repo (`foreman`, `agents/work-queue`, `bin`, `kb`) returns only
   the three files this same PR touches (the module, its test, its own report) — zero
   consumers. Nothing reads the flag, queues a review, or blocks on it anywhere in reach.
   `passed=True` is the only signal anything downstream that gates on `result['passed']`
   (which is the documented contract — see the module's own "can only flip a passing
   verdict to failing" comment) will ever see.
5. **The fix itself raises the odds of hitting this path, not just tolerates it.** The new
   repo-scoped tier adds up to `ADVISORY_REPO_MAX_CANDIDATES` (4) extra calls to
   `api.github.com` per unresolved id (`GH_REPO_ADVISORY_API`), sharing the same
   unauthenticated rate-limit bucket (60 req/hr/IP) as `_ghsa_exists`'s
   `api.github.com/advisories/{id}` global lookup. `_ghsa_exists` returns `None` (not just
   on exceptions) for any non-200/non-404 status — exactly what a 403 rate-limit response
   looks like. A proposal citing several ids, or a busy verify queue, burns the shared
   budget faster with this diff than without it, making the fail-open branch more reachable
   in practice, not a purely theoretical edge.

## Why this matters (not a corner case)

- The module's own retained design comment (`minerva_verify.py:732-740`) still says Layer 0
  exists because citation/judge scores agree with a hallucinated source just as readily as
  a true one — that's precisely the scenario reproduced in step 3, and it now escapes.
- wq#2005's own report (`reports/2005_fix_wq1997_fresh_advisory_autoreject.md`) states: "an
  API error used to be a hard reject too... That now routes to review." The first half is
  accurate (verified: old code's `if exists is None: unresolved.append(...)` did hard-block
  on error); the second half is not — there is no review route, per step 4. The claimed
  trade (avoid over-rejecting during an outage) is real, but it was implemented as
  fail-open, not fail-to-review, because the review side was never built.
- wq#2005's ticket text explicitly scoped the intended fix as "genuinely fabricated id
  (resolves nowhere including repo-scoped) still blocks" — the shipped code only holds that
  promise when every leg *resolves* to a definitive answer; it silently drops the promise
  the moment any leg errors, which is a materially different (and broader) carve-out than
  the ticket asked for.

## What a correct fix looks like (not implemented here per this archetype's instruction)

Keep three-way branching, but don't let `exists is None` alone downgrade a case that would
otherwise be a **definitive** repo-scoped miss: `unresolved` should require "every leg that
answered said no," and a leg erroring should only ever add uncertainty in the direction of
`inconclusive`+review when at least one leg is a *positive-evidence-only* miss — never let
an error on the historically-stronger oracle (NVD/global GHSA) silently overrule a
definitive repo-scoped `False` into a pass. At minimum, wire `advisory_review_required`
to an actual queue/alert before the "review" framing is accurate — right now it is a field
nobody reads, so `passed=True` alone is a false all-clear for network-error cases exactly as
much as it was for wrongly-rejected fresh CVEs before this fix, just in the opposite
direction.

## ISSUE-LOG

Appended to `/home/lumen/kb/ISSUE-LOG.md` (#417): wq#2005/PR#627's `inconclusive` bucket
in `external_advisory_check()` (`minerva_verify.py:130-175`) also catches `exists is None`
(NVD/GitHub API error/timeout/rate-limit) regardless of repo-scoped result, so a fabricated
CVE citation now passes (`passed=True`) whenever the global oracle errors — same failure
class as wq#1512, previously fail-closed, now fail-open, and undetectable downstream
because `advisory_review_required`/`inconclusive_advisories` have no wired consumer
anywhere in reach (`foreman`, `agents/work-queue`, `bin`, `kb`). The added repo-scoped tier
shares GitHub's unauthenticated rate-limit bucket with the existing global check, raising
the odds of hitting this path. Filing follow-up ticket for opus-5@high per archetype-8
escalation.

## Bottom line

CONTRADICT. Escalating per archetype-8 protocol: contradiction → opus-5@high.
