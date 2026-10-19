# wq#4402 — refuse/warn when a ticket's detail names a sibling wq# with no --deps

wq#4396 (INTEGRATE: remote-cli as Limpet shared terminal backend) existed only to wrap
wq#4395's deliverable, but was filed with an EMPTY `--deps` 1 second after wq#4395 and
claimed 4 seconds after it — the dependency existed only in prose. The wrapper was
written against an invented wire protocol while the real sibling artifact sat on disk
26s before the commit, shipping 8/8 green with 0/4 tools working
(reports/4397_verify_wq4396_remote_cli_protocol_mismatch.md, ISSUE-LOG row 1473).
`deps_met()` (wq:1065-1070) already refuses a claim unless every dep is `status=done` —
nothing was unenforced, the dependency was simply never recorded.

## Install (Lumen)
    python3 /home/lumen/foreman/ops/wq4402-sibling-ref-dep-guard/apply_wq_patch.py   # .bak'd, idempotent; --check to verify

## What it does
`wq add` scans `--title`/`--detail` for a `wq#NNNN`/`wqNNNN`/`wq NNNN` reference. If
`--deps` is empty and the reference points at an OPEN (not `done`) sibling task:
- lane `build` (where wq#4396 itself was filed) REFUSES with exit 1, naming the
  referenced id(s) and status, and suggests `--deps` or `--force`.
- every other lane prints a WARNING and proceeds — a mention is not always a
  dependency (back-references to closed tickets are common in detail text), so only
  the build lane gets a hard stop.

A reference to an already-`done` sibling never fires the guard at all — that's a
routine citation, not an undeclared dependency.

## Probe (wq#2630: must be able to fail)
    python3 ops/wq4402-sibling-ref-dep-guard/probe_wq4402.py
Constructs an in-memory `tasks` table with one `open` and one `done` row, asserts the
guard's detector fires on the open reference and not on the done one.

## Rollback
    cp /home/lumen/bin/wq.bak.<ts>_pre_wq4402 /home/lumen/bin/wq
