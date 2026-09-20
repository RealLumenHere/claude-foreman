# wq#1630 — one handoff: generated region + hand prose in HANDOFF.md

MINERVA_PIPELINE_MAP.md §1.10/§4.2/§5#2: `kb/SESSION-HANDOFF.md` was regenerated every 30 min by
`bin/session-handoff` and read by nobody, while `HANDOFF.md` (hand prose) is `cat`ed at every
`--core` boot. Now there is one file.

- **`session-handoff`** (installs to `/home/lumen/bin/session-handoff`): renders in-flight
  claims, last 10 landed PRs, detector stamps, and the last 5 `--note` entries between
  `<!-- session-handoff:begin … -->` / `<!-- session-handoff:end -->` at the **top** of
  `/home/lumen/HANDOFF.md`. First run prepends the region; later runs replace only the region.
  Everything outside the markers is byte-for-byte untouched (hand `## 4. VERBATIM CONSTRAINTS`
  stays below). Write is temp-file + rename, mode preserved. Unpaired markers or an
  unreadable `queue.db` → exit 1 and the file is left as it was. No `kb/` output, no git step.
- **`claude_context_render_handoff.diff`**: `claude_context.sh --core` runs
  `timeout 15 session-handoff --render` right before it `cat`s HANDOFF.md (fail-open), the same
  read-time re-render pattern as `fleet-tools-render`.
- **Dropped sections**: blocked-by-class (`wq list --status blocked`) and open FRANK decisions
  (frank gates retired, 2026-08-22).
- **`install.sh`**: installs the script and hook, renders once, asserts the region + hand prose
  are present, then `git rm`s `kb/SESSION-HANDOFF.md` and stops tracking the notes log. Deletion
  happens only after that real render succeeds ("one clean cycle").

Why an install step: the BUILD worker's guard (wq#1719/wq#900) makes `/home/lumen/bin`,
`/home/lumen/minerva` and `/home/lumen/kb` read-only, so the live copies could not be changed from
the ticket's session — same situation and pattern as `ops/wq1633-minerva-projection-dirty-flag/`.

Tests: `python3 -m pytest -q ops/wq1630-handoff-merge/test_session_handoff.py`.
