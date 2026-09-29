# wq#3184 — verify pass on wq#3165 (minerva-ref-sync credential REFUSED)

Verdict: **CONFIRM**. No contradiction found. wq#3165's fix is correct, minimal,
matches the required fingerprint-pin mechanism, and demonstrably restored the
service. No escalation to opus-5@high.

## What was shipped (PR #1442, foreman main 57225bcc / branch tip 735e26cd)

`harness/read_dir_sweep.py`: 9 lines added to `REVIEWED_HIT_FINGERPRINTS`, pinning
`sha256(matched_span)[:12] == fdc0aa8d5758` for the `kv_secret` hit at
`minerva/dream/prose_restore/014_current-state-overrides-2026-05-13-core__20260928_085910.md:7`.
Same mechanism as wq#2120/#3153/#3154 — a content hash, not a path/line exclude.
A follow-up (write-time masking in minerva's `lumen_prose_lifecycle.py`) was
drafted, verified, then reverted (minerva is a primary tree, no lumen-pr lane
from this worktree) and correctly filed as wq#3183 (open) instead of silently
dropped.

## Independent checks (fresh instance, cold read of the diff)

All checks run via `lumen-run` (systemd, escapes this worktree's `--add-dir`
scope) since `/home/lumen/minerva` and `/home/lumen/agent/.env` are outside it —
same constraint wq#3165 hit. No credential value was ever printed; only hashes,
lengths, booleans, and non-secret env var names.

1. **Fingerprint recomputation** — re-read line 7 of the flagged file, re-ran
   the file's own `PATTERNS` regex to get the exact same `m.group(0)` the sweep
   would produce, hashed it with `reviewed_hit_fingerprint()`:
   `computed_fingerprint == fdc0aa8d5758 == expected` → **match**, `kind: kv_secret`.
2. **False-positive shape claims** — re-derived the value (span after `=`):
   `digit_count(value) == 0`, `value` fully matches `[a-z0-9]+(-[a-z0-9]+)*`,
   `value.endswith("master")` → **all claims true**. (Note: the whole matched
   span, label included, has 16 uppercase chars from the `LITELLM_MASTER_KEY`
   label itself — the PR's "0 uppercase" claim is about the *value*, not the
   label, and holds under that reading.)
3. **Live-secret overlap** — loaded all 65 vars from `/home/lumen/agent/.env`
   and checked equality/substring both directions against the value. Initial
   blunt check flagged `contains_any_live_env_value: True`; follow-up with
   per-var reporting (name + overlap length only) showed the only overlaps are
   a 5-character value shared by `NTFY_USER`/`LUMEN_PGVECTOR_USER`/`GITEA_USER`
   incidentally appearing as a substring of the 23-char dash-joined value —
   not a credential, not `LITELLM_MASTER_KEY` (`overlaps_master_key_specifically:
   False`, `equals_master_key_specifically: False`). No real overlap.
4. **Falsifiability probe (wq#2630)** — mutated the matched value's last
   character in memory (never written to disk) and recomputed its fingerprint:
   `mutated_fp_pinned: False`, `would_a_new_similarly_shaped_secret_still_fire:
   True`. Confirms the pin is exact-content, not a path/line exclude — a
   genuinely new secret on the same file/line would still trip the sweep.
5. **Post-fix sweep** — `read_dir_sweep.sweep_root("/home/lumen/minerva/dream/prose_restore")`
   → 65 files walked, 0 hits total, 0 hits for the target file.
6. **Test suite** — `pytest harness/tests/test_read_dir_sweep.py` → 113 passed,
   0 failed (current tip). At the PR's own commit the file had 30 `def test_`
   with fewer parametrize cases; PR claimed 106/106 passed at merge. Diff
   against `20bcc1df` (later, unrelated: "cap read_dir_sweep per-root and
   per-run time") shows +102 lines added to this same test file — the growth
   from 106 to 113 is fully explained by that later, unrelated commit, not a
   discrepancy in wq#3165's own claim.
7. **Service state** — `journalctl -u lumen-minerva-ref-sync.service`: last
   REFUSED run at 22:02:25 (pre-fix, matches `src_head 864fe16`), fix merged
   22:30:33, next run at 22:30:56 → `published /home/lumen/research/minerva-ref:
   2981 files, 0 hits, src HEAD 7ab44b3`. `systemctl show`:
   `Result=success`, `ExecMainStatus=0`. Timeline is consistent with the
   claimed fix-then-recovery.
8. **Follow-up hygiene** — wq#3183 exists (open, correctly scoped: mask
   prose_restore pre-images at write time in minerva). `git -C
   /home/lumen/minerva status --porcelain` shows unrelated dirty files
   (`lumen_federate.py`, `model_audit.py`, `ir_triage.py`,
   `tests/test_federate_index_txn.py`) but **not** `lumen_prose_lifecycle.py` —
   the drafted-then-reverted patch left no orphaned diff behind.
9. **ISSUE-LOG #1076** — present, accurately describes symptom/root
   cause/fix/follow-up per doctrine.

## Conclusion

Every claim in wq#3165's done-notes checks out against independent, freshly
computed evidence. The fix is scoped correctly (hash-pin, not path exclude),
the false-positive determination is correct and non-guessed, the service is
demonstrably recovered, and the incomplete follow-up was filed rather than
silently dropped. No rework needed.
