# wq#4255 — verify wq#4251: kv-secret sha256:369132a8371a rotation verdict

**Verdict: CONFIRM**

Author: claude-opus-5-5 (wq#4251, PR http://100.121.17.5:3000/lumen/claude-foreman/pulls/2167,
branch `fix/wq4251-rotation-burned-credential-sha256-369132-a1`, merged as `03ce272f` → main)
Verifier: claude-sonnet-5@high (fresh instance, archetype 8 adversarial verify-pass)

## What wq#4251 claimed

`mask_replay.py 4244` flagged one kv-secret (sha256:369132a8371a, prefix
`OPENAI_API_KEY=`, 18 chars) as BURNED. Root cause: wq#4244 ran `wq show 4237`;
wq#4237's detail quoted the Aider config from
`/home/lumen/scratch/research/fleet-infrastructure-playbook.md`, but dropped the
angle brackets around the placeholder `<ops-emergency-key>`, so the kv-secret rule
read the bare words `ops-emergency-key.` (with trailing period) as a value. Not a
real credential: 0 digits, not present in `agent/litellm_config.yaml` or
`agent/*.env` (real master key is 41 chars). Fix: re-bracket wq#4237's ticket
detail (closes the leak path going forward) and pin the value as committed text
in `harness/tests/fixtures/wq4244_values.py` + `harness/tests/test_mask_replay_wq4244.py`
so future `mask_replay` runs read it silently. Nothing rotated — correctly so, per
the PR's own reasoning.

## What I checked

- **Diff scope** (`git -C /home/lumen/foreman show --stat 03ce272f`): two new
  files only — `harness/tests/fixtures/wq4244_values.py` (24 lines) and
  `harness/tests/test_mask_replay_wq4244.py` (37 lines). No changes to
  `masking.py` or `mask_replay.py` themselves — zero blast radius to the
  masking rules other callers depend on.
- **Hash verification**: computed `masking.value_sha12()` on the pinned value
  directly. `whole = "OPENAI_API_KEY=" + "ops-emergency-key."`, prefix
  `"OPENAI_API_KEY="`, body after stripping prefix = `"ops-emergency-key."`.
  `value_sha12(body)` → `369132a8371a`, exactly matching the ticket's hash and
  the pin's sha. The claimed string is literally what hashes to the burned
  value, not an approximation.
- **Ticket-side fix verified live**: `wq show 4237` on this host now shows
  `detail: ...OPENAI_API_KEY=<ops-emergency-key>...` — the angle brackets are
  back, matching the PR's claimed remediation of the leak path. (Did not read
  `agent/litellm_config.yaml`/`agent/*.env` or the real master key myself —
  those are credential files the PreToolUse guard denies reading outright, and
  instinct 4 bars printing credential-shaped material; I verified everything
  else and treat that specific sub-claim as the original author's domain,
  consistent with kb/ISSUE-LOG.md row 1426 already recording it as SOLVED.)
- **Ran the shipped tests**: `pytest tests/test_mask_replay_wq4244.py -v` → 2/2
  pass, including `test_wq4244_corpus_burned_nothing`, which is **not**
  skipped on this host (wq#4244's run dir exists here) — it scores the live
  transcript corpus, not just the fixture.
- **Live replay, independent of the test**: ran `python3 mask_replay.py 4244`
  directly. Output: `1 credential-shaped values in the run's model context, 0
  unaccounted`, with the one value shown as `accounted: harness/tests/fixtures/wq4244_values.py:21`.
  This matches the PR's claim via a path that doesn't go through the pinned
  test at all.
- **Falsifiability check (wq#2630)**: in my verify worktree, edited the pinned
  sha in `wq4244_values.py` from `369132a8371a` to `000000000000` and reran —
  both tests failed as expected (`AssertionError: ['000000000000']` / mismatched
  frozenset). Reverted the edit (`git diff` on the file is empty afterward,
  confirmed clean). The probe can fail; it isn't tautological.
- **No row-collision risk**: kb/ISSUE-LOG.md row 1426 is already filed for this
  exact hash/ticket pair with status SOLVED, consistent with the PR, and no
  renumbering was needed in this verify pass.
- **Full-suite regression check**: `pytest tests/test_mask_replay.py
  tests/test_mask_replay_wq4244.py -q` → 99 passed, 1 failed, 25 skipped.
  The one failure (`test_wq2278_unaccounted_values_are_fixtures_off_its_own_branch`)
  is unrelated to this diff — it scores wq#2278's own live corpus against a
  separate fixture map (`WQ2278_BRANCH_FIXTURES`, test_mask_replay.py:1193)
  that the file's own wq#2765 comment already documents as something that
  drifts with no code change; a new sha (`9f656ba515b4`) surfaced there since
  it was last pinned. wq4251's diff touches none of that (only the two new
  wq4244 files). Logged as kb/ISSUE-LOG.md row 1430, OPEN, out of scope for
  this verify-pass.

## Conclusion

The diff is additive-only (two test files, no rule changes), the pinned hash is
mathematically exact for the claimed plaintext, the ticket-side leak path is
confirmed re-bracketed on this host, the shipped test passes against the live
(unskipped) corpus, an independent direct `mask_replay.py 4244` run confirms
"0 unaccounted" outside the test harness, and the acceptance probe demonstrably
fails under a deliberately wrong pin. No contradiction found; no escalation to
opus-5@high needed.
