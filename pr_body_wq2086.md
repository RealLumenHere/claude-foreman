# Presentation-stability probe for the Minerva verify gate

Minerva proposal `d653cc13c6a3`, factory plan `8b4eafb63f5d` (RED / unplannable, so this is
a hand-built change, not a factory apply). Source: [arXiv 2609.16517] *Competence-Preserving
Resume Perturbations Expose Presentation Sensitivity in LLM Screening*.

## What

`ops/wq2086-presentation-stability/` — a timer-driven probe that measures whether the
Minerva accept/reject gate flips on presentation alone, plus its systemd units, an
`install.sh` answering the wq#1696 `--verify` contract, a README and a 22-case acceptance
suite.

## Why

`minerva_verify.verify()` decides accept/reject for every Minerva proposal. Every existing
harness varies something other than the input: DGM v2 `paired_eval` varies the agent,
`pelican_bench` tracks local-model drift, `model_audit` external rung correctness,
`predicate_audit` reasoning grounding, `judgearena_bench` judge-vs-human agreement,
`cross_agreement.py` inter-rung agreement. None asks whether the same evidence, worded
differently, still passes. The paper's finding is that screening models flip on
presentation; this measures whether ours does.

## How

Per item (one already-scored `verification_log` row = answer + the context it was scored
against): re-score the untouched answer twice (baseline + **control**, so the rung's own
nondeterminism is measured rather than mistaken for sensitivity), then re-score K
gate-admitted surface rewrites. Headline per rung:
`excess_flip_rate = flip_rate - self_flip_rate`. Emitted to stdout/journal, `--json`,
Langfuse (one span per rung, best-effort) and ntfy above threshold.

The competence-preserving gate is deterministic — no model judges whether a variant is
fair. Tier A requires an identical multiset of evidence tokens (urls, CVE/GHSA ids, `wq#`
refs, paths, filenames, numbers, ALLCAPS identifiers, quoted spans). Tier B requires an
identical multiset of content words after canonicalising a *declared* synonym table;
negations, modals and quantifiers are deliberately not stopwords, so dropping a "does not"
or softening it to "may not" is rejected as a competence change. `reorder` additionally
declines any text whose sentences open with a back-reference ("None of these currently test
…" — the shape real eval items take), because rotating that changes what the text asserts.

Two things worth a reviewer's eye:

1. **`verify()` writes.** It appends a row to `verification_log` on every call. A probe that
   ignored that would feed its own perturbed variants back as next week's "already-scored
   items". The probe redirects `minerva_verify.DB` to a temp sqlite it deletes on exit, tags
   subjects `pstab:*`, and excludes `subject LIKE 'pstab:%'` from selection.
   `test_probe_never_appends_to_the_live_verification_log` imports the live module and
   asserts the live row count is unchanged.
2. **Rung choice.** The default is today's production judge (`VERIFY_JUDGE_MODEL`,
   `qwen36-local`), not the rung each row was historically scored on: pre-wq#1501 rows carry
   the paid `deepseek-v4-flash`, and the standing constraint is no paid-model spend on the
   judge. Sized for that CPU rung — weekly, `Nice=15`, 3 items × (2 baselines + 2 variants)
   = 12 scores, hard-capped at `PSTAB_MAX_SCORES=20`.

## Testing

```
pytest -q ops/wq2086-presentation-stability/test_presentation_stability.py   # 22 passed
python3 .../minerva_presentation_stability.py --selftest                      # selftest OK
python3 /home/lumen/bin/minerva-presentation-stability --dry-run
  qwen36-local: items=3 scored=0 admitted=6 variants=0 gate_rejected=0 errors=0
```

Deployed by hand under change-safety (verify → precheck → apply → health):
`/home/lumen/bin/minerva-presentation-stability` is live and byte-identical to the bundle;
selftest and live-corpus `--dry-run` both pass through the installed copy.

What is **not** done, deliberately:

- The two units are not installed: `/etc/systemd/system` needs root, which this lane does
  not have. Until then nothing schedules the probe and `install.sh --verify` reports the
  missing units (`MISS absent: …`, `MISS not enabled`) — honest by design. The owed commands
  are printed by `install.sh` and listed in the README.
- No **scored** run yet: the judge key lives behind a guard-denied path, so no judge call
  was possible from the build session. All evidence above is model-free. See
  `ISSUE-LOG-CANDIDATE.md` rows 449/450 (which also record that the change-safety backup dir
  `/home/lumen/backups` is not grantable to this lane).

## Risk / rollback

Risk is small and bounded: nothing is scheduled until a privileged step installs the timer;
the probe is read-only against the eval corpus (`mode=ro` URI plus the `DB` redirect above),
writes no state, and changes no schema. Its only cost is judge calls on the free local rung,
capped by `PSTAB_MAX_SCORES`. `PSTAB_ENABLED=0` makes the unit a no-op.

Rollback:

```bash
systemctl disable --now lumen-presentation-stability.timer
rm -f /etc/systemd/system/lumen-presentation-stability.{service,timer}
rm -f /home/lumen/bin/minerva-presentation-stability
systemctl daemon-reload
```

🤖 Generated with [Claude Code](https://claude.com/claude-code)
