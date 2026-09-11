# Embedder convergence (wq#1639) — measurement protocol, and why it did not run

Status: **NOT RUN.** wq#1639 asked for a measured adopt/reject between two embedder
designs. No worker on the foreman BUILD profile can execute it today (§4). This file is
the executable protocol plus the two preconditions, so the lane that does have the access
runs it once instead of re-deriving it.

Sources: `research/MINERVA_PIPELINE_MAP.md` §1.7, §1.9d, §2.1, §4.7, §5#14, §6 (wq#1626);
wq#595 (scoring semantics, `done`, report `/home/lumen/minerva/v4/reports/595_scoring.md`).

---

## 1. The question

`context_selector` ranks `sections.json` with **bge-m3**. `search_v3` ranks
`archival_notes`, `federated_chunks` and the research index with **nomic-embed-text** 768d.
Two resident Ollama models and two vector formats over overlapping text, and
`federated_chunks` exists only because `search_v3` cannot read `sections.json`
(MAP §2.1:225, §1.7).

- **(a)** Re-embed `sections.json` with nomic; `search_v3` reads sections directly;
  `federated_chunks` retires, its chunking + header-context kept as the section chunker.
  One model, one vector format.
- **(b)** Keep bge-m3 for sections; keep 1.7 as the bridge; re-trigger it (§4.4).

MAP §4.7: "(a) is cleaner; (b) is cheaper this month. Either is fine; two embedders with
one of the two indexes dead is not."

## 2. Scoring — fixed by wq#595, not re-negotiable per arm

Corpus: `golden_v4_frozen_20260814.jsonl`,
sha256 `8ba5afb0ce12585606b8dcff5ffb8245af24b0f0f32ccbddf3d4121f1bbf46b7`.
Cite the sha in every artifact; if the file's sha differs, stop — the set is frozen.

| metric | definition | role |
|---|---|---|
| headline R@1 / MRR | **retrieval classes only**; abstention excluded | adopt signal |
| abstention pass-rate | reported separately, never folded into headline | guard |
| federated coverage | reported separately | guard |
| `trap_at1_rate` | trap note served at rank 1 = MISS for contradiction-resolution / knowledge-update | **disqualifier** |
| ORIG-193 subset | headline over the original 193 qids | time-series continuity |

wq#595 (1) exists because `run_query()` set `eff_rank=1` on abstention PASS, inflating
headline. Confirm that is still fixed in whatever `recall_bench.py` this run uses before
trusting a number.

**Adopt rule.** An arm ships only if headline ≥ baseline **and** `trap_at1_rate` does not
rise. A `trap_at1_rate` rise disqualifies the arm outright, whatever it does to headline —
Frank's correctness axis is stale *detected, not served*. `W_REC = 0.00`, the frozen set +
sha, and the trap@1 alarm are MAP §6 invariants: "every retrieval change above is measured
against them or does not ship."

## 3. Procedure

**Baseline (arm 0).** Current fleet as-is. Record headline, abstention, federated coverage,
trap@1, ORIG-193, plus `context_selector --rank` output for the probe set. This is the
comparison floor; do not reuse an older stored baseline, re-run it.

**Probe set.** 29 core sections + 20 sampled non-core, per the ticket. The core count is
load-bearing and known: 279 sections, 29 core = 11,056 tok (MAP appendix). Freeze the 20
sampled qids to a file with its own sha before running either arm, or (a) and (b) are not
comparable. Sample from non-core so the probe set exercises §2.1 step 5c (hybrid-ranked,
budget-clipped), which is where ranking quality actually shows.

**Arm (a).** Re-embed `sections.json` with nomic-embed-text 768d, keeping `lumen_federate`
chunking + header context as the section chunker. Point `search_v3` at sections. Re-run §2
and the probes. Note: this **writes** the live boot-path index — snapshot `sections.json`
first and have the restore tested, since `--core` boot reads it unconditionally.

**Arm (b).** Keep bge-m3. Re-trigger federated per §4.4. Re-run §2 and the probes.
Arm (b) is not measurable as the fleet currently sits (§5).

**Report.** `/home/lumen/minerva/v4/reports/1639_embedder_convergence.md`, with the golden
sha, the probe-set sha, all five metrics per arm, and the adopt decision stated against §2.

## 4. Precondition 1 — capability (blocking)

Every artifact is under `/home/lumen/minerva`, which `harness/fleet-exec-policy.yaml:67`
deliberately keeps off `allow.read_dirs` ("credentials mirrored into context/sections,
wq#1467"); `harness/tests/test_guard.py:111-116` asserts guard.sh denies `minerva/context`
reads. Confirmed empirically from this worktree: `ls /home/lumen/minerva` and
`grep -c "" /home/lumen/minerva/eval/golden_v4_frozen_20260814.jsonl` are both refused.

Needed: `context/sections.json` (read **and rewrite**), `context_selector.py`,
`tag_sections.py`, `lumen_federate.py`, `lumen_recall2.py`, `eval/`, `minerva.db`, and
**write** to `v4/reports/`.

**wq#1738 does not unblock this.** That ticket grants `minerva/eval` +
`lumen_recall2.py` for wq#1614, and is explicitly shaped to keep `minerva/context`
excluded. wq#1639 needs `context/sections.json` — the exact credential-mirror surface the
exclusion exists for — and needs to write it, not just read it. A read grant of any shape
is insufficient.

The cheap route is therefore **not** a wider grant: run wq#1639 from a lane that already
holds the minerva root, or fix wq#1467 (stop mirroring credentials into `context/sections`)
so the exclusion can be narrowed honestly. Widening a BUILD profile onto the credential
surface to run one measurement is the wrong trade.

## 5. Precondition 2 — the stores are not stable yet (blocking)

MAP §5 sequencing: "9, 11, 14 reshape the boot paths and go after the stores they read are
stable." wq#1639 is #14.

- **#11 / wq#1637 is `claimed` right now** — core prose migration, rules → instincts,
  procedures → playbooks, stubs deleted, target 11K → ≤8K core. That rewrites
  `claude_memory.md`, hence `sections.json`, hence both the corpus being embedded *and*
  the 29-core probe set. A measurement taken now is stale the moment wq#1637 lands.
- Arm (b) is currently unmeasurable on its own terms: `federated_files` holds **3 rows,
  all indexed 2026-08-16**, and `MEMORY_V4_FEDERATED` defaults to `'0'` in
  `lumen_recall2.py` with the env unset in `agent/.env`, so the tier is OFF for lanes and
  CLI (MAP §1.7:101, appendix). Scoring (b) today measures a dead index and would hand
  arm (a) an unearned win. A fair (b) needs the re-trigger from wq#1633 (`done`) to have
  actually run *and* the flag flipped — and `ops/wq1633-.../README.md:58-62` says not to
  flip it before a re-index plus a golden run against sha `8ba5afb0`.

Order: land wq#1637 → re-index federated → then run this protocol against a re-generated
`sections.json`.

## 6. One design note the measurement should not have to discover

Arms (a) and (b) are not symmetric in what they leave behind. (a) removes a store
(`federated_chunks`), a model, and a vector format, and deletes the reason 1.7 exists.
(b) keeps three things alive to avoid re-embedding 279 sections once. If the two arms
score within noise on headline and neither raises trap@1, §4.7's own framing decides it:
prefer (a). Record that as the tiebreak in advance so it is not litigated after the
numbers land.
