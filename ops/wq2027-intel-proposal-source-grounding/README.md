# wq#2027 — intel→proposal source-grounding gate

## The defect

`foreman/research/AGENTFORGE_EVAL.md` section 0/1: proposals `ff80d5e94515`
(source `classified_intel` row: `Saichandra2520/AgentForge`, a project
scaffolder) and `37cb4ce26a9b` (source row: `Roshanrameshhub/AgentForge`, a
6-commit, 1-day-old demo) were both generated from a repo **title only** —
`classified_intel.url` and `.summary` were empty for both rows. The
"orchestration patterns" / "memory + skills + self-improvement" claims in
each proposal were the classifier's own HINT text, not a fact about the
repo. `minerva_verify.verify()` scored both **1.00** because every layer it
runs (semantic citation, NLI judge, wq#1997's advisory-existence check) only
checks a claim against Lumen CONTEXT (or an advisory id) — nothing checked
whether the claim was true of the *external subject* the proposal was about.

A companion fleet research note,
`scratch/research/intel_new_github_repo__roshanrameshhub_agentforge_01a87c.md`,
independently fabricated a repo tree for the same repo (`agents/ routing/
rag/ memory/ mcp/ workflow/ human/` — the real tree is `agent/ client/
docker/ docs/ k8s/ media/ monitoring/ schema/ scripts/ service/`, zero
overlap, confirmed live against the GitHub trees API).

## The fix — three parts, all fail-closed

> **Deployed 2026-09-28 (wq#3004), from this directory's own `minerva_proposal.py` /
> `minerva_verify.py`, not the sibling copies named below.** By then the sibling
> copies predated wq#2430, wq#2460 and wq#2905 on live, so the wq#2027 hunks were
> re-derived onto minerva `42d8c2a` (landed as minerva PR #47). `install.sh`
> drift-gates on that base and answers `--verify`.

**1. Eligibility gate** (`minerva_proposal.py`, patched in place at
`ops/wq2019-proposal-reviewer-fix/minerva_proposal.py` — that directory is
the live tracked copy this file has moved through since wq#2019; the pattern
already accumulates wq#2021/2022/2023/2025's route_conflict fixes the same
way). `get_candidates()` now requires `url` and `summary` to be non-empty —
a row with either empty is not eligible to seed a proposal, full stop. It
stays ineligible (silently excluded, with a one-line log count) until the
classifier backfills both. This directly stops the defect class: neither
`ff80d5e94515` nor `37cb4ce26a9b`'s source row would clear this gate today.

**2. Subject-grounding check** (`minerva_verify.py`, patched in place at
`ops/wq1997-external-advisory-check/minerva_verify.py` — the file wq#1997/
wq#2005/wq#2010 already accumulate in). New `subject_exists(url)` (GitHub
repo API, same fail-closed True/False/None contract as `_ghsa_exists`) and
`source_grounding_check(claim_text, url, summary)` (extracts claims from
`claim_text`, scores them against the fetched `summary` via the existing
`semantic_citation_score`, the same machinery Layer 1 already uses — just
pointed at the fetched source instead of Lumen CONTEXT). `verify()` gained
three optional kwargs (`source_url`, `source_summary`,
`subject_claim_text`); every pre-existing call site that doesn't pass them
is unaffected (proven in `test_source_grounding.py`). `minerva_proposal.py`'s
`run()` now passes them for every candidate — `url`/`summary` are guaranteed
non-empty at that point by gate 1. Can only flip a passing verdict to
failing, runs after every rescue path, same discipline as wq#1997's Layer 0.

**3. Sweep** (`sweep_empty_source_proposals.py`, new). Rejects pending
proposals whose source `classified_intel` row still has no url/summary —
this is what actually resolves `ff80d5e94515`/`37cb4ce26a9b`, per the eval
doc's own recommendation (section 6: "recommend REJECT both... wq#2027 step
3 sweeps the class"). Dry-run by default, `--apply` to write, mirrors
`reopen_stale_rejects.py`'s safety shape.

**Classifier-side fetcher** (`fetch_source.py`, new). `fetch_repo_source(url)`
does the four checks the eval doc's own manual research used: README head,
license, commit count (Link-header page estimate), created/pushed_at. Wiring
this into `minerva_classifier.py` itself is **out of scope of this session**
— that file lives at `/home/lumen/minerva`, outside every directory this
worktree/session can reach (verified: a bare `wc` on it was blocked by the
harness's path guard). The integration point is documented in
`fetch_source.py`'s own docstring for a session that has that path in scope.
Until that wiring lands, gate 1 does its job from the other side: rows with
no summary simply never seed a proposal.

**Quarantine** (`quarantine_fabricated_notes.py`, new). General form of the
second finding: scans `scratch/research/*.md` for notes that name a GitHub
repo AND cite what reads as a top-level directory tree; flags (dry-run) or
moves (`--apply`) any note whose cited top-level entries have zero overlap
with the real tree (fetched live). Verified against the exact
Roshanrameshhub/AgentForge case from the eval doc (both the fabricated and
the real tree strings) — `--apply` moves matches into
`scratch/research/_quarantine/` so `lumen-research-index` stops picking them
up; dry-run only prints candidates.

## Files

- `minerva_proposal.py` / `minerva_verify.py` — **not duplicated here**; the
  patches live in their canonical ops directories (see part 1/2 above) and
  are pulled from there by `install.sh`.
- `fetch_source.py` — classifier-side fetcher (README/license/commits/pushed_at).
- `sweep_empty_source_proposals.py` — rejects pending proposals from
  no-source rows. `python3 sweep_empty_source_proposals.py [--apply]`.
- `quarantine_fabricated_notes.py` — flags/moves notes with a fabricated
  cited tree. `python3 quarantine_fabricated_notes.py [--research-dir DIR] [--apply]`.
- `test_source_grounding.py` — regression tests for parts 1 and 2. Loads
  both patched files via importlib with `sys.modules['minerva_verify']`
  pre-seeded, so it never touches `/home/lumen/minerva` or the network
  (GitHub calls are monkeypatched): `python3 test_source_grounding.py`.
- `install.sh` — change-safety replay (backup → apply all five files →
  import-health → auto-revert).

## Deploy

Primary deploy is git: land this PR and let `lumen-pr` fast-forward
`/home/lumen/minerva` (same convention as wq#1997/wq#2005/wq#2010/wq#2019).
`install.sh` is the replay for a host where that fast-forward hasn't
happened yet. After deploy, from a session with `/home/lumen/minerva` in
scope:

```
python3 /home/lumen/minerva/sweep_empty_source_proposals.py --apply
python3 /home/lumen/minerva/quarantine_fabricated_notes.py --apply
```

Wiring `fetch_source.fetch_repo_source()` into `minerva_classifier.py` is a
follow-up for a session that can reach that file (see `fetch_source.py`
docstring).
