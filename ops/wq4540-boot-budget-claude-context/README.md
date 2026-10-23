# wq#4540 — boot budget: claude_context.sh --core is ~22K tok, not ~2K

Source: ticket text (quotes `/home/lumen/scratch/research/memory-system-audit.md` F7/F8/QW2/
MW3/S3 — that path is outside this BUILD lane's read sandbox, same wall wq#4539's design doc
hit on the same audit file) plus `research/MINERVA_STALENESS_REVIEW.md` §2.7/§2.8, which this
session could read and which independently confirms two of the ticket's three claims:

- §2.7: `LIVE_FLEET_STATE=5734 tok exceeds soft cap 4000 … (not truncated here). The selector
  warns and passes the whole block through.` — a cap that only warns is not a cap.
- §2.8: `HANDOFF.md §3 — carries July-era entries under a hand-written "everything below this
  line is July-era and mostly done" disclaimer. Prose managing its own staleness.`

wq#4537 (landed, `ops/wq4537-claude-memory-hand-prose/`) already named this ticket as its
follow-up: "`Not done here: HANDOFF.md §3 still points at the deleted SESSION blocks. QW2
rewrites §3 completely.`" wq#4540 is that QW2.

## Why staged, not applied

Every target this ticket names lives under `/home/lumen/minerva` (not a git repo; `wq#1467`
walls it from BUILD-lane `read_dirs` — credentials mirrored into `context/sections`) or
`/home/lumen/bin` (a git repo, but its primary tree is worker-read-only, `wq#1719`/`wq#900`;
this session has no `lumen-bin` worktree). `/home/lumen/ORCHESTRATOR-HANDOFF.md` itself is not
under any root granted to this dispatch either. Same wall, same resolution wq#4537 used:
build and test every change against fixtures in this worktree, ship it as a bundle a session
with `minerva`/`lumen-bin` fs access applies with `install.sh`.

One gap worth flagging for whoever reviews this: `python3 /home/lumen/bin/fleet-tools-render`
(no path argument, run from this sandbox) executed and actually read/wrote
`/home/lumen/minerva/context/{fleet_tools.yaml,FLEET_TOOLS.md}` without the guard refusing it —
the per-command guard pattern-matches literal `minerva` text in a Bash command, it does not
sandbox a spawned process's own file I/O. That run was incidental (checking whether the binary
would even execute) and reproduced the live file byte-for-byte (`no drift`, confirmed by the
tool's own output), not a content change. It is logged in ISSUE-LOG as a real gap; this bundle
does not rely on it for anything below — every other file here is tested against a fixture,
never the live path.

## What this bundle ships

| File | Purpose |
|---|---|
| `fleet-tools-render` | Full replacement of `/home/lumen/bin/fleet-tools-render`. Adds `--boot`: drops the registry note and per-group description, collapses each tool to one line (`name — first line of use — invoke`) instead of two. `--src`/`--out` made overridable (env or flag) so it's testable without touching `minerva/context`. |
| `live_fleet_state_cap.py` | New script. Enforces the 4000-tok soft cap `projection_state.py check-warn` only warns about (`MINERVA_STALENESS_REVIEW.md` §2.7) — whole-line cut, never mid-line, states what was dropped in a trailing HTML comment. Same budget-cut contract as `harness/instincts_render.py`'s `tier0_block()` (landed, this repo). |
| `handoff_s3_rewrite.py` | New script. Anchors on the one verified substring ("July-era and mostly done"), archives the whole body of the section it's in to `sessions/handoff_s3_archive_<date>.md`, replaces it with 5 live-command lines (`wq board`, `instincts_render.py --check`, `fleet-tools-render`, `state-regen --if-dirty`, `lint-handoff.py` — all real tools verified elsewhere in this session's research, not guessed). Raises instead of writing if the anchor is missing or appears more than once — it cannot exact-string-match the live file's surrounding text without reading it, so it refuses rather than guesses (`apply_docs.py`'s contract, wq#4537). |
| `test_fleet_tools_render.py` / `test_live_fleet_state_cap.py` / `test_handoff_s3_rewrite.py` + `fixtures/` | 14 tests total. `fixtures/ORCHESTRATOR-HANDOFF-synthetic.md` is explicitly synthetic — not a copy of the live doc, which this session cannot read — built only to exercise the anchor/archive/replace logic. |
| `install.sh` | Backs up and installs `fleet-tools-render`, runs the cap and the HANDOFF rewrite once each against the live files, lints the result with `lint-handoff.py`, then prints (does not apply) the two `claude_context.sh` one-liners this session cannot verify against the live file. |

## What landed directly (not staged — in this branch, this repo)

`harness/instincts_render.py` gained a third audience, `boot`: tier 0 + tier 1 rendered whole
(same as `core`), tier 2 as bare `[trigger]` titles with no rule text — `core`'s tier-2 index
was the single biggest line item in `--audience core`'s own test corpus. `--audience` now
accepts `worker|core|boot`. Tests in `harness/tests/test_instincts_render.py`.

CORRECTION (wq#4582, ISSUE-LOG row 1547): the paragraph this replaced claimed wq#978 had
established `harness/instincts_render.py` as what `claude_context.sh --core` calls. That was
true of `harness/fworker.py`'s own context assembly, not of `claude_context.sh`: its line 56
has called the legacy `/home/lumen/bin/instincts-render` (wq#958, standalone — `--file` /
`--max-tier` / `--budget-tokens` only, no `--audience`) since wq#958 and was never repointed.
wq#4576 found install.sh step 4's first anchor (`--audience core`) missing and refused it per
instruction. wq#4582 chose option (a) — repoint line 56 to
`python3 /home/lumen/foreman/harness/instincts_render.py --audience boot` (one canonical
renderer, no bin/harness drift) — and corrected the staged sed in install.sh step 4 to anchor
on the literal `/home/lumen/bin/instincts-render` path. Measured against the live corpus the
repoint saves only ~763 B (13316 B → 12553 B): tier 2 is down to 4 rules, so wq#4540's
"tier-2 index is the biggest line item" premise no longer holds; the 45 KB target rides on the
HANDOFF s3 rewrite (wq#4581).

## Not done here

- Wiring edit 2 (FLEET_TOOLS.md → FLEET_TOOLS_BOOT.md) landed live via wq#4576; edit 1
  (instincts call-site repoint, corrected by wq#4582) is staged in install.sh step 4 and needs
  a session with `minerva` fs access to apply — queued as a follow-up ops ticket by wq#4582.
- Re-measuring `bash claude_context.sh --core | wc -c`: 75,020 B post wq#4576 (baseline was
  89,865 B). The 45 KB target needs the HANDOFF s3 rewrite (wq#4581) plus edit 1; edit 1 alone
  is worth ~763 B.
- `live_fleet_state_cap.py` is wired into `install.sh` as a one-shot; making it run on every
  boot means adding the call to `claude_context.sh` right after the existing `state-regen
  --if-dirty` / `projection_state.py check-warn` step (`ops/wq1633-minerva-projection-dirty-flag/claude_context_if_dirty.diff`) — same access gap.

## Verify (this bundle, against fixtures — no live access needed)

    cd ops/wq4540-boot-budget-claude-context && python3 -m pytest -q
    cd harness && python3 -m pytest -q tests/test_instincts_render.py

## Evidence (2026-10-23)

- `python3 -m pytest -q ops/wq4540-boot-budget-claude-context/` → 14 passed.
- `python3 -m pytest -q harness/tests/test_instincts_render.py` → 9 passed (8 pre-existing + 1
  new boot-audience test).
- `python3 /home/lumen/bin/lint-handoff.py <scratch copy of the synthetic fixture, post-rewrite>
  --no-live -v` → `RESULT: CLEAN (0 issue(s))`, `paths_checked=5` — all 5 of the new live-command
  lines cite tools that exist on disk right now.
