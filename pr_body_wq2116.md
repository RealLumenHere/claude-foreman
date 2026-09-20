# Full base-`add_dir` sweep: triage, and the `_runs/` snapshot hole

## What

wq#1483 closed on two named files. Running the same `read_dir_sweep.py` across **every**
`agents.yaml` base `add_dir` (work-queue, mailbot, pokemon, bin, foreman, kb, projects/nest)
turned up 361 hits. This triages all of them, fixes the one real mechanism behind the large
majority, redacts a live token out of two git-tracked files, and records the evidence.

## Why

`action_gate.snapshot()` takes a verbatim pre-image of every AMBER-classified file edit into
`_runs/<ticket>/snapshots/`. That directory lives under `/home/lumen/foreman`, which is a base
`--add-dir` for every worker. So a worker that edits a credential file copies it whole into the
grant — and the copy outlives the fix to the live file.

What was actually sitting there: two full copies of `/home/lumen/sentinel/config/config.yaml`
(the 38-occurrence gatus config, wq#1466) and 15+ copies of `/home/lumen/agent/.env` (live
`GITHUB_TOKEN`). Both are files the allow list refuses to grant `dir(...)` on **by name**.
`_runs/` handed a worker the contents anyway. 323 of foreman's 348 hits.

A read grant is not just what the allow list names — it is everything reachable under the root.

## How

- `harness/action_gate.py`: every snapshot artifact now goes through `_stow()`, which asks
  `read_dir_sweep`'s own `BASENAME_GLOBS` + `PATTERNS` whether the bytes are credential-shaped
  (one list, two consumers). If they are, the pre-image lands in `~/.secrets/gate-snapshots` at
  0600 — outside every `add_dir` — and the snapdir gets a path-only `.held-out` pointer instead
  of the bytes. Rollback still works; the grant no longer reaches the value. A failed import of
  the sweep counts as sensitive, so it fails closed. `git-state.txt` and `systemctl show` output
  go through the same check, since both can carry a secret nobody handed them directly.
- Redacted the live gatus push token out of `ops/wq897-litellm-auth-monitor-probe/gatus_config.diff`
  and `reports/wq908_pokemon_liveness_probe.md` — both git-tracked, both verbatim pastes of
  `sentinel/config/config.yaml`. Same value in both, and the same value the `_runs` copy of the
  live config carries, so it is the real shared `5-probes` token, not a placeholder. It is in git
  history, so it rotates with wq#1466 (noted there with all four escape sites).
- `research/BROKER_GRANT_COVERAGE.md` §3.1/§3.2: the per-root table, the file:line triage, and
  the retention decision.

### Triage result

Four live non-test source hits, all false positives, no credential literal in live foreman source:
`despag/app.py:100` and `claude-shim/claude_shim.py:83` (RHS is a function call, both read from a
0600 file), `research/headroom_proxy_gate.py:42` (literal says `not-a-real-credential`),
`events/fworker-wq1482-a3.jsonl:37,38` (the alphabet-and-digits fixture). `projects/nest`'s two
`*.pem` hits are a CA bundle and a CRL bundle — zero `PRIVATE KEY` blocks.

The first two are a **third** `kv_secret` false-positive class, distinct from wq#2115's two: the
pattern matches a Python identifier or call on the right of `=`. Noted on wq#2115 so one owner
fixes one regex, rather than this branch racing it in the same tuple.

### Out of scope here

- Re-running the sweep after wq#2115 lands to confirm the fixture noise clears. wq#2115 is still
  `claimed`, nothing merged. The pre-fix baseline is in §3.1, and wq#2115 now carries the re-run
  in its own acceptance.
- The history half: 57 existing foreman run dirs plus 8 work-queue transcript hits still hold the
  old copies. Neither tree is git-tracked, so it is a local-disk relocation rather than a history
  rewrite, and it needs an `mv` beyond any worktree. Filed as wq#2118, which proposes moving
  rather than destroying: same vault, same pointer, record intact.

## Testing

`python3 -m pytest harness/tests -q` — 1176 passed. Four new cases in
`harness/tests/test_action_gate.py` cover divert-on-content, divert-on-basename, no-divert for an
ordinary file, and fail-closed when the sweep import is unavailable.

One unrelated pre-existing failure, `test_liveness_ops_verified.py::test_drifted_target_still_blocked`:
the worktree's pytest tmp root is deep enough that `liveness` truncates the detail string the
assertion reads. The same file passes 10/10 under `--basetemp=/tmp/w2116bt`. It does not import
`action_gate`.

`python3 harness/read_dir_sweep.py ops reports research` in this worktree: `reports` 0 hits,
`ops/wq897-…` gone, `research` down to the one synthetic negative control.

## Risk / rollback

`action_gate` is a fail-closed PreToolUse hook on every worker tool call, so the change is
deliberately conservative: the classification is a pure read of the sweep's existing patterns, the
snapshot still always happens, and the only difference is which directory receives it. An artifact
that is not credential-shaped follows exactly the old path, which the unchanged
`test_amber_edit_snapshots_preimage` and `test_amber_sed_snapshots_target` still assert. The worst
case of a misclassification is a pre-image in the vault instead of the snapdir, with a pointer
naming it — recoverable by path, no data lost.

Rollback is `git revert` of this commit; it touches no state outside the repo other than creating
`~/.secrets/gate-snapshots` on first sensitive snapshot.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
