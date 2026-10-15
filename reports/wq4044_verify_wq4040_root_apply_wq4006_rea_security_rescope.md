# wq#4044 — verify wq#4040 (root-apply wq#4006 REA security rescope, live)

Archetype 8 adversarial verify-pass of
http://100.121.17.5:3000/lumen/claude-foreman/pulls/1973 (wq#4040). Author:
claude-sonnet-5. Verifier: claude-sonnet-5@high, fresh instance. Verdict:
**CONFIRM** — no contradiction, no escalation.

## What wq#4040 claimed

- Ran `ops/wq4006-rea-security-rescope-a1/install.sh` live via `lumen-run`:
  exit 0, journal shows backup + `rea`/`rea-maksoor` removed from user scope
  + `~/re/.mcp.json`/`PROVENANCE.json` written.
- `install.sh --verify` -> `VERIFY OK` against the live box.
- `rea --version` -> `6.5.0`.
- `rea doctor --provider ghidra` exits 1 for two reasons out of this
  ticket's scope (user-scope absence flagged as drift; `GHIDRA_INSTALL_DIR`
  unset in the job env).
- `ops_verified(wq4006-rea-security-rescope-a1)` liveness probe now
  SATISFIED.
- Repo diff is docs-only (`pr_body_wq4040.md` + the wq4040 report); the real
  change is the live apply.

## What I independently re-ran (not re-trusting the report's quoted output)

All against the live box, via `lumen-run` detached units (outside the
per-tool sandbox) except the first, which ran directly:

1. `/home/lumen/foreman/ops/wq4006-rea-security-rescope-a1/install.sh --verify`
   directly (not via the PR's quoted journal) -> `VERIFY OK`, exit 0.
2. Called `liveness.ops_verified('wq4006-rea-security-rescope-a1')` directly
   from `harness/liveness.py` (the actual probe code at the cited
   `harness/liveness.py:432-451`, not a paraphrase) -> `('SATISFIED',
   'wq4006-rea-security-rescope-a1: VERIFY OK')`.
3. Read live `~/.claude.json`'s `mcpServers` keys -> `[]`. Cross-checked the
   `~/.claude.json.pre-wq4006-20261015T111013Z` backup the apply itself
   created -> pre-edit `mcpServers` keys were exactly `['rea',
   'rea-maksoor']`. Rules out a `remove_user_scope.py` bug that might have
   wiped unrelated entries — there were no unrelated entries to wipe.
4. Read live `~/re/.mcp.json` and `~/re/PROVENANCE.json` in full. `rea`/
   `rea-maksoor` present, `rea-guard`-wrapped, project-scope marker
   (`_wq4006`) present. `PROVENANCE.json` has a real npm `dist.integrity`
   (sha512) and `dist.tarball` URL for `rea-agents@6.5.0`, matching what
   `generate_provenance.py` actually does (`npm view <spec> dist.integrity`/
   `dist.tarball` against the live registry — not fabricated).
5. `diff`'d the live `~/re/bin/rea-guard` and
   `~/re/common/tools/HOSTILE_INPUT_POLICY.md` against the bundle's source
   copies in `ops/wq4006-rea-security-rescope-a1/` — byte-identical (exit 0
   both).
6. **Adversarial probe the PR didn't run**: invoked
   `/home/lumen/re/bin/rea-guard update|self-update|upgrade` directly.
   All three refused with the documented message and exit 1; none fell
   through to the real binary.
7. Ran `rea doctor --provider ghidra` myself: exit 1, output shows exactly
   the two causes the report names — `registrations[1]` for `claude_code`
   at `/home/lumen/.claude.json` is `state: missing` (`classification:
   config_drift`, the intended end state), and `ghidra` scope check is
   `missing_analysis_engine` / `GHIDRA_INSTALL_DIR is not set`. No other
   failure present.
8. `rea --version` -> `6.5.0`, matches the pin.
9. `/home/lumen/bin/slock who wq4006-rea-rescope` -> `(no active locks)`.
   The apply's lock was released; no orphaned hold blocking a future run.
10. **Specifically checked for a clobbered pre-existing file**: `~/re` is a
    symlink to `/data/lumen/archive/re` (predates this ticket — symlink
    mtime 2026-09-26, long before wq#4006's 2026-10-14 finding), the same
    directory the RUNBOOK says `ghidra-lumen` uses for its own project
    scope. If a `ghidra-lumen` `.mcp.json` had existed there already,
    `install.sh`'s unconditional `sed ... > .mcp.json` (no backup of the
    pre-existing file) would have destroyed it. `stat` on the live
    `.mcp.json` shows `Birth: 2026-10-15 11:10:14` == `Modify` == the
    apply's own timestamp — the file did not exist before this apply, so
    nothing was clobbered. This is the strongest candidate for a real bug
    and it did not materialize.

## Contradiction search (came up empty)

- Checked whether the apply silently wiped unrelated `mcpServers` entries:
  no, confirmed via the backup (item 3).
- Checked whether the shipped guard/policy files were only present, not
  content-correct: byte-diffed, identical (item 5).
- Checked whether `rea-guard`'s block was asserted but untested: ran it
  myself against all three blocked subcommands (item 6) — the PR's report
  never actually invoked `rea-guard update` etc., only inspected its
  source; I did.
- Checked whether `rea doctor`'s exit 1 might hide a real regression beyond
  the two named causes: re-ran it, output matches the two named causes
  exactly, nothing else failing.
- Checked whether the install clobbered pre-existing ghidra-lumen state at
  the shared `/data/lumen/archive/re` directory: ruled out by file birth
  time (item 10).
- Checked the `ops_verified` liveness probe isn't a paraphrase of a
  cherry-picked run: called the probe function itself, got SATISFIED.

## Minor observation (not a contradiction, not escalation-worthy)

`/home/lumen/kb/ISSUE-LOG.md` row 1346 (wq#4006) still carries status
`BUNDLE-SHIPPED-NOT-APPLIED`, which predates wq#4040's live apply and is now
stale — wq#4040's diff never touched it (confirmed via `git show --stat
8f8392b4`: only `pr_body_wq4040.md` and the wq4040 report changed). Worth a
follow-up status-flip edit on that row; does not bear on wq#4040's own
acceptance criteria, which are all about the live apply, not the issue log.

## Verdict

**CONFIRM.** Every claim in the wq#4040 PR/report reproduced independently
against the live box, including two checks the PR itself didn't run (guard
subcommand refusal, pre-existing-file clobber risk). No contradiction. No
escalation to opus-5@high.

Refs: wq#4044, wq#4040, wq#4017, wq#4006.
