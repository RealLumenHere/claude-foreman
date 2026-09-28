# wq#1614 — contradiction penalty: evidence

Golden set: `/home/lumen/minerva/eval/golden_v4_frozen_20260814.jsonl`
sha256 `8ba5afb0ce12585606b8dcff5ffb8245af24b0f0f32ccbddf3d4121f1bbf46b7` (257 rows, verified
by `contra_penalty_eval.py reach` — `reach_20260913.json:"golden_sha256_ok": true`).

Contradiction fixture: `/home/lumen/minerva/eval/fixtures/note_contradictions_20260913.json`
sha256 `d0df678036066b066627cbc04962387949fcba92a94eb99ea9e5a8df82499633`.

## 1. Baseline

From the last scheduled bench, `eval/results_sched_20260912_1041.json` — same frozen golden
sha, `control_ok: true`:

| metric | value |
| --- | --- |
| `trap_at1_rate` | **0.250** (4 / 16 trap rows) |
| `trap_at1_qids` | 230, 238, 241, 243 |
| `penalized_qids` | 230, 231, 235, 238 |
| headline | R@1 0.753 · R@5 0.904 · R@10 0.925 · MRR 0.817 (n=239) |
| abstention pass-rate | 0.500 (failed: 240, 241, 243) |

`.250` is the number this ticket exists to move.

## 2. What the penalty can and cannot do to `trap@1`

The multiply is monotone non-increasing on a contradicted note's score and touches nothing
else. So:

- `trap@1` **falls** when a trap note sitting at rank 1 carries an open contradiction.
- `trap@1` **rises** only in one shape: the rank-1 note is *correct* and contradicted, gets
  demoted, and a trap note moves up into rank 1. That is the entire risk surface, and the
  gate in `install.sh` enumerates it directly by diffing `trap_at1_qids` between the two
  runs (`checks[].trap_at1_no_rise` in the gate artifact).

Softness matters for exactly that risk: at `W_CONTRA=0.5` a demoted note falls by half its
score, not out of the result set, so the blast radius is bounded to reordering.

## 3. Reach: does the index touch the trap rows at all?

This is the diagnostic the ticket asks for first. `reach_20260913.json`:

- The export carries **20 rows over 17 distinct notes**, all open (`resolved_at: null`).
- Subjects covered: `gameboy-pc`, `hathor`, `isis`, `llama33-local`, `lumen`, `nephthys`,
  `sia`, `thor`. Predicates: `fan_status`, `ollama_version`, `power_plug`, `status`, `ts_ip`.
  11 of the 20 rows are one repeated fact — `llama33-local status LIVE-VERIFIED → TORN-DOWN`.
- The golden set has **16 trap rows**. Exactly one (qid 235, *"is ollama-embed still installed
  on lumen"*) identifies its trap by note id (`d60d852d`); that note is **not** among the 17
  contradicted notes. The other 15 identify traps by content substring, which cannot be
  resolved to a note id without reading note content — so `id_typed_traps_in_contradiction_index: []`
  is a **lower bound** on reach, not a proof of zero.
- None of the four `trap@1` queries are about a host status, IP, ollama version, fan or power
  plug. They are: 230 scout-X `ingestion_outage`, 238 the power dashboard (`Grafana`), 241
  restic/pokemon checkpoints (`CRITICAL hourly`), 243 the pgvector migration. The
  contradiction index is a *fleet-hardware-state* index; the trap rows are *project and
  infrastructure narrative* notes.

**Expected outcome, stated before the gate runs:** the gate passes (no rise) and `trap@1`
does not move. Per the ticket that verdict indicts the index, not the weight — the follow-up
is widening the wq#1610 scan beyond the five hardware-state predicates it currently covers,
not retuning `W_CONTRA`.

## 4. What was NOT measured here, and why

The live-corpus `trap_at1_rate` under the penalty is **not** in this artifact. `recall_bench.py`
scores against `minerva.db`, which this build sandbox cannot read (the ticket's own
instruction is *"run the eval against the fixture, never against `minerva.db`"*), and no
replayable per-query candidate dump exists inside the granted roots —
`eval/results_*.json` records ranks only, not hit ids or scores, and the wq#574 feature dump
lives under `/home/lumen/scratch/`, outside the grant.

That measurement is therefore produced by `install.sh` step 4 **before** anything is copied
in, written to `gate_<stamp>.json`, and it aborts the install on any rise. The gate is the
adoption evidence; this document is the design and reach evidence.

## 5. Tests

`python3 test_contra_penalty.py` — **20 passed**, 0 failed, no `minerva.db` access (13 at
wq#1614, 18 after wq#3017's scope tests, 20 after wq#3038's boundary tests — §8).

Load-bearing ones:

- `test_w_contra_1_matches_unpatched_mirror` — loads the unpatched mirror module and the
  patched module over the same temp corpus and asserts identical output at `W_CONTRA=1.0`.
- `test_penalty_halves_only_contradicted_scores` — the contradicted note's score is exactly
  halved; the other two are unchanged to the digit.
- `test_penalty_is_soft_not_a_filter` — with all three notes contradicted, all three are still
  returned, in the same order, with scores > 0.
- `test_cleared_at_schema_is_accepted` / `test_missing_table_fails_open` /
  `test_unknown_schema_fails_open` — schema introspection and the three fail-open paths.
- `test_real_fixture_parses_to_open_note_ids` — the shipped fixture parses to 17 open notes.
- `test_shim_injects_the_staged_module_and_env` — the gate's mechanism (inject the staged
  `lumen_recall2` into `sys.modules` so `recall_bench`'s own import resolves to it, and pass
  `MEMORY_V4_W_CONTRA` / `MEMORY_V4_CONTRA_FIXTURE` through) verified against a stand-in
  bench, since the real one needs the DB.

## 6. Gate result, 2026-09-28 (wq#3004): REJECT, not installed

First live run of `install.sh`. The patch was re-derived onto minerva `42d8c2a` first,
because the 09-13 mirror base had gone stale and shipping it would have reverted wq#2895.
Artifact: `gate_20260928_134734.json`. At `W_CONTRA=0.5`:

- `trap@1` stayed at 0.25 with the same qids. The prediction in §3 held.
- R@1 went 0.715 → 0.707 and MRR 0.793 → 0.787. The `knowledge-update` class lost
  16.6pt and `build` lost 3.1pt. The gate fails on those three checks.
- qid 142 dropped from rank 1 out of top-k. Its gold note `27424d3dad48` is itself in the
  contradiction set (nephthys `ts_ip` 100.98.74.87), which is the "good procedure around
  one dead fact" case from §1. A blanket multiply does not survive it.

`/home/lumen/minerva/lumen_recall2.py` was left untouched. Follow-up is wq#3017: scope the
penalty to queries that ask about the dead fact, or retire the bundle.

## 7. Query-scoped rework, 2026-09-28 (wq#3017): PASS (INERT), installed

A contradiction row now demotes its note only when the query names the row's subject and
either a word of its predicate or its dead value (`_asks_about`; README "Query-scoped").
`W_CONTRA` stays `0.5`. `install.sh` ran unmodified. Artifact: `gate_20260928_140308.json`.

- Every check passed. R@1 0.715, MRR 0.793 and `trap@1` 0.25 are unchanged. No query in the
  239-query headline changed rank in either direction (per-query diff of
  `results_wq1614_w1_20260928_1406.json` vs `results_wq1614_w0.5_20260928_1407.json`),
  and that includes qids 142 and 236.
- On the frozen fixture the rule fires on 4 of 257 golden queries: 11 and 12 (thor fan),
  195 and 235 (lumen ollama). The blanket version had already left all four at the same rank.
- The verdict is INERT, as §3 predicted: the trap@1 queries ask about nothing in this
  hardware-state index. The remedy is widening the index, not the weight. That is filed as
  wq#3030.

`install.sh` installed the file (backup `lumen_recall2.py.bak.20260928_140308_pre_wq1614`),
and `install.sh --verify` returns VERIFY OK. The verify markers are now the scope markers,
so the rejected blanket version fails the check.

## 8. dead_value word boundaries, 2026-09-28 (wq#3038): PASS (INERT), installed

wq#3031's verify-pass CONTRADICTED §7. The three probes above all pass, but they cannot see
the defect they were asked to cover: `_asks_about` matched `subject` on word boundaries and
`dead_value` as a raw substring (`dv in q_lower`, a `len >= 2` floor and nothing else), and
**the golden gate is structurally blind to the difference** — the rule fires on 4 of 257
golden queries (§7) and none of the four carries a short dead value, so PASS/INERT was
evidence about those four queries, not about the matching rule. That is the probe-can't-fail
trap of wq#2290/wq#2630. Two of the fixture's own rows leak into ordinary English:
`thor`/`fan_status`/`dead` fires on *"thor migration deadline notes"* and *"thor deadlock
investigation"*; `sia`/`status`/`UP` on *"sia backup schedule"*, *"sia group config notes"*,
*"sia support ticket triage"*. None asks about the dead fact; each one demotes the note anyway.

Decision: option (a), tokenize. `dead_value` is now tokenized by the same `_words()` the
subject arm uses and must appear as whole words, contiguously for multi-word values
(`_phrase_in`). `W_CONTRA` stays `0.5` — untouched, per the ticket. Option (b) (accept as a
bounded known limitation) was rejected: the demotion is soft, but the qid 142 failure §6
recorded was also only a demotion, and it cost a rank-1 answer.

Evidence, cheapest first:

- **Per-query diff over all 257 golden queries** (`_contradicted()` under the frozen fixture,
  shipped file vs fixed file): **0 changed**, fire count 4 → 4. The 7 leak queries above all
  go from firing to empty; *"is the thor fan dead"*, *"is sia up"*, *"what is nephthys's
  tailscale ip"* and *"is llama33-local still live-verified"* are unchanged.
- **Unit tests**: 20 passed (was 18). The two new ones —
  `test_asks_about_dead_value_word_boundaries` and
  `test_real_fixture_no_false_positive_on_english` — **fail against the shipped wq#3017
  file** and pass against the fix, so the probe can fail. Their input comes from the frozen
  fixture, outside the golden corpus the rule was tuned on.
- **Golden-set gate**: `gate_20260928_142606.json`, all 8 checks `ok`, `pass: true`. R@1
  0.711, MRR 0.791, `trap@1` 0.25 → 0.25 (n=16), abstention 0.5 — identical baseline and
  treatment, as the per-query diff predicted. Verdict INERT, same as §7.
- **`install.sh --verify`**: VERIFY OK (exit 0), with a new marker `^def _phrase_in` that
  **fails against the pre-fix live file** (`MISS marker ... VERIFY FAIL (1)`).

`install.sh` ran end-to-end and installed the file (backup
`lumen_recall2.py.bak.20260928_142606_pre_wq1614`); live and staged are byte-identical at
`46692ecf…`. To get there, `BASE_SHA` was moved forward to the file wq#3017 installed
(minerva `a9290e8`, `e5e15fcd…`) — the step-1 drift gate is also a re-entrancy lock, so a
second patch of the bundle exits 3 before reaching the gate until the base is re-recorded,
which is what install.sh's own refuse-message says to do. The wq#3038 delta ships as
`lumen_recall2.wq3038.diff`; `lumen_recall2.wq1614.diff` still holds the whole change from
the pre-wq1614 base that `test_contra_penalty.py` pins its identity test to. Logged as
ISSUE-LOG rows 995 and 996.

The INERT verdict is unchanged and so is the §7 conclusion: the remedy for `trap@1` is the
contradiction index (wq#3030), not the weight.
