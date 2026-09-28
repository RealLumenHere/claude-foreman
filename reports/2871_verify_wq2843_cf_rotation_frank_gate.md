# wq#2871 — verify-pass of wq#2843 (PR#1178): CF_ROTATION_RE widened to CF[_-] variable names

**Verdict: CONFIRM.** wq#2843's fix closes the exact gap wq#2787 (ISSUE-LOG 869) CONTRADICTed
in wq#2782: both walls now key on one shared shape (`harness/cf_hold.py:CF_NAMED`) that
matches a bare `CF[_-]` variable-name prefix as well as the literal word `cloudflare`, so a
`CF_API_TOKEN`/`CF_ZONE_ID`/`CF_ACCOUNT_ID` credential caught under the generic `kv-secret`
mask label is now held. No contradiction found; nothing escalates to opus-5@high.

## What was verified

1. **The regex change is real and shared, not duplicated.** `harness/fworker.py:1164`
   (`is_cf_rotation`) and `dispatcher/foreman_dispatcher.py:179`
   (`CF_ROTATION_HOLD_RE = re.compile(r"\[rotation\].*" + cf_hold.CF_NAMED, re.I)`) both
   import and compose `cf_hold.CF_NAMED` — the two walls that drifted apart under wq#2782
   (one `cloudflare`-literal regex hand-copied into each file) cannot independently drift
   again, because there is now exactly one source string.

2. **The real-world reproduction from ISSUE-LOG 869 is genuinely fixed.** Confirmed the
   cited live line still reads as described: `ops/wq2608-dc-endpoint-bes-a1/dns_access_setup.sh`
   at the pinned rev `93a79d6` has `CF_ACCOUNT_ID=...` (line 25) and
   `CF_API_TOKEN=$(cat /home/lumen/.secrets/cf_token)` (line 46) — no `cloudflare` substring
   anywhere. Ran the fix's own out-of-corpus probe (a synthetic body attached to that
   variable name, since the live line is a pointer masking suppresses): old
   `is_cf_rotation` → `False`, new → `True`; same flip on the dispatcher's title wall.

3. **The second-wall finding is real and is actually closed.** A rotation ticket's title
   carries only mask LABELS (e.g. `kv-secret`), never the masked context — so widening the
   title regex alone could not have caught a CF value found only by variable name, since the
   word never reaches the title. `fworker.rotation_title(..., cf_held=True)` now appends
   `cf_hold.TITLE_MARKER` (`[cf-hold]`, itself in the `CF[_-]` shape), which is what lets
   `CF_ROTATION_HOLD_RE` recognise the row. Verified end to end with
   `test_mask_alarm_frank_gates_a_cf_api_token_row`: label `kv-secret`, no `cloudflare`
   anywhere in the input or the record, title still comes back `--frank-gated` and carrying
   the marker.

4. **Full test suites reproduced independently**, in a detached nested worktree at the fix
   commit `c137934` (`git -C /home/lumen/foreman worktree add --detach
   /tmp/fworker-wq2871/verify_wq2843 c137934`, ISSUE-LOG #691's pattern, since the branch's
   own worktree at `/home/lumen/worktrees/wq2843-a1` is outside this ticket's `--add-dir`
   set):
   - `pytest harness/tests/test_masking.py` → **110 passed** (full file, not just the new
     cases).
   - `pytest harness/tests/test_mask_triage.py` → **32 passed**.
   - `pytest dispatcher/tests/test_dispatcher.py -k "gate or rotation or nodispatch or
     skip"` → **29 passed**, matching wq#2843's own claimed count exactly.
   - `pytest dispatcher/tests/test_dispatcher.py -k cf_rotation` → **2 passed**
     (`test_cf_rotation_hold_title_skipped`, `test_cf_rotation_hold_holds_a_row_that_never_
     says_cloudflare`).

## What I tried to break

- **Falsifiability of the shipped tests.** Every new probe (`CF_KV_LINE`,
  `test_is_cf_rotation_matches_an_abbreviated_cf_variable_name`, the dispatcher's
  `test_cf_rotation_hold_holds_a_row_that_never_says_cloudflare`) is keyed on
  `CF_API_TOKEN`/`CF_ZONE_ID`, and each asserts `"cloudflare" not in ...` on its own input —
  confirmed these probes cannot pass by matching the literal word, closing the exact
  wq#2630/wq#2787-shaped gap wq#2782's own tests had (which fed the check nothing but
  `cloudflare`-containing strings).
- **Independent adversarial regex probing beyond the shipped corpus.** Ran my own table of
  titles/contexts against the composed dispatcher regex and `cf_hold.names_cf_resource`,
  not reusing the shipped fixtures: `WQ_CF_ZONE_ID` (mid-identifier, matches — the lookbehind
  is `(?<![A-Za-z0-9])`, not `\b`, specifically so a `_`-prefixed embedding still fires),
  `CFG_SECRET`/`CFLAG_TOKEN`/`NETCF_TOKEN` (no separator directly behind `CF`, all correctly
  excluded), `CLOUDFLARE_ZONE_ID` (bare-word branch, matches), a bare `CF_API_TOKEN` mention
  with no `[rotation]` prefix (correctly does NOT dispatch-hold — the dispatcher wall is
  scoped to rotation titles only, not every ticket mentioning a CF var). All matched
  expectation; found no shape from either wall's own regex source that flips against my
  crafted inputs.
- **Checked the pre-existing failure claim rather than trusting it.** wq#2843's notes claim
  "full dispatcher suite 1 failed/149 passed, that failure reproduces on main, filed as
  wq#2870 (ISSUE-LOG 879)." Independently ran the full suite at the fix commit: still fails,
  but on a *different* test than ISSUE-LOG 879 names
  (`test_auto_lane_non_sweep_ticket_still_unmapped`, reason `no_fresh_stamp` rather than the
  row's `lane_unmapped`/guessed-repo symptom), and running the same `-k
  auto_lane_non_sweep` filter against current `origin/main` (`37e5c10`) fails *again* on yet
  another variant (`test_auto_lane_non_sweep_ticket_uses_lane_default`, reason "no LANE_REPO
  entry"). This is continued unrelated churn in the auto-lane dispatcher path — wq#2870
  already landed a fix for the row-879 shape (`69467f1`), and today's wq#2818/wq#2853 are
  independently reworking the same auto-lane code further. None of it touches
  `cf_hold.py`/`is_cf_rotation`/`CF_ROTATION_HOLD_RE`; it is noise in a shared test file, not
  a wq#2843 regression. Scoped `-k` runs (listed above) are clean, so this does not affect
  the verdict.

## Liveness gate note (out of scope, filed for the record only)

This verify ticket's own text carries `Liveness: ops_verified(wq2608-dc-endpoint-bes-a1)`,
generated by `orchestrator/judgment.py:ops_bundles_cited()` because wq#2843's ticket
`detail`/`notes` mention `ops/wq2608-dc-endpoint-bes-a1/dns_access_setup.sh` as the
real-world *evidence* for the bug (the live naming convention), not as anything wq#2843
shipped, installed, or claims is live — `wq show 2843`'s `deliverable` field is `None`. Ran
it anyway via `lumen-run` (direct `bash .../install.sh --verify` is outside this worker's
Bash allowlist, same gap ISSUE-LOG documents at row `reports/2619_verify_wq2600_...md`):
result is `VERIFY FAIL (4)` — `dc-gateway.service` is `inactive`/not installed on `bes`, no
cloudflared ingress rule for `mcp-bes.lumennode.uk`. That is expected, not a regression:
the bundle's own `install.sh` header says "Never installs anything" and its `README.md`
states the real rollout still needs a root run on Bes plus wq#2614's Access half — it has
never been live. This `Liveness:` line is also inert for this ticket regardless:
`harness/liveness_sweep.py`/`liveness_clear.py` only evaluate `Liveness:` lines on
frank-gated tickets, and wq#2871 has `gate_type: None`. Net: `ops_bundles_cited()`'s citation
scrape doesn't distinguish "the ops path this ticket shipped" from "an ops path cited as
supporting evidence" — a real, minor gap in judgment.py worth narrowing (e.g. only scan
`deliverable`, or require the bundle path to appear in a shipped diff's file list), but it
does not bear on wq#2843's correctness and is left as a note rather than a new ticket, since
it never gates anything for a non-frank-gated verify ticket like this one.

## Evidence index

- `foreman` commit `c137934` (the wq#2843 fix), branch
  `feat/wq2843-fix-wq-2782-cf-rotation-frank-gate-cf-ro-a1`, PR#1178
- `harness/cf_hold.py` (new), `harness/fworker.py:1089-1168`,
  `dispatcher/foreman_dispatcher.py:172-181`
- `harness/tests/test_masking.py:1115-1179` (new wq#2843 cases),
  `dispatcher/tests/test_dispatcher.py:595-` (`test_cf_rotation_hold_holds_a_row_that_never_says_cloudflare`)
- `ops/wq2608-dc-endpoint-bes-a1/dns_access_setup.sh` at rev `93a79d6`
- `kb/ISSUE-LOG.md` rows 869 (wq#2787's original contradiction, now marked FIXED by wq#2843),
  879 (the unrelated pre-existing dispatcher failure wq#2843 itself filed as wq#2870)
- `wq show 2843`, `wq show 2870`
