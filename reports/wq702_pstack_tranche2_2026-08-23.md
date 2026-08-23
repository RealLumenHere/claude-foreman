# wq#702 — pstack tranche 2: the 14 Cursor-tied skills, ported to the Lumen stack

Fork: `/home/lumen/pstack` (upstream github.com/cursor/plugins, MIT). Tranche 1
(30 editor-agnostic skills) shipped 2026-08-19; this tranche ports the 14
skills whose bodies are tied to Cursor's surface, per the adaptation map in the
ticket. The binding per-construct mapping is
`/home/lumen/pstack/docs/LUMEN_ADAPTATION.md`; this report is the summary.

## What shipped

**Skills** (installed to `~/.claude/skills` via `tools/port-skill.py`):
arena, automate-me, create-verification-skill, how, interrogate,
maintain-verification-skill, no-comments, **lumen-mode** (was poteto-mode,
+23 playbooks +2 references), recall, reflect, **lumen-setup-stack** (was
setup-pstack), show-me-your-work, swarm, why. The upstream `poteto-mode/` and
`setup-pstack/` trees were deleted after the rename (migrate callers, delete
legacy). Two tranche-1 skills that referenced renamed or Cursor-tied things
were migrated in the same wave: figure-it-out (poteto-mode → lumen-mode) and
architect (runner slugs → pstack-models roles).

**Subagents** (installed to `~/.claude/agents`): lumen-agent (was
poteto-agent), comment-sicko, both in Claude Code agent format.

**New tool**: `tools/lumen_panel.py`, the panel-seat CLI. One call per
cross-model seat against the LiteLLM gateway (`localhost:4000`), key resolved
from `$LITELLM_KEY` / `$LITELLM_MASTER_KEY` / the scoped foreman virtual key
file, never printed. `--list-models` reports exactly what the active key can
call, which is what lumen-setup-stack's detection step uses. Verified live:
`--list-models` returns the scoped set; a `deepseek-v4-flash` completion
round-tripped.

## The five adaptation decisions that matter

1. **lumen-mode keeps the router architecture; the stack underneath changed.**
   Principles index verbatim. Babysit/opening-a-pr/shipping now run on
   `lumen-pr` + Gitea (Anubis, `http://100.121.17.5:3000`) under
   `github-standards.md`; bugbot triage became the foreman verify/critic
   fail-closed gate (`references/review-triage.md`: a finding is dismissed only
   with a concrete refutation, unverified findings block landing);
   pause-safely/session-pickup ride the house `lumen_busy` key; worktree
   playbooks target `/home/lumen/worktrees` with fleet lanes never prunable.

2. **No direct Anthropic API spend.** Claude roles (judgment, prose, hardest
   tasks) are Claude Code itself — parent session or subagent with no model
   override. Cross-model seats (interrogate reviewers, arena runners and
   cross-judge, how critics, reflect tooling seat, swarm race arms,
   second opinions) go through `lumen_panel.py` onto deepseek-v4-pro,
   deepseek-v4-flash, cerebras-oss-120b, mistral-small. A gateway seat cannot
   use tools, so seats that must read the repo or write files stay Claude
   subagents; panel briefs inline their material.

3. **lumen-setup-stack writes `~/.claude/pstack-models.md`** (was
   `~/.cursor/rules/pstack-models.mdc`), and only ever writes models the key's
   `--list-models` confirmed. The scoped foreman key currently exposes
   `deepseek-v4-flash` + `qwen36-local` only; the skill trims the role map to
   what the session's key can actually call.

4. **automate-me mines our stores**: the episode store
   (`minerva/lumen_episode.py recall`) and `claude_memory.md`, not editor
   transcripts; output is `frank-mode` authored via the skill-creator plugin.

5. **The bun-only scripts were dropped, their jobs remapped.** `scripts/orch`
   (state store) → `/home/lumen/bin/wq` tickets plus single-writer store files;
   `scripts/watch-pr` → `lumen-pr check` + `git fetch` + Gitea API polling.
   `worktree-audit.sh` was kept and fixed for this host (it was macOS-flavored
   and GitHub-tied: `gh pr list` → Gitea API, Cursor transcript scan →
   `~/.claude/projects/<worktree-slug>`, `stat -f`/`date -r` → GNU forms).

## Verification

- Residue sweep over `skills/` + `agents/`:
  `grep -rniE 'cursor|poteto|\.cursor/|pstack-models\.mdc|grok-|gpt-5|AskQuestion|generalPurpose|bugbot|graphite|agent-transcripts|simulator'`
  returns zero hits (one "precursor" false positive; docs/ and README-LUMEN.md
  may name Cursor when describing the fork itself).
- Skill loading: a fresh session lists all 16 installed skills with their new
  descriptions, lumen-mode and lumen-setup-stack included.
- Acceptance test (ticket gate): headless `claude -p` session against a
  scratch repo with a seeded off-by-one in a trailing moving average (two
  failing pytest cases), prompted only to use lumen-mode and fix the failing
  suite. The session invoked the lumen-mode skill, read
  `playbooks/bug-fix.md`, reproduced the failure, instrumented the loop to
  prove the off-by-one mechanism (window spanned `window+1` elements, clamp
  masking it at the list head), applied the one-line fix, re-ran the suite
  (2 passed), and committed. Independently re-verified from outside the
  session: suite green, one-line diff, commit present. 15 turns, ~$0.54
  session cost, zero permission stalls under the acceptEdits settings file.

## Known limits

- Foreman fleet workers still have no Skill tool in worker-settings; these
  skills serve interactive `claude` sessions until Frank approves widening.
- The scoped foreman key limits panel diversity to its entitled models; wider
  panels need a key with more models attached (lumen-setup-stack adapts either
  way).
- `orch`/`watch-pr` upstream refreshes won't re-port automatically; the
  playbooks no longer reference them.
