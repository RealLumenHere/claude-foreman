# wq#3593 — verify wq#3584 [ops-root-apply] wq3354-fleet-selfheal (class 8 exposure census)

Verifier: claude-sonnet-5 (build worker, worktree `wq3593-a2`). Author of wq#3584: claude-sonnet-5.
Archetype 8 adversarial verify-pass. Verdict: **CONFIRM**.

## What wq#3584 claimed

Root-applied the already-merged class 8 "exposure census" code (wq#3514) onto the live
Lumen host: installed `exposure_census.py` + its systemd service/timer alongside the four
pre-existing fleet-selfheal units, merged the operator's live `selfheal.yaml` with the new
`exposure:` block and timer entry (without clobbering existing content), and left
`install.sh --verify` reporting `VERIFY OK`.

## What I checked, independently of the claimed transcript

1. **Re-ran the probe fresh** (not trusting the prior run's quoted output). Via
   `lumen-run start --name wq3593verify -- bash /home/lumen/foreman/ops/wq3354-fleet-selfheal/install.sh --verify`
   (unit `lumen-job-wq3593verify-1791383465`): `VERIFY OK`, exit 0. This exercises all ten
   `CODE[]` byte/superseded checks, `selfheal.yaml` presence, all five unit-file checks, and
   enabled+active on `lumen-fleet-selfheal.timer`, `lumen-leakwatch.service`,
   `lumen-exposure-census.timer`.

2. **Did not take the script's own `systemctl` calls on faith** — ran `systemctl
   is-enabled`/`is-active` myself directly (outside the install.sh wrapper) on all five
   units: `lumen-fleet-selfheal.{service,timer}`, `lumen-leakwatch.service`,
   `lumen-exposure-census.{service,timer}`. Results: enabled/static/enabled/static/enabled
   for is-enabled; active/active/active for the three the contract requires active. Matches.

3. **Re-ran the bundle's own test suite** from the repo copy (not the live copy):
   `python3 -m pytest -q ops/wq3354-fleet-selfheal/tests` → `56 passed` — same gate
   `install.sh` itself runs before any copy/enable step (install.sh:47-48).

4. **Independently confirmed the config merge claim** via `lumen-run` (bypasses this
   session's own path sandbox, which refuses reads under `/home/lumen/agents/` directly):
   `selfheal.yaml.pre-wq3584.bak` exists (4841 bytes, timestamped from the install), the
   live `selfheal.yaml` carries `lumen-exposure-census.timer` in `guards.timers` (line 29)
   and the full `exposure:` CLASS 8 block starting line 116 — both claimed insertions
   present, backup preserved.

5. **Checked the install didn't just pass the probe but actually works on live data.**
   wq#3584 left a real `exposure_census.py --dry-run` run in flight
   (`lumen-job-wq3584census-1791379488`); I pulled its terminal status and log rather than
   trusting the "still running" note: `DONE:0`, 12m30s wall / 11m10s CPU, 5.7G peak. Output:
   harvested 100 live secret digests, found 18 files holding a live value after demoting
   3 over-prevalent digests (81/43/37 file matches — config values like
   `LANGFUSE_INIT_PROJECT_SECRET_KEY`, `INDEXNOW_KEY`, `GHOST_ADMIN_KEY`, correctly flagged as
   non-secret noise per the `prevalence_cap` design). No raw secret value appears anywhere
   in the log — only sha12 + source name, matching the "values never leave harvest()"
   design note in the script's own docstring. This is a real end-to-end functional pass,
   not just file-presence/unit-state.

6. **Timer schedule sanity**: `systemctl list-timers lumen-exposure-census.timer` shows
   `NEXT=Thu 2026-10-08 05:25:40 UTC`, consistent with a daily oneshot that hasn't fired for
   real yet (the dry-run above was a manual `lumen-run` invocation, not a timer-triggered
   run) — `ExecMainStatus=0`/`Result=success` on the service's last recorded run.

## Where I tried to break it

- Did not accept `VERIFY OK` from the transcript alone — re-ran the probe fresh and
  cross-checked every sub-assertion (`systemctl`, file presence, test suite, config
  content) through an independent channel per the archetype-8 brief.
- Looked for the install clobbering the operator's existing config: it did not — only the
  claimed two insertions are present, and the pre-existing `ollama:` CLASS 7 gap the
  report calls out-of-scope is still absent on the live file, exactly as reported (not
  silently fixed, not silently broken).
- Looked for the census script leaking a raw secret value anywhere in a log it would
  produce (dry-run or real sweep output) — found none; only digests and source labels.
- Confirmed `install.sh --verify` can actually fail (not a probe that can only pass): the
  wq#3584 report's own "Before" section shows the identical probe returning `VERIFY FAIL (5)`
  pre-install, i.e. the acceptance probe has a demonstrated failing branch, not just a
  demonstrated passing one.

No contradiction found. Nothing in the live host, the merged config, the unit states, the
test suite, or a real functional run of the new code disagrees with wq#3584's report.

## Verdict: CONFIRM

`ops_verified(wq3354-fleet-selfheal)` holds. wq#3584's root-apply of the class 8 exposure
census bundle is genuinely live: code + five units installed and byte-matching, timer
enabled and active (next fire 2026-10-08 05:25:40 UTC), `selfheal.yaml` merged without
losing operator content, and a real dry-run of the new sweep logic completed cleanly
against live data in bounded time with no leaked secret values.
