# PILOT 132cba7d6594 — REPORT

ASIRF-style domain-conditional redaction KB as a pre-inference filter in Minerva.
Ticket wq#3433, worktree wq3433-a1, run 2026-10-03 against foreman `81978815`.

## Outcome

Stopped on the card's second kill criterion: **baseline does not reproduce**. Treatment was not run,
as the card requires ("stop, report the baseline defect. Do not proceed.").

## Baseline defect

None of the Baseline commands can run. Every input they name is missing on hathor, the node the card names,
and the four sweeps do not have the CLI the card assumes.

| Card input | What exists | Evidence |
|---|---|---|
| `/var/log/lumen/transcripts/*.jsonl` (200-transcript sample) | absent on hathor; `/var/log/lumen` itself absent | `fleet-exec run hathor ls -d /var/log/lumen/transcripts` → `No such file or directory` |
| `/var/lib/lumen/benign_spans.db` (200-span benign control) | absent on hathor; `/var/lib/lumen` itself absent | same probe |
| `/opt/lumen/tools/{union_spans,kb_prefilter,diff_spans,count_fp,summarize_pilot}.py` | absent on hathor | same probe, `/opt/lumen/tools` → `No such file or directory` |
| `lumen-read-dir-sweep`, `lumen-read-dir-sweep-messenger`, `lumen-mask-at-rest`, `lumen-pat-scan` as CLIs taking `--input/--manifest/--out` | Lumen systemd units, not on hathor. They run `harness/read_dir_sweep.py` (`--policy --json --max-lines --write-runtime`), `harness/read_dir_sweep_messenger.py` (no flags), `harness/mask_at_rest.py` (`--store --session --dry-run`) and `bin/wq523_pat_residue.py --alert` | `systemctl cat lumen-*.service`; `grep -c '"--(input\|manifest\|out)"'` = 0 in all four scripts |

`python3 harness/pilot_spec_author.py lint /home/lumen/foreman pilots/132cba7d6594/CARD.md` exits 1 and flags
every Baseline and Treatment path. Some of those hits are arm outputs (`/tmp/baseline_*.jsonl`), but the inputs
above are not outputs. The spec generator invented this harness. The pinned manifest
`/tmp/lumen_pilot_sample.sha256` cannot be built because the corpus it hashes does not exist.

Building the missing pieces here would mean writing the measurement plan myself: a transcript corpus, a
benign corpus, a span-union/diff format, and a true-positive oracle. The card's metric relies on that oracle
(`count_fp.py`, `summarize_pilot.py`) and does not specify it. The card forbids writing that plan in a run,
so I did not.

## What still stands without a measurement

- The deferral question (do the existing sweeps already cover the real leak paths?) is still open. The pilot
  produced no number for it.
- Before anyone rebuilds this pilot, note that the card's seed rows (`rotate-credential`, `diagnosis`,
  `reverse-engineer`) are topic words, not secret values. Every literal match on them would count toward
  "additional masked spans", but none of them is a leaked credential. A rerun needs a labelled
  sensitive-span set, so that "true positive" means something other than "the KB matched".

## Follow-up

- Dispatch defect: this is the same class as ISSUE-LOG rows 1039 and 1120, and it recurs because `lint_card()`
  is still called only from its tests. Logged as ISSUE-LOG row 1171.
- To rerun: give the pilot a real Lumen-side corpus (e.g. a pinned, already-masked extract of the
  `mask_at_rest.py` stores), use `harness/masking.scan()` as the regex-union baseline, and supply a
  hand-labelled sensitive and benign span set.

Measured numbers: baseline masked spans = not measurable (0 of 200 transcripts available, corpus absent);
treatment = not run; additional_true_positive_masked_spans = n/a; benign false positives = n/a (control set
absent); wall-clock used ≈ 15 min of 90; tokens well under the 150000 budget.

RECOMMENDATION: WATCH
additional_true_positive_masked_spans=n/a benign_fp=n/a transcripts_run=0/200 kill_criterion=baseline_does_not_reproduce
