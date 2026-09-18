# wq#2005 — wq#1997's Layer 0 auto-rejected real, recently-disclosed CVEs

Fixes the defect wq#2001's verify-pass found (CONTRADICTED, PR#623, merged
fecb854/873ea54). Branch: `feat/wq2005-judgment-wq-1997-external-advisory-layer-a1`.

## The id wq#1512 and wq#1997 both called fabricated is real

Re-confirmed live on 2026-09-18:

| probe | result |
| --- | --- |
| `GET /repos/netdata/netdata/security-advisories` | `GHSA-3qh4-842w-fvrm` / `CVE-2026-83601`, `state=published`, `severity=medium`, `published_at=2026-09-02T12:37:09Z` |
| `GET /advisories/GHSA-3qh4-842w-fvrm` (global reviewed DB) | `404` |
| `GET /rest/json/cves/2.0?cveId=CVE-2026-83601` (NVD) | `totalResults: 0` |

wq#1997's Layer 0 queried only the bottom two rows. Neither had synced a
16-day-old repo-level publication — and that lag is the default state of every
advisory in its first weeks, so "not yet promoted to the global index" was
indistinguishable from "fabricated". Fail-closed then turned that into a
silent reject: the mirror image of the wq#1512 hallucination-passes bug the
layer was built to fix. wq#1997's own test and install-health check asserted
the wrong direction on this id (`unresolved == ['CVE-2026-83601']`).

## Fix

`ops/wq1997-external-advisory-check/minerva_verify.py`

1. **Repo-scoped tier.** `repo_candidates()` (:249) infers `owner/repo` from a
   github.com URL, a bare slug, or `name/name` for a project-looking word —
   the reference text names no slug, only "netdata". `_repo_advisory_exists()`
   (:276) matches the exact id in each candidate's own published list. The
   last tier is a guess, and that is safe: a candidate can only ever *confirm*
   an id it holds, so a wrong guess 404s or returns a list without the id and
   changes nothing. Candidates are capped (`VERIFY_ADVISORY_MAX_REPOS`, 4) so
   a wordy proposal cannot fan out into GitHub's unauthenticated rate limit.
2. **Three outcomes, not two.** `external_advisory_check()` (:306) now returns
   `inconclusive` alongside `unresolved`. A hard block requires that the full
   check actually *ran*: a global miss **and** a definitive repo-list miss.
   When a lookup errored, or no repo list could be read, the id is
   inconclusive — we never asked the source that would have known.
3. **verify() wiring** (:754): `unresolved` still flips `passed` False;
   `inconclusive` sets `advisory_review_required` + `inconclusive_advisories`
   and tags the reason `advisory_needs_review(...)` without touching `passed`.
   A fresh real advisory degrades to review, not to a silent auto-reject.

Note that an API error used to be a hard reject too — an NVD outage could
reject every security proposal on the queue. That now routes to review.

## Liveness

`python3 ops/wq1997-external-advisory-check/test_external_advisory_check.py`
— 15/15 pass, exit 0. Cases pin both directions:

- `CVE-2026-83601` and `GHSA-3qh4-842w-fvrm` resolve repo-scoped;
  `verify()` returns `passed=True` with empty `unresolved_advisories`.
- `CVE-2026-99999` ("netdata" inferable, repo list read, id absent) still
  blocks: `passed=False`, `unresolved_advisory(CVE-2026-99999)`.
- `CVE-2026-99998` with no inferable repo → `advisory_review_required=True`,
  `passed` untouched.
- No-id text is still a zero-HTTP no-op.

`install.sh`'s import-health replay was asserting the contradicted claim; it
now checks the real id passes and a nowhere-resolving id blocks.
