# wq#2001 — verify-pass: wq#1997 external-existence check for CVE/GHSA (PR#623)

**Verdict: CONTRADICT** — the shipped Layer 0 check cannot reliably tell a fabricated
CVE/GHSA from a real, recently-disclosed one, because it only queries two indices (NVD's
CVE API, GitHub's *global reviewed* advisory API/search) that lag GitHub's *repo-level*
security-advisories by weeks. Proven on the exact id the ticket is built around:
`CVE-2026-83601` / `GHSA-3qh4-842w-fvrm`, which wq#1512/wq#1997 both call "fabricated," is
in fact a real, `state: published` netdata security advisory (published 2026-09-02,
`severity: medium`, CVSS 6.5 — matching the original Fleet Advisories item exactly) that
has been public for 16 days and still 404s/empty on both oracles the fix relies on.

PR: http://100.121.17.5:3000/lumen/claude-foreman/pulls/623, merged to main as
`fecb854`+`873ea54`. Shipped under `ops/wq1997-external-advisory-check/` (not applied to
live `/home/lumen/minerva`, which is outside every worktree's `--add-dir` — that
constraint, stated in the PR body, checks out; not itself a defect).

## What the diff does

Adds `ops/wq1997-external-advisory-check/minerva_verify.py` (a full copy of the live file
plus new code) and `test_external_advisory_check.py`. New pieces:
- `external_advisory_check(text)`: regex-extracts `CVE-\d{4}-\d+` / `GHSA-xxxx-xxxx-xxxx`
  ids from text, resolves each via `_cve_exists` (NVD `services.nvd.nist.gov/rest/json/
  cves/2.0?cveId=`) or `_ghsa_exists` (`api.github.com/advisories/{id}`), returns
  `{checked, unresolved, error}`. Network error and "not found" are both `unresolved`
  (fail-closed).
- Wired into `verify()` as "Layer 0," run last (after any latent-guard rescue): if
  `unresolved` is non-empty, force `passed=False` regardless of citation/judge scores.
- `install.sh` for hosts where the live-`/home/lumen/minerva` git fast-forward hasn't
  landed yet.

## Verification performed (adversarial, cold read + live probe)

1. **Read the diff cold** via `git -C /home/lumen/foreman show fecb854`/`873ea54`, not the
   PR's own description of itself.
2. **Ran the shipped probe as-is**: `test_external_advisory_check.py` — 5/5 PASS, exactly
   as the done-notes claim. This is where the defect hides: one of the 5 "passing"
   assertions (`wq1512 fabricated CVE is flagged unresolved`, asserting
   `external_advisory_check(...)["unresolved"] == ["CVE-2026-83601"]`) encodes the
   ticket's own unverified premise as ground truth, then checks the code against that
   premise instead of against reality.
3. **Independently re-verified the premise against live GitHub/NVD data** (this worktree
   has outbound network access; confirmed `nvd.nist.gov`/`api.github.com` reachable):
   - `GET api.github.com/repos/netdata/netdata/security-advisories` (the repo's actual
     advisory list, which is what the original wq#1512 "verified against the real
     netdata/netdata GitHub Security page" step should have hit) returns **10** advisories,
     not the 3 wq#1997 found — including `GHSA-3qh4-842w-fvrm` / `CVE-2026-83601`,
     `severity: medium`, `state: published`, `published_at: 2026-09-02T12:37:09Z`,
     summary "Streaming protocol dimension slot has no upper-bound guard, allowing integer
     overflow and out-of-bounds write." This is an exact match (id, severity, subject) for
     the item wq#1512/wq#1997 call fabricated.
   - `GET api.github.com/advisories/GHSA-3qh4-842w-fvrm` → **404**.
   - `GET api.github.com/advisories?ghsa_id=GHSA-3qh4-842w-fvrm` (global reviewed-DB
     search) → **200, `[]`** — genuinely absent from the global index, not a caching
     artifact.
   - `GET services.nvd.nist.gov/rest/json/cves/2.0?cveId=CVE-2026-83601` →
     `totalResults: 0`.
   - `GET github.com/netdata/netdata/security/advisories/GHSA-3qh4-842w-fvrm` (repo's own
     public page) → **200**, page renders, matches the API record.
   - Ran `_cve_exists`/`_ghsa_exists`/`external_advisory_check` from the shipped module
     directly against this id: returns `unresolved` (blocks), same as the shipped test
     asserts as "correct."
4. **Sanity-checked the other two "real" ids** wq#1997 cites as control cases
   (`CVE-2024-32019` for libuv, and its own claim that only 3 netdata advisories exist):
   older CVEs (2023/2024) resolve fine on both oracles — the lag is specifically on
   recently-assigned ids, which is exactly the class of proposal a live security-intel
   pipeline needs to get right, not the class it can afford to get wrong.

## Why this matters (not a corner case)

- The check is fail-closed by design (comment at `minerva_verify.py:207`: "a fabricated id
  ... and an API error both land in 'unresolved' ... both must block"). That's the right
  call for a true API error, but it makes "not yet synced to NVD/GH's global DB" — which
  is the *default* state for any CVE/GHSA in its first weeks of life — indistinguishable
  from "fabricated." The fix inverts the false-negative it was built to close (hallucinated
  CVE passes) into a false-positive on the opposite tail (real, fresh, disclosed CVE is
  auto-rejected), and the second failure mode is arguably worse: it silently suppresses
  genuine, still-unpatched, medium/high-severity findings for as long as GitHub's global
  promotion lag runs — 16+ days and still counting on the one id this ticket is about.
- This also means wq#1512's original "CLASS DEFECT" diagnosis is itself unverified: the
  proposal it flagged as a hallucination cited a real advisory. wq#1512/wq#1997's own
  research method (NVD + GH global advisory DB + web search) has the identical blind spot
  this PR now bakes into production. Re-opening or re-scoping wq#1512 is out of this
  ticket's scope (verify, not re-implement) but is a necessary input to the opus escalation
  below.

## What a correct fix looks like (not implemented here per this archetype's instruction)

Query the *repo-level* `GET /repos/{owner}/{repo}/security-advisories` list (or GitHub's
advisory search scoped by repo) in addition to the global DB/NVD before calling an id
unresolved — this endpoint had the real record from day one. Where the repo isn't known
from context, treat "absent from global DB, present nowhere else checked" as
`inconclusive`/needs-human-review rather than an automatic `passed=False`, so a brand-new
real CVE degrades to "flag for review" instead of "silently reject," while a genuinely
fabricated id still gets caught (it resolves nowhere, including the repo-level list).

## ISSUE-LOG

Appended to `/home/lumen/kb/ISSUE-LOG.md`: wq#1997's external-advisory-check Layer 0
(minerva_verify.py, PR#623) fail-closes on "not yet synced to NVD/GitHub's global
advisory DB," which is the normal state for any CVE/GHSA in its first weeks after
disclosure — reproduced against the ticket's own reference id (CVE-2026-83601 /
GHSA-3qh4-842w-fvrm), confirmed real and published 2026-09-02 via the netdata repo's own
GitHub security-advisories API, still absent from both oracles 16 days later. Filed
wq#2005 (`claude-opus-5@high`, deps wq#1997) to fix: add repo-scoped advisory lookup
and/or downgrade unresolved-but-unconfirmed to a review flag instead of an auto-reject.

## Bottom line

CONTRADICT. Escalating per archetype-8 protocol: contradiction → opus-5@high, filed as
wq#2005.
