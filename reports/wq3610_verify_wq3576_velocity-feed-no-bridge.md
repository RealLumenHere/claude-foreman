# wq#3610 — verify wq#3576 [research] velocity feed for github_scout: rebuild the RSSHub bridge wq#2500 retired

Verifier: claude-sonnet-5@high (fresh instance, build worker, worktree `wq3610-a2`). Author of
wq#3576: claude-opus-5-5. Archetype 8 adversarial verify-pass. PR: http://100.121.17.5:3000/lumen/claude-foreman/pulls/1703
(merged `114ca343f7`, already on `main`/HEAD at `b06a5268`). Verdict: **CONFIRM**.

## What wq#3576 claimed

Retired `ops/wq2500-rsshub-bridge` ran `node lib/index.js`, which upstream RSSHub no longer ships
and which was never deployed on Sia anyway. Replacement `ops/wq3576-trending-velocity-feed`: Miniflux
on Sia subscribes directly to two static `mshibanami/GitHubTrendingRSS` GitHub Pages feeds — no
bridge, unit, or container runs anywhere. `install.sh --verify` is read-only: `vremote_up` on Sia,
then one SELECT per feed against `miniflux-db` checking subscribed / `parsing_error_count=0` / an
entry within 48h. `trending_vs_scout_diff.py` moved into the new bundle, fixed to also read
`research_prompts_done/` (the live queue dir is empty most of the time — consumed prompts move
there), and now sniffs RSS/JSON/text input. Claimed 26 tests pass. Explicitly disclosed as **not
deployed**: the worker had no ssh to Sia and no Miniflux API key, so neither the subscribe step nor
a live `--verify` could run; filed the deploy as a separate follow-up, wq#3609.

## What I checked

1. **Read the diff cold** via `git show 114ca343f7` in `/home/lumen/foreman` (not the worktree) —
   9 files, +526/-26. Read every new file in full (`install.sh`, `miniflux_subscribe.py`,
   `feeds.txt`, `trending_vs_scout_diff.py`, both test files, `README.md`, the retired bundle's
   README deprecation note) rather than trusting the PR description.

2. **Reran the bundle's own test suite independently**: `python3 -m pytest -q
   ops/wq3576-trending-velocity-feed ops/wq2500-rsshub-bridge` → **26 passed**, 0 failed — matches
   the claimed count exactly (21 new + 5 surviving on the retired bundle's inverted-sense verify
   test).

3. **Confirmed the moved-not-duplicated claim**: `ops/wq2500-rsshub-bridge/` has no leftover copy of
   `trending_vs_scout_diff.py`/`test_diff_report.py`; the retired README now carries a `RETIRED
   (wq#3486)` banner pointing at the new bundle.

4. **Hunted for the SQL-injection shape explicitly, since the verify handler string-interpolates
   `$url` into a `psql -c` argument**: `install.sh:29` rejects any feed URL containing a character
   outside `[A-Za-z0-9:/._-]` *before* the SQL string is built, and the check runs in the same loop
   iteration ahead of the `sql=` assignment — no way to smuggle a quote, backtick, `$`, or `;`
   through `feeds.txt`. Confirmed `feeds.txt` itself is plain LF, both lines satisfy the whitelist
   (`cat -A` showed no stray `\r`).

5. **Verified the fixture backing the pinned test is real, not fabricated**: `grep -c "<item>"
   fixtures/daily_all_2026-10-07.xml` → 12 (matches `test_trending_file_reads_the_pinned_rss_snapshot`
   and `test_cli_offline_against_pinned_fixture`'s `missed_total == 11`); `earthtojake/text-to-cad`
   present.

6. **Checked the chosen upstream source is still live today**, not just on 2026-10-07 when the
   README was written: `curl -s -o /dev/null -w '%{http_code}'` on both subscribed feed URLs → `200`
   / `200`; `daily/all.xml` still carries 12 `<item>` entries with a same-day `pubDate`. The
   no-bridge design's central bet (a third party keeps rebuilding a static feed) still holds.

7. **Adversarially verified the one claim I could not take on faith — Miniflux's actual duplicate-
   feed response shape, which `miniflux_subscribe.py:63-64`'s idempotency depends on**
   (`status == 400 and "already exists" in body.lower()`). Pulled the real upstream source instead
   of guessing: `internal/validator/feed.go:25-27` calls `store.FeedURLExists` and returns
   `locale.NewLocalizedError("error.feed_already_exists")` *before* `feedHandler.CreateFeed` ever
   runs; `internal/api/feed_handlers.go:49-52` routes that validation error through
   `response.JSONBadRequest` (HTTP 400); `internal/locale/translations/en_US.json` resolves
   `error.feed_already_exists` to the literal string `"This feed already exists."`. Status 400 +
   a lowercased body containing "already exists" — exactly what the Python checks for. (There is a
   second, dead-for-this-path `ErrDuplicatedFeed`/`error.duplicated_feed` wrapper later in
   `handler.go` that routes through `JSONServerError`/500 instead, but validation short-circuits
   before that code is ever reached for a plain duplicate URL, so it doesn't apply here.)

8. **Ran the live probe myself and hit the same wall the author hit**: both `bash
   .../install.sh --verify` and the executable directly were refused — `"This command requires
   approval"` — because this BUILD lane's `harness/worker-settings.json` has no `Bash(bash *)`/`ssh`
   allowlist entry broad enough to cover it (confirmed by `grep` — no `ssh` entry anywhere, only
   `Bash(bash scripts/*)`, which doesn't match an `ops/` path). This is the exact same wall
   documented in `kb/ISSUE-LOG.md` #256 for a prior ssh(anubis)-tagged verify ticket, and the one
   `wq#3609`'s own notes record for this same bundle. Per that precedent and the "don't retry the
   same refusal" rule, I did not loop on it.

9. **Followed up on wq#3609 instead of treating the gap as unknown**: `wq show 3609` shows the
   bundle's `--verify`, when actually run (by whatever process has real ssh to Sia — likely an
   automated ops-deploy-drift sweep, not a BUILD worker), currently returns **exit 1** with two
   `MISS ...: not subscribed in Miniflux` lines, one per feed. This is not a surprise the diff is
   hiding: wq#3576's own done-notes say exactly this — `[NO-DEPLOY-NEEDED: worker has no ssh to Sia
   nor Miniflux key; subscribe step is the root-apply follow-up] [ROOT-APPLY-FILED: wq#3609]` — and
   wq#3609 is open/blocked on the identical credential/ssh wall, not on a defect in this bundle's
   code.

## Where I tried to break it

- Treated "26 tests pass" and "fixed the research_prompts_done bug" as claims to reproduce, not
  facts to accept — reran the suite myself and read `test_scout_pool_includes_consumed_prompts`
  to confirm the dual-dir fix is actually exercised (queue-only reads 0, queue+done reads the repo).
- Looked specifically for the failure mode `ops/_verify.sh:44-62`'s own comments warn about
  (`_vsuperseded`) — not applicable here, since this bundle's `--verify` never calls `vfile`/`vunit_file`
  at all (there is no local file to deploy), consistent with "nothing runs anywhere."
- Looked for a probe that could not fail (wq#2630's standing instruction): confirmed the test
  suite's fake-ssh harness drives `--verify` through all three exit codes (0 OK / 1 MISS / 2 ERR) and
  that `test_unreachable_sia_is_err_not_miss` / `test_failed_db_query_is_err_not_miss` specifically
  assert ERR is never misreported as MISS — the ERR-vs-MISS distinction `_verify.sh` documents is
  actually tested, not just asserted in a comment.
- Did not accept the idempotency claim about Miniflux's HTTP response on faith — pulled the actual
  upstream Go source for the exact status code and message, per the "verify it, don't guess" doctrine.
- Tried to run the live probe myself rather than relying solely on the author's and wq#3609's say-so;
  got the same structural refusal they did, which corroborates rather than contradicts their account.
- No contradiction found in the diff itself. The one outstanding gap — live `--verify` currently
  fails because the subscribe step was never applied — is pre-existing, disclosed by the author,
  already filed as wq#3609, and blocked on the same credential/ssh wall this verify-pass also hit.
  It is a liveness/deploy gap, not a code defect, so I'm not escalating it as a contradiction.

## Verdict: CONFIRM

The diff does exactly what it claims: a correctly-scoped, well-tested, no-bridge replacement for
the retired RSSHub bundle, with its one unverifiable-by-source-reading claim (Miniflux's duplicate-
feed response) checked against real upstream source and found correct. 26/26 tests pass, reproduced
independently. The live `--verify` probe cannot be run from this BUILD lane (no ssh allowlist entry,
same wall as `kb/ISSUE-LOG.md` #256 and wq#3609), and external evidence (wq#3609) shows it currently
fails live because the Miniflux subscribe step is still pending — a disclosed, already-ticketed
deploy gap, not something this diff got wrong.

## Structural gap (not specific to this ticket)

Same class as `kb/ISSUE-LOG.md` #256: this ticket's own capability lint tagged `needs: ssh(anubis)`,
but no BUILD-lane `worker-settings.json` profile grants `ssh` (or a bare `bash <path>` broad enough
to reach a script that calls it) to any host. Every verify-pass tagged with a remote liveness probe
on this lane hits the identical refusal. Worth fixing once at the lint/allowlist level rather than
re-discovering per ticket.
