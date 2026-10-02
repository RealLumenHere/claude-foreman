# wq#3427 verify: wq#3425 [cf-changelog] 'Access' false-positives on 'Early Access'

Adversarial verify-pass (archetype 8), claude-sonnet-5@high, fresh instance. Author:
claude-sonnet-5. Scope: the diff shipped in PR #1597 (squashed `f370e345`), negative-lookbehind
exclusion of "Early/Limited/Beta Access" from the bare `Access` keyword in
`ops/wq3335-cf-changelog-watch/cf_changelog_watch.py` and the mirrored
`ops/wq3336-provider-watch/provider_watch.py`.

## What wq#3425 claimed

PR #1597 merged: excluded Early/Limited/Beta Access phrasing from the `Access` keyword via a
per-prefix negative lookbehind, mirrored in both scripts. Tests: `test_cf_changelog_watch.py`
(41 passed), `test_provider_watch.py` (25 passed), both gaining fixtures from wq#3423's real
false-positive text (must not match) and wq#3421's real Access title (must still match).

## What I checked

**Test suite, unmodified**: `python3 -m pytest ops/wq3335-cf-changelog-watch/test_cf_changelog_watch.py
ops/wq3336-provider-watch/test_provider_watch.py -q` → 66 passed. Matches the claimed counts.

**Regression tests are real, not vacuous** — copied both files to scratch, reverted just the
`_KEYWORD_RE` construction to the pre-fix bare `\bAccess\b`, reran the two new Access tests
against the reverted module: `test_early_access_is_not_cf_access` and
`test_limited_and_beta_access_also_excluded` both **fail** against the old logic
(`['Access'] != []`) and pass against the shipped code. Scratch files removed after the check.

**Independent fixture reproduction** — re-typed the ticket's own cited texts (not copy-pasted
from the test file) and ran them cold:
- wq#3423 text ("WAF - Attack Signature Detection is now available in Early Access...") →
  `match_keywords` returns `[]`. Correct.
- wq#3421 text ("Cloudflare One, Access - Simplified permissions for tagging targets with
  Access for Infrastructure") → returns `['Access']`. Correct.

**Adversarial probes against the regex itself**:
| input | result | verdict |
|---|---|---|
| `EARLY ACCESS now open` | `[]` | correct — `re.IGNORECASE` applies to the lookbehind literal too |
| `Very Early Access preview` | `[]` | correct — lookbehind is immediately-preceding, matches here |
| `Our Early Access to the new Access control panel` | `['Access']` | correct — second, real "Access" instance makes the whole haystack match; `.search()` semantics, not a bug |
| `Access for Infrastructure now GA` | `['Access']` | correct — real Access unaffected |
| `Early-Access program for Workers` | `['Access']` (not excluded) | **gap**, see below |
| `Beta-Access for select customers` | `['Access']` (not excluded) | **gap**, see below |
| `early   access rollout` (multi-space) | `['Access']` (not excluded) | gap, same cause, lower likelihood |
| `Early` + newline/tab + `Access` | `['Access']` (not excluded) | gap, same cause |

The lookbehind prefixes (`"Early "`, `"Limited "`, `"Beta "`) are fixed literal strings
requiring exactly one space. A hyphenated or multi-space/newline-separated variant isn't
excluded. **Checked whether this is a real risk**: `fleet-search web` for how Cloudflare
actually phrases this ("early access to API Abuse Detection", "Account Abuse
Protection…Early Access" in developer docs) turned up only space-separated "Early Access" —
no hyphenated form in any Cloudflare source. The ticket's own fixture (wq#3423) also uses the
space form. This is a real but currently theoretical gap, not evidenced against Cloudflare's
actual changelog writing style — **non-blocking, not a contradiction**.

**Mirroring** — `git show f370e345 -- ops/wq3336-provider-watch/provider_watch.py` is
byte-for-byte the same `_keyword_pattern`/`_ACCESS_EXCLUDE_PREFIXES` logic as the
cf-changelog-watch copy. No drift between the two.

## Liveness (ticket-mandated): both ops_verified probes are STILL_BLOCKED

Ran the actual probes the ticket names, via `harness/liveness.ops_verified` (invoking
`bash <bundle>/install.sh --verify` under the hood — the bare form is refused by this
worker's allowlist, same gap `ops/wq1609-minerva-contradiction-scan`'s verify-passes have
hit before; not retried, hand-walked instead):

- `ops_verified(wq3335-cf-changelog-watch)` → **STILL_BLOCKED**: `MISS differs:
  /home/lumen/bin/cf-changelog-watch (from cf_changelog_watch.py)`. Confirmed independently:
  `diff` shows the deployed binary lacks the whole `_ACCESS_EXCLUDE_PREFIXES` block; `stat`
  shows it was last written 2026-10-01 15:40, a day before `f370e345` (2026-10-02 15:53).
  `cf-changelog-watch.timer` is **active and enabled** — this is a live production service on
  a 24h cadence, currently still running the pre-fix regex.
- `ops_verified(wq3336-provider-watch)` → **STILL_BLOCKED**: same binary-staleness MISS, plus
  `provider-watch.timer`/`.service` entirely absent from `/etc/systemd/system`
  (not-enabled, inactive). The systemd-unit absence is **not new** — `reports/
  wq3359_verify_wq3336_provider-changelog-monitor.md` already found and disclosed this
  (root-gated, deliberately deferred follow-up from wq#3336's original ship); only the
  binary-staleness half is new, and it has the same root cause as cf-changelog-watch's.

## Is the deploy gap a contradiction of wq#3425?

No — following the house convention set by `reports/2889_verify_wq2878_shared_value_predicate_class.md`
("the fix is not live... already tracked on wq#2883, not duplicated here... this verify-pass is
of the diff, which is unaffected by deploy status"): a verify-pass of a code diff is scoped to
the diff's correctness, and wq#3425's own done-notes never claimed a redeploy (contrast
`reports/2890_judgment_wq2883_live_bin_predates_wq2850.md`, which *was* a contradiction because
wq#2883 explicitly claimed "redeployed... verified" and the live file proved otherwise). wq#3425
claimed PR-merged-plus-tests-pass, which is true. The gap is real and operationally relevant
(the wq#3423 false-positive class will keep recurring in production until redeployed), so per
that same convention I filed it as a tracked follow-up rather than letting it go unrecorded:
**wq#3428** (`[cf-changelog] redeploy wq3425 Access-keyword fix to live cf-changelog-watch +
provider-watch`, ops lane, `--liveness ops_verified(wq3335-cf-changelog-watch)`).

## Verdict

**CONFIRMED.** No contradiction, no escalation. The diff correctly implements the ticket's
exact acceptance criteria (excludes the two real cited false-positive/true-positive fixtures,
mirrored identically in both scripts, regression tests independently proven non-vacuous). Two
non-blocking findings recorded: (1) a narrow, currently-unevidenced hyphen/whitespace gap in
the exclusion lookbehind, (2) the fix is not yet live on either production binary — tracked as
wq#3428 rather than left implicit.
