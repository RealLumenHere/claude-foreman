# wq#1614 — contradiction penalty in `search_v3`

Source: `research/MINERVA_STALENESS_REVIEW.md` §3.7 (fix #9). wq#1610 detects notes that
assert a `state_facts` value we have since closed and writes them to `note_contradictions`.
Detection alone changes nothing a caller sees — the stale note still ranks where it ranked.
This adds the demotion.

Minerva is not a lane repo, so the change ships here as an ops package: a patch against the
mirror, the gate that must pass before it lands, and the install that runs the gate first.

## What this adds

- `lumen_recall2.py` — the patched file (`lumen_recall2.wq1614.diff` is the change against
  minerva `42d8c2a:lumen_recall2.py` @ `d185ce3c…`). A note carrying an OPEN
  `note_contradictions` row **that the query asks about** has its fused score multiplied by
  `MEMORY_V4_W_CONTRA` (default `0.5`; `1.0` = off).
- `contra_penalty_eval.py` — `reach` (index-coverage diagnostic, no DB) and `gate` (the
  adoption gate: two `recall_bench.py` runs over the frozen golden set).
- `test_contra_penalty.py` — 18 tests, self-contained, no `minerva.db`.
- `install.sh` — drift gate → reach → unit tests → **golden-set gate on the live corpus** →
  only then copy the file in.
- `reach_20260913.json` — the reach diagnostic as run on 2026-09-13.
- `REPORT.md` — evidence, and what is still unmeasured and why.

## Design

**Soft, not a filter.** A contradicted note is usually a good procedure wrapped around one
dead fact (*"ssh nephthys at 100.98.74.87, then …"*). The procedure is still the best answer
we have; dropping the note loses it. `0.5` demotes, cannot make a note unreachable, and
preserves the relative order of the contradicted set among itself
(`test_penalty_is_soft_not_a_filter`).

**Query-scoped (wq#3017).** The first version multiplied every contradicted note on every
query, and the golden gate rejected it (`gate_20260928_134734.json`: R@1 0.715→0.707,
knowledge-update −16.6pt). qid 142's gold note carries an open nephthys/`ts_ip` row. The
query is about re-provisioning with a fleet auth key and never mentions nephthys, but it lost
its rank-1 answer anyway. A row now fires only when the query names its **subject** and
either a word of its **predicate** or its **dead value** (`_asks_about`). Subject alone
is not enough: *"ssh into nephthys"* wants the procedure. Predicate or value alone is not
enough either: `status` and `dead` are ordinary English. On the frozen golden set the rule
fires on 4 of 257 queries (qids 11, 12, 195, 235), and the blanket version left all four at
the same rank. qids 142 and 236 match no row (`test_real_fixture_scoping_on_the_rejected_qids`).

**Not the freshness prior returning.** `W_REC` stays `0.00` — blanket freshness was measured
to cost recall (the wq#574 block in `lumen_recall2.py`). This fires only on notes we hold
positive, row-level evidence about. Asserted by `test_w_rec_stays_zero`.

**`1.0` is off, and off is free.** At `1.0` the contradiction query is not issued at all and
the scoring expression is untouched. `test_w_contra_1_matches_unpatched_mirror` loads the
*unpatched mirror* module alongside the patched one and compares their output on the same
corpus — the identity claim is demonstrated against the real previous code, not against a
re-implementation of it.

**Fail-open.** A missing `note_contradictions` table, an unrecognised schema, or any
`sqlite3.Error` yields the empty set and leaves every score alone. A detector outage must not
reorder recall. The resolution column is introspected (`resolved_at`, else `cleared_at`)
because wq#1610 shipped it as `cleared_at` and the live export calls it `resolved_at` — a
hardcoded wrong name would raise, get swallowed by the fail-open, and read as *"measured,
moved nothing"* when the penalty never ran at all.

**Salience is outside the multiply.** The wq#576 boost is a usage term, not evidence about
the note's content; scaling it here would make the penalty's size depend on how often the
note happens to be read.

## Environment

| var | default | meaning |
| --- | --- | --- |
| `MEMORY_V4_W_CONTRA` | `0.5` | multiplier on a contradicted note's fused score; `1.0` = off |
| `MEMORY_V4_CONTRA_FIXTURE` | *(unset)* | read the contradiction set from a frozen JSON export instead of the live table — so a benchmark measures a fixed set, not whatever the hourly scan wrote that minute |

A demoted hit carries `_contra` in its dict and `contra=x0.5` in the CLI probe output.

## Install

```sh
ssh lumen@100.117.66.102 '/home/lumen/foreman/ops/wq1614-search-v3-contradiction-penalty/install.sh'
```

The gate runs `recall_bench.py` twice against the *staged* file (never the live one) — once
at `W_CONTRA=1.0`, once at the candidate weight — and aborts with the live file untouched on
any of: a rise in `trap_at1_rate` (wq#595), a control-gate failure, a drop in abstention
pass-rate, a drop in headline R@1 or MRR, or any headline class losing more than 2pt R@1.

Set `W_CONTRA=…` to gate a different weight. `install.sh` refuses (exit 3) if the live file
has drifted from the recorded patch base — re-derive the patch rather than clobbering
another lane's change.

## If the gate passes but `trap@1` does not move

Per the ticket, that verdict is about the **index**, not the weight: the contradiction scan
is not reaching the notes the trap rows are made of. Open a follow-up against the wq#1610
scan's coverage. Do **not** retune `W_CONTRA` — see `REPORT.md` §3 for why that is the
likeliest outcome and what coverage is missing.
