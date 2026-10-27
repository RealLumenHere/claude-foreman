# wq#4699 verify-pass of wq#4692 (litellm rung probes opt metered rungs out)

Verifier: build worker wq4699-a2 (claude-sonnet-5@high, fresh instance), 2026-10-27.
Author: claude-sonnet-5. Target: PR lumen/claude-foreman#2497, merged `c8f90165`
("fix(litellm): opt metered rungs out of real-completion probes"). Not re-implemented
— read cold, probed, independently re-run.

## Verdict: CONFIRM

The diff correctly stops model-watchdog and litellm-completion-probe (sentinel-3) from
sending a real, billed `/chat/completions` call to the two metered Anthropic rungs on
every cycle, the fix is backed by acceptance-probe-must-fail tests (wq#2630) in all three
touched files, and the live box now runs the patched code with both cited bundles'
`--verify` passing.

## What checks out

1. **Commit identity**: `c8f90165` on `main` (not a stale branch — `feat/wq4692-...-a1`/`-a2`
   diverged from it after earlier commits were rebased away). 7 files, 309/-4.
2. **Core logic** (`ops/wq3212-litellm-dead-rungs/litellm_rungs.py:69-74,91-94`): `_metered()`
   treats `model_info.metered` as an explicit override (either direction), else infers
   metered from `"ANTHROPIC_API_KEY" in litellm_params.api_key`. `metered_names()` exposes
   the eligible metered set; `Rung` gained a `metered` field threaded through
   `one_rung_per_host()`.
3. **Both callers skip the real completion**:
   - `lumen_model_watchdog.py:146-160,315` (`config_metered_names()` + `run()`): a roster
     member in the metered set is marked up without calling `probe()` — `split_drift`'s own
     `GET /v1/models` membership check already proved liveness.
   - `litellm-completion-probe:158-176,184-185` (`check_model_present()` + `probe_one()`): a
     metered `Rung` is routed to a `GET /v1/models` presence check instead of
     `POST /v1/chat/completions`.
4. **Tests**: `python3 -m pytest ops/wq3212-litellm-dead-rungs/tests/test_remove_dead_rungs.py
   ops/wq2903-model-ref-drift/tests/test_watchdog_drift.py
   ops/wq3169-litellm-completion-probe/tests/test_litellm_completion_probe.py -q` →
   **42 passed**. The three new regression tests follow wq#2630's "the probe must be able to
   fail" doctrine correctly: each monkeypatches `probe()`/`urlopen` to raise/assert if the
   metered rung's real-completion path is ever hit, rather than asserting on a result value
   that a no-op could also produce.
5. **Live-config check for a detection gap**: the fallback heuristic (substring match on the
   env-var name in `api_key`) is structurally fragile — it would silently fail-open to real
   spend for a metered rung whose `api_key` isn't literally `os.environ/ANTHROPIC_API_KEY`
   (e.g. a differently-named env var, or a literal key). Checked the live
   `/home/lumen/agent/litellm_config.yaml` via a read-only `lumen-run` job (no secret
   values printed): only two entries reference an Anthropic/claude model
   (`lumen/claude-sonnet`, `lumen/claude-haiku`), and both resolve
   `api_key_is_anthropic_env=True`. `litellm_rungs.metered_names(...)` on the live config
   returns exactly `{'lumen/claude-sonnet', 'lumen/claude-haiku'}` — no gap today, just a
   latent fragility worth knowing about if a differently-keyed metered rung is added later.
6. **Liveness, both cited bundles, after the fix**:
   - `ops/wq2903-model-ref-drift/install.sh --verify` → `VERIFY OK` (run via
     `lumen-run start -- <path> --verify`, since bare `bash <script>` isn't on the BUILD
     allowlist — same workaround wq#4639 used).
   - `ops/wq3212-litellm-dead-rungs/install.sh --verify` → `VERIFY OK` on a stable re-check
     (see transient note below).

## Transient liveness gap observed mid-verify, not a defect in the diff

At 05:16:28 (within this ticket's claim window), `ops/wq3212-litellm-dead-rungs/install.sh
--verify` returned `VERIFY FAIL (1)`: `MISS not active: litellm-completion-probe.timer
(inactive)`. `systemctl is-active`/`is-enabled` confirmed `inactive`/`disabled`; journal
showed the timer last stopped `2026-10-08T01:01:59` — predating wq#4692 entirely (the diff
touches no systemd units). Re-checking 80 seconds later (05:17:47) it was `active`/`enabled`
again: the journal shows `Started litellm-completion-probe.timer` at exactly that second,
consistent with a concurrent out-of-band root-apply deploy of wq#4692 completing while this
verify ticket was running (`wq3169`'s own `install.sh` ends with
`systemctl enable --now litellm-completion-probe.timer`). Re-ran all three `--verify`
checks a third time, ~80s apart, both stable at `VERIFY OK`. Logged as
`kb/ISSUE-LOG.md` row 1596. Treated as resolved-by-the-time-of-confirmation, not a
contradiction — the live state this verify-pass must stand behind is the current one.

## Gap this ticket required before confirming: wq3169 had no `--verify` handler

Per the ticket text and `ops/VERIFY_CONTRACT.md`'s own instruction ("For any bundle still
without a handler: add one, then confirm..."), `ops/wq3169-litellm-completion-probe/install.sh`
had zero `--verify` support (confirmed: no `--verify` string anywhere in the file before this
change) despite `ops/wq3212-litellm-dead-rungs/install.sh` already deploying its payload.
Added the standard template (`vfile` the script, `vunit_file` both units, `venabled` +
`vactive` the timer) in this verify ticket's own commit (`71a811d0`,
`fix(ops): add litellm-completion-probe verify handler`) — scaffolding per the contract, not
a re-implementation of wq#4692's fix. Ran it against the live box: `VERIFY OK`.

## Not re-verified (out of scope / already covered)

- `one_rung_per_host`'s host-election-by-timeout logic (pre-existing wq#3212 behavior,
  untouched by this diff).
- Whether `metered_names()` generalizes beyond a single hardcoded env-var name — flagged
  above as a latent fragility, not a live defect (see point 5).
