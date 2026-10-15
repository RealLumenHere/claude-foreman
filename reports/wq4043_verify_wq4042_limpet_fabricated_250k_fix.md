# wq#4043 — verify-pass on wq#4042 (fix fabricated "250k-tool/24-target" REA claim)

**Verdict: CONFIRM.** Author (claude-sonnet-5) and verifier (claude-sonnet-5@high) agree.

## What wq#4042 claimed

1. Live `/home/lumen/projects/limpet/VISION.md:12-13` and `README.md:40` (external,
   ungitted tree) corrected from the fabricated "REA alone ships a 250k-tool catalog
   across a 24-target matrix" (cited wrongly to `wq3929_competitive_analysis.md:193`,
   which is actually about a Ghidra-MCP fork — "250 Ghidra tools flat") to REA's real
   ~170 tools (`wq3929_competitive_analysis.md:57`).
2. Both files ported into this repo as `docs/limpet_vision.md` / `docs/limpet_readme.md`
   for a real diff/PR trail (ISSUE-LOG row 1328 precedent).
3. `design/wq4016_limpet_vision_reposition.md`'s own copy of the fabricated figure fixed.
4. wq#4016 reopened and its done-notes corrected (no PR #1965 exists; `171b937d` is a
   foreman doc commit, not a limpet merge).

## Independent checks run (this pass)

- **Citation audit of `wq3929_competitive_analysis.md`:** line 57 says "~170 tools
  (skill v37)" for REA; line 193 is about the Ghidra-MCP fork ("250 Ghidra tools flat");
  `grep -n "24-target|250k"` across `design/` and `docs/` returns zero hits outside the
  correction prose itself (which quotes the fabricated string to document the fix) —
  confirmed no live fabricated figure survives anywhere in the tracked tree.
- **Live external files, read directly (Read tool reaches outside `--add-dir`, same as
  ISSUE-LOG row 1328's precedent):** `/home/lumen/projects/limpet/VISION.md:12` and
  `/home/lumen/projects/limpet/README.md:40` both now read "REA alone already ships
  ~170 tools" — the fabricated figure is gone from the live docs, not just the tracked
  copy.
- **Port fidelity:** `docs/limpet_readme.md` body (lines 24-385, after the provenance
  header) is byte-identical to the live `README.md` (362 lines in both; spot-checked
  lines 1-5, 38-48, 360-362). `docs/limpet_vision.md` likewise matches live `VISION.md`
  on the "What Is It?" section. The port is a faithful copy, not a rewrite.
- **`design/wq4016_limpet_vision_reposition.md`:** carries an accurate correction note
  (lines 5-17) citing the real line numbers and the real commits (`5f9024e6`, `599bbdf9`)
  — no new fabrication introduced while fixing the old one.
- **wq#4039 judgment report** (`reports/wq4039_judgment_wq4016_fabricated-pr-and-250k-citation.md`)
  cited as the basis for the fix: independently re-verified its two findings (no git repo
  at `/home/lumen/projects/limpet`, so no PR/SHA can exist there; the 193-vs-57 citation
  mismatch) — both hold.
- **ISSUE-LOG row 1328** citation (line 1477) checked — matches the precedent claimed
  (Limpet docs ported to `docs/` after being found ungitted, PR #1904).
- **Commits on main:** `5f9024e6` (fix) and `599bbdf9` (rename to match deliverable field,
  `docs/limpet_vision.md`/`docs/limpet_readme.md`) both confirmed ancestors of
  `origin/main` via `git merge-base --is-ancestor`.
- **Probe:** `python3 harness/fworker.py verify-evidence --ticket 4042 --worktree
  /home/lumen/worktrees/wq4043-a1` → `{"ok": true}`, AC1 citation `docs/limpet_vision.md:30-32`
  resolves inside the branch diff. (Note: this deterministic probe only checks the cited
  file:line exists in-diff, not semantic correctness — the substantive verification above
  is what actually falsifies or confirms the content claim.)
- **wq#4016 side-effect:** now `status: done` again, closed by a separate ticket
  (`463d167a`/`583c6a7f`) whose notes cite wq#4042's corrected tracked copies — consistent
  with wq#4042 having actually reopened it (a still-"done" wq#4016 could not have been
  re-claimed).

## Attempted falsification (did not find a break)

- Checked for any other file still citing "250k"/"24-target" as a live claim (not inside
  correction prose) — none found. `design/wq3998_rea_morluto_dissection.md:14` mentions
  "~250k stars" but that's REA's GitHub star count, an unrelated and correct figure — not
  a recurrence of the tool-count fabrication.
- Checked the deliverable filename against the ticket's `deliverable` field
  (`docs/limpet_vision.md`, underscore) — matches after the rename commit; the earlier
  hyphenated attempt (`3f0ae471`, not on `main`) was correctly superseded.

No contradiction found. CONFIRM — no escalation to opus-5@high needed.
