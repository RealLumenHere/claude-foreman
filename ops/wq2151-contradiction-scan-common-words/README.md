# wq#2151 — common-English dead values matched as prose

The fix lives in `../wq1609-minerva-contradiction-scan/lumen_contradiction_scan.py`
(`COMMON_WORD_VALUES`, `common_word()`, `boundary_re(case_exact=)`); this directory holds
the evidence behind it.

`isis.service_state` closed `UP`, and on 2026-09-20 eight of the nine hits the matcher
produced over the 11,464-document corpus were the English preposition — *"Spin up a
FastAPI orchestrator"* — in research sections that happened to mention Isis and a service
(`corpus_reach_pre_wq1775.txt`). The value was correctly scoped; subject-plus-cue is simply
not a scope for a word that any prose about the subject contains.

## What changed under this ticket

wq#1775 landed its corpus tier between this ticket being filed and being worked, and it
narrowed the same class for the corpus: a 2-4 character all-caps value must be shouted in
the prose too (`SHOUTED_SHORT_RE`, corpus-only). That half is already fixed —
`corpus_reach_main.txt` is main at **0 hits**, and `corpus_reach_branch.txt` is this branch,
also 0: no reach lost, none regained.

What wq#1775 explicitly left alone is the fleet tiers, on the grounds that they were never
measured — *"Fleet artifacts keep the case-insensitive match they were measured with"*.
This ticket is that measurement. `lumen-contradiction-scan --json` over notes and fleet
artifacts, main versus this branch (`fleet_scan_diff.txt`): **32 hits → 28**, four removals,
nothing added.

| removed | why it was wrong |
|---|---|
| `archival_notes:6d29bb85c4b9` `sia.status = 'UP'` | *"- 3 containers up"* |
| `LIVE_FLEET_STATE.md:19,20` `isis.service_state = 'UP'` (info) | the word inside two hardware inventory lines |
| `fleet/inventory/machines/bes.yaml:7` `bes.status = 'DOWN'` | `alert_desc: "… vault standby slot down"` |

All four are false positives; no true positive was lost. The last one is why the rule is a
word list rather than wq#1775's length gate extended: `DOWN` sits in a file the scanner may
open a ticket against, and `ONLINE`, `RUNNING` and `ACTIVE` are past four characters.

Read-only throughout: the corpus session sets `default_transaction_read_only`, the scan was
run without `--apply`, and nothing was written to either store.

Analysis and the rejected options are in `research/PGVECTOR_INVALIDATION_CONTRACTS.md` §8.2.
