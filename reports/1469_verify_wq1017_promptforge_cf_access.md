# wq#1469 — verify-pass: wq#1017 [prompt-forge] expose prompts.lumennode.uk — add CF Access

**Verdict: CONFIRM** — the shipped script (`ops/wq1017-promptforge-cf-access/add_access_hostname.py`)
does what its README and INSTALL.md wiring claim: an idempotent, offline-tested,
config-only Cloudflare Access hostname-add tool that refuses to guess on
ambiguity and never touches the live API without an operator-supplied token.
Two non-blocking observations below; neither reopens a functional defect.

PR: http://100.121.17.5:3000/lumen/claude-foreman/pulls/336 (merged,
`c706536` + `908b561`, branch `feat/wq1017-prompt-forge-expose-prompts-lumennode-uk-a1`).
This worktree (`d9eca3d`) branched before the merge, so the shipped files were
pulled out via `git show <commit>:<path>` rather than read from the working tree.

## What was verified

1. **Tests pass, and there are more of them than documented.** Copied
   `add_access_hostname.py` and `tests/test_add_access_hostname.py` (as of
   the PR's final commit `908b561`) into a scratch dir and ran
   `python3 -m unittest discover -s tests -p "test_*.py"`: **17/17 pass**,
   `Ran 17 tests in 0.007s / OK`. `README.md:68` still says "11 tests, all
   offline" — that was accurate for the PR's first commit (`09d524a`,
   `grep -c "def test_" test_add_access_hostname.py` → 11) but the second
   commit (`908b561`, "cover CLI entry point exit-code contract") added 6
   more `MainTest` cases (11 → 17, confirmed via the same grep against both
   commits) without updating the README count. Cosmetic doc drift, not a
   functional gap — if anything, coverage is better than advertised.

2. **Core logic matches the claims.** Read `add_access_hostname.py` in full
   (155 lines): `_domains()` merges `self_hosted_domains` + legacy `domain`
   without duplicating; `find_front_app()` looks the target app up by an
   *existing* covered hostname via `GET .../access/apps` and refuses (rather
   than guessing) on zero or multiple matches; `add_hostname()` no-ops if
   the hostname is already present, respects `--dry-run`, and only appends
   to the domains list on a live `PUT` — it never removes an existing
   domain. Exit codes (`0`/`1`/`2` for ok/API-or-lookup-failure/bad-invocation)
   match the docstring and are each covered by a `MainTest` case. Compiles
   clean (`py_compile`).

3. **The "needs root, out of BUILD-worker scope" claim for INSTALL.md steps
   3/5 is accurate, not hand-waved.** Confirmed directly: any command
   containing `sudo` is hard-blocked by this repo's own guard hook
   ("BLOCKED — denied verb (sudo/reboot/shutdown/poweroff/halt/init 0|6)"),
   which covers `sudo systemctl enable --now lumen-prompt-forge` (step 3)
   and the `/etc/cloudflared/config.yml` edit + `sudo systemctl restart
   cloudflared` + step 5's docker restart. The README's "same staged
   posture as wq#957/wq#718" framing holds up.

4. **The "no Access-mgmt token found" claim is consistent with the
   independent wq#1373 record.** `foreman/ops/wq1373-cf-token-liveness/README.md`
   (built after the PR's cited 2026-09-02 mint date) still lists the #37
   Access-mgmt token as pending, not wired up — the same gap this PR
   reports from its own sandbox search. No fabricated token path, no
   fabricated app id (`find_front_app` looks the app up live by domain
   rather than taking an id on faith).

## Non-blocking observations (not contradiction-worthy)

- **Unverifiable live-API behavior.** `add_hostname()`'s live `PUT` body is
  `{**app, "self_hosted_domains": new_domains}` — the entire GET-returned
  app object spread back with one field changed. This is a reasonable
  pattern but its correctness against Cloudflare's real Access Apps API
  (e.g., whether the list endpoint's app shape round-trips cleanly through
  a PUT, or whether read-only fields like `created_at`/`aud` are tolerated)
  can't be checked from this sandbox: no Access-mgmt token exists here to
  call the real API with, and doing so would be out of scope for a BUILD
  worker regardless (per instinct 7, not fabricating a value or a result to
  paper over the gap). The author disclosed this script as unexecuted
  against the live API; that disclosure holds.
- **No pagination on `GET .../access/apps`.** If the account ever has more
  Access apps than one page returns, `find_front_app` could report
  "no Access app currently covers home.lumennode.uk" even though it exists
  on a later page. Low real-world risk for this fleet's current app count,
  but worth a follow-up if the CF account grows.

## Disposition

No contradiction found. wq#1017's shipped diff is exactly what it claims to
be: a tested, idempotent, config-only script plus INSTALL.md wiring, with an
honestly-disclosed pending-token blocker for the parts that need live
infra. Closing wq#1469 CONFIRM.
