# wq#2086 — presentation-stability probe for the Minerva verify gate

Minerva proposal `d653cc13c6a3`, plan `8b4eafb63f5d` (RED / unplannable — hand-applied).
Source: [arXiv 2609.16517] *Competence-Preserving Resume Perturbations Expose
Presentation Sensitivity in LLM Screening*.

## What gap this closes

`minerva_verify.verify()` is the accept/reject gate on every Minerva proposal: it scores
a claim set against a fact-pack context through a LiteLLM judge rung. Every existing
harness holds the *input* fixed and varies something else — DGM v2 `paired_eval` varies
the agent, `pelican_bench` tracks local-model drift, `model_audit` external rung
correctness, `predicate_audit` reasoning grounding, `judgearena_bench` judge-vs-human
agreement, `cross_agreement.py` inter-rung agreement. None of them asks whether the same
evidence, worded differently, still passes. The paper's finding is that screening models
flip on presentation alone; this probe measures whether ours does.

## Method

Per already-scored item (one `verification_log` row = an answer plus the context it was
scored against):

| step | calls | why |
| --- | --- | --- |
| baseline | 1 | re-score the untouched answer on the rung, this run |
| control | 1 | re-score it again — disagreement here is the rung's own nondeterminism |
| variants | K | K gate-admitted surface rewrites; flip = `passed != baseline.passed` |

Headline metric per rung: **`excess_flip_rate = flip_rate - self_flip_rate`**. Without the
control, a temperature-sampled judge's own noise would read as presentation sensitivity.
Also reported: `mean_abs_delta_final`, `max_abs_delta_final`, `gate_rejected`, `errors`.

### Competence-preserving gate

Both tiers are deterministic — no model decides whether a variant is fair. A variant
failing either tier is discarded and never counted as a flip:

- **A — evidence tokens identical as a multiset**: urls, CVE/GHSA ids, `wq#` refs, absolute
  paths, filenames, numbers/versions/percents, ALLCAPS identifiers, quoted spans.
- **B — content words identical as a multiset** after canonicalising the *declared*
  synonym/contraction table (`SYNONYMS`). Negations, modals and quantifiers are **not**
  stopwords, so dropping a "does not" or softening it to "may not" fails tier B: that is a
  competence change, not a presentation one.

### Transforms (surface only, deterministic in the item id)

`reflow` (wrap/unwrap), `bulletize` (prose ↔ bullets), `punctuate` (dash/semicolon/
emphasis/typewriter spacing), `resplit` (comma clause → sentence, parenthetical →
apposition), `reorder` (sentence rotation — **declined** when any sentence opens with a
back-reference, e.g. "None of these currently test …", because rotating that changes what
the text asserts), `synonym` (declared equivalences only).

## Read-only contract

- items are read over a `file:…?mode=ro` sqlite URI;
- `verify()` **appends a row to `verification_log` on every call**, so the probe points
  `minerva_verify.DB` at a temp sqlite it deletes on exit. Without that redirect the
  probe's own perturbed variants become next week's "already-scored items" and the corpus
  eats itself. Item selection also excludes `subject LIKE 'pstab:%'` as a second fence.
  `test_presentation_stability.py::test_probe_never_appends_to_the_live_verification_log`
  pins this against the live module;
- `VERIFY_ALERT=0`: a judge error inside the probe must not page as a production judge
  outage. Errors are counted and reported here instead.
- no schema change, no state file, nothing written outside the temp dir.

## Rung and cost

Default rung is **today's production judge** (`VERIFY_JUDGE_MODEL`, `qwen36-local`), not
the rung a row was historically scored on — pre-wq#1501 rows carry `deepseek-v4-flash`,
which is paid, and the standing constraint is no paid-model spend on the judge. `--rungs`
overrides, visibly. `qwen36-local` is a CPU llama-server on Isis that also serves LPP, so
the unit runs weekly at Mon 03:20 UTC, `Nice=15`, and the defaults are small: 3 items ×
(2 baselines + 2 variants) = 12 scores, hard-capped by `PSTAB_MAX_SCORES=20`. The context
(fact pack) is identical across every call in an item, so the rung's prefix cache stays
warm.

## Output

stdout report (journal), `--json` for machines, Langfuse trace `presentation-stability`
(one span per rung, best-effort per the `harness/langfuse_client` contract), and ntfy when
`excess_flip_rate >= PSTAB_FLIP_THRESHOLD` (0.25) or when the run scored nothing. A
finding exits 0: it is an alert, not a unit failure for the healer to retry. `--fail-on-
finding` flips that for CLI use.

## Install / verify / rollback

```bash
bash ops/wq2086-presentation-stability/install.sh            # script half is unprivileged
bash ops/wq2086-presentation-stability/install.sh --verify   # wq#1696 contract
```

`install.sh` runs verify → precheck → backup (`/home/lumen/backups/wq2086`) → apply →
health (selftest + `--dry-run` against the live corpus) → revert on fail. The unit half is
privileged: without write access to `/etc/systemd/system` it prints the owed commands and
exits 3, leaving the script live and unscheduled — `--verify` then reports the missing
units honestly.

Rollback:

```bash
systemctl disable --now lumen-presentation-stability.timer
rm -f /etc/systemd/system/lumen-presentation-stability.{service,timer}
rm -f /home/lumen/bin/minerva-presentation-stability
systemctl daemon-reload
```

## Tests

```bash
pytest -q ops/wq2086-presentation-stability/test_presentation_stability.py   # 22 passed
python3 ops/wq2086-presentation-stability/minerva_presentation_stability.py --selftest
python3 ops/wq2086-presentation-stability/minerva_presentation_stability.py --dry-run
```

`--selftest` is network-free and is also the install health check. `--dry-run` shows gate
admissions and rejections against the live corpus without scoring anything.
