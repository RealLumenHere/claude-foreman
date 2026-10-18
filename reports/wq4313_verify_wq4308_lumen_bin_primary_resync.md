# wq#4313 — verify wq#4308 (PR#258, lumen-bin primary resync)

**Verdict: CONFIRM.** wq#4308's done-notes hold under a fresh adversarial pass. The
one apparent gap (ticket text says `grep -c _clean_amazon` should read 6, live file
reads 10) is a pre-existing stale number in the ticket's own acceptance clause, not a
defect introduced by this diff — independently re-derived below, not taken from
wq#4308's report.

## What I re-checked, independent of the author's report

1. **Ancestry (fresh run):**
   `git -C /home/lumen/bin merge-base --is-ancestor a3fef0e HEAD` → exit 0.
2. **No divergence from remote (fresh `git fetch origin main`, not the cached ref):**
   `origin/main` resolves to `fc62d28e04aa1ca747348d046f921dee676d34d2`, identical to
   local `HEAD` and local `main`. `rev-list --left-right --count main...HEAD` → `0 0`.
   Branch is `main` (not detached): `git branch --show-current` → `main`.
3. **Tree clean:** `git status --porcelain` → empty.
4. **PR#258 actually closed, not just claimed:** `/home/lumen/bin/lumen-pr check
   /home/lumen/bin` lists `open PRs:` for `lumen/lumen-bin` and #258 is absent from
   that list (dirty: 0 file(s)) — independent confirmation it landed, from a tool that
   queries the gitea broker rather than re-reading the report's own claim.
5. **File-preservation claim, recomputed from the commit object itself (not from the
   report's prose):** `git show fc62d28 --name-status` → 8 `M` + 4 `A` = 12 files,
   matching the 8 modified + (5 untracked − 1 already-tracked-elsewhere) = 4 added
   the report describes. `aider-fworker` is confirmed tracked since `f28c22f` and
   present on disk, so its preservation claim doesn't depend on this commit at all.
6. **`fc62d28` vs the PR's squash commit `3c401fac82`:** these are *not* the same
   commit hash (different timestamp/trailer), but `git diff fc62d28 3c401fac82` is
   empty — identical tree, same parent (`f28c22f`). No content divergence; the
   two-hash situation is cosmetic (local re-commit vs. the PR's merge commit), not a
   fork in history. Worth flagging for whoever reads this later, but not
   acceptance-relevant since origin/main matches `fc62d28` exactly (point 2).
7. **Numeric claims, recomputed fresh:**
   - `grep -c _clean_amazon /home/lumen/bin/fleet-search` → **10**, at the exact line
     numbers the report cites (273, 287, 288, 291, 327, 328, 353, 354, 400, 401).
   - `grep -c '_clean_amazon\|AMAZON_NOISE_PARAMS'` → **13**, matching the report.
   - **Traced the "6" to its origin**, which the report did not do: wq#4299's verify
     report (`reports/wq4299_verify_wq4281_fleet_search_amazon_subscriptionid.md:34`)
     is where "exactly 6" first appears. Checking the blob at `a3fef0e` itself (the
     Amazon-fix commit, i.e. `main`'s state at the moment the fix landed, before any
     resync question even existed): `git -C /home/lumen/bin show a3fef0e:fleet-search
     | grep -c _clean_amazon` → **10**, same as today. So "6" was never achievable
     against the landed fix at any point in this repo's history that matters here —
     it was a measurement against an earlier revision of the PR, baked into the
     ticket text, and already flagged stale by wq#4305 before wq#4308 was filed.
     wq#4308's report citing that prior flag rather than re-litigating it is the
     correct move, not a dodge.
8. **Sanity check on preserved file content:** `python3 -I -m py_compile
   mac-bench.py litellm_rungs.py` → clean (exit 0). (Shell-script syntax checks via
   `bash -n` / `sh -n` were refused by the worker's Bash allowlist — not retried, per
   doctrine; noted for the allowlist, not treated as a finding against the diff.)
9. **ISSUE-LOG row 1450** (cited for the `sync-live` allowlist gap) exists and matches
   the report's description.

## Things I tried to break and couldn't

- Tried to catch primary parked on a feature branch again (recurrence class
  wq#900/wq#1313/row 1441) — `main`, 0 ahead/0 behind, confirmed by a fresh fetch, not
  a stale cached ref.
- Tried to catch the squash-vs-local-commit hash mismatch as a silent fork from
  origin — ruled out by `git diff` being empty and `origin/main` matching `fc62d28`
  after a live fetch.
- Tried to catch an undercount on preserved files (easy to drop one in a
  backup/restore dance) — commit object's own `--name-status` gives exactly 12,
  matching 8 modified + 4 added, with `aider-fworker` resolved separately.
- Tried to catch the acceptance clause's "6" as evidence of an actual regression
  introduced by this ticket — traced it to `a3fef0e`'s own blob, where it already
  reads 10, so the number was wrong before wq#4308 ever touched the tree.

No contradiction found. Not escalating to opus-5@high.

## Scope note

wq#4309 (central `_scrub` at `cache_store`/`emit`, filed by wq#4305 as the real fix
for the scope gap) is separate and out of scope for this verify pass — it was still
in `research` phase under an unrelated, now-stale busy-key entry I cleared at the
start of this session (dead PID, no live work lost).
