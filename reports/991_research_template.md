# wq#991 — prior-art search template

The device gate (wq#1006) requires a non-empty `projects/<name>/research/
PRIOR_ART.md` before any device command on a `hardware_class` project. That gate
is satisfied by running `fleet-search --project` by hand, one query at a time.
wq#991's deliverable #3 was the search DISCIPLINE itself: several angled
queries, follow the links, read the comments — not one broad search and give
up. This adds `harness/research_phase.py`, the one command that does that and
writes the `PRIOR_ART.md` the gate consumes.

Mechanics:
- `derive_queries`: rules `research_queries` / `research_subject` win; else
  de-slug the project name (miko3 -> "miko 3") plus angles (github, firmware
  root unlock, teardown forum xda) plus the bare subject. Short, Frank-style.
- `run_phase`: web on every query, reddit + github on the primary subject, then
  read the top reddit thread and pull its comment tree via `fleet-search read`.
- Sole writer: calls fleet-search WITHOUT `--project` (pure JSON) and writes one
  `## RESEARCH PHASE` section itself. Passing `--project` would duplicate the
  content, since fleet-search appends its own sections.
- Fail-closed: if every source returns zero, it writes nothing and exits 2. A
  header-only file would be non-empty and would wrongly open the gate.

Relationship to wq#1006: additive, no gate code. The gate stays exactly as 1006
shipped it; this just produces a better `PRIOR_ART.md` than a hand-run search,
and any single `fleet-search --project` still opens the gate on its own.

Test: `tests/test_research_phase.py` (6). The load-bearing one compiles a temp
project's rules with the real `action_gate` and asserts `_prior_art_ok` flips
false -> true after `research_phase` runs — i.e. the template output opens the
landed gate. A unique temp project name keeps the gate's hardcoded canonical
path empty, so only the template's file counts. Live smoke: 18 results across
web/reddit/github, one clean section, found the r/hacking Miko 3 thread.

Refs wq#991, wq#1006, wq#999.
