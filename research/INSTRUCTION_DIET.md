# INSTRUCTION_DIET — prompt compression (wq#631)
Depends on TOKEN_BASELINE.md (wq#629) and PREFIX_STABILITY.md (wq#630).

## Scope and what got preserved verbatim

Per the ticket: safety rules, deny rationale, provenance block, and no-human-present block
are compression-fragile — moved, never shortened. Applied to both live worker prompts:

- `FOREMAN_DISPATCH.prompt` (interim ORCH-dispatch pattern, gitignored — not in this repo's
  diff, edited on disk regardless since the ticket names it and it's this session's own
  live boot prompt): the `PROVENANCE` paragraph (includes the no-human-present sentence)
  and the entire `GUARDRAILS (hard, non-negotiable)` block are **byte-identical** to
  pre-diet. Only the connective prose around them (intro line, BOOT/TICKET/EXECUTE/LAND/
  DONE wording) was tightened — no step, path, or command was dropped, only filler words
  ("that is your spec" → em-dash, "Do not re-derive" → "Don't re-derive", etc).
- `harness/prompts/worker.md`: the "No human watches this session..." bullet (no-human-
  present block) and the PreToolUse guard-hook deny-list bullet (deny rationale) are
  **byte-identical**. The worktree/land bullet and the `wq done`/`wq release` bullet were
  tightened (same content, fewer words). Also carries wq#630's freeze-rule bullet, added
  last session, left as-is (already dense, not prose).

## CLAUDE.md — scoped out, not silently skipped

`/home/lumen/CLAUDE.md` is the only CLAUDE.md a worker actually loads (`HOME=/home/lumen`
in `fworker.py build_env()`; confirmed no `CLAUDE.md`/`AGENTS.md` is tracked in the
claude-foreman repo itself — `git ls-files | grep -i claude.md` is empty, and worker
worktrees only ever contain tracked files). But `/home/lumen/CLAUDE.md` is **not part of
any git repo** (`/home/lumen` itself isn't a git worktree) and it's fleet-wide: every
Claude session on this box loads it, not just foreman workers. Editing it is out of the
claude-foreman repo's diff/review path entirely (no PR, no revert-by-git), and a wording
slip there risks the busy-key coordination protocol for every concurrent agent on the box,
not just this lane. Left untouched. It's already dense-bullet (25 lines, ~310 cl100k
tokens, see below) with limited compressible prose left in it (the "### Why:" paragraph is
the only real prose block, 2 sentences). Follow-up recommendation if Frank wants this
touched: a separate ticket scoped to the shared CLAUDE.md specifically, with its own
backup + fleet-wide review, not bundled into a claude-foreman PR.

## Measured token counts

Method: `tiktoken` `cl100k_base` encoding as a consistent proxy (Claude's own tokenizer
isn't exposed to this box's OAuth CLI surface, same limitation TOKEN_BASELINE.md/
FOREMAN_RESEARCH.md already logged for `cache_control`) — valid for a same-tokenizer
before/after delta, not for predicting the exact live Claude token count.

| file | before (tokens) | after (tokens) | delta |
|---|---|---|---|
| `FOREMAN_DISPATCH.prompt` | 965 | 909 | -5.8% |
| `harness/prompts/worker.md` | 491 | 467 | -4.9% |
| `/home/lumen/CLAUDE.md` | 310 | 310 (unchanged, see above) | 0% |

**Tokenizer-factor note (wq#706, CALL_AUDIT.md):** these are `cl100k_base` proxy counts,
and cl100k understates real billed Claude tokens by ~1.8x on code/shell/JSON/paths
(measured two independent ways, 1.63–2.04x across a five-session clean set). These three
files are prose/procedural instruction text, not code, and CALL_AUDIT.md explicitly does
not extend that 1.8x to prose — it estimates prose sits closer to 1.0–1.3x but was not
itself measured here, so do not rescale the table above by 1.8x. The before/after deltas
stand either way: both counts in each row are the same tokenizer on the same content mix.

Well short of the source research's 40-50% claim. That number is achievable on prompts
that are mostly prose; these two are majority safety/procedural content this ticket
explicitly forbids shortening (GUARDRAILS + PROVENANCE in `FOREMAN_DISPATCH.prompt` alone
is ~40% of its bytes). The honest number for the compressible remainder (connective prose
around the preserved blocks) is a genuine ~5-10% reduction with zero behavior dropped —
reported as measured, not inflated to match the source research's headline figure.

## Gate verification

- Probe suite: `python3 -m pytest harness/tests/ governor/test_governor_core.py -q` → 87
  passed (60 harness + 27 governor; the ticket's "36/36" figure predates several harness
  tickets that added tests since — current count reported here, not the stale one).
- Real worker run on the dieted prompt: wq#650 (`[foreman-tok] wq#631 smoke: dieted
  worker.md real-dispatch validation`), a purpose-built one-file mechanical-edit ticket,
  dispatched for real through `fworker.py` (not the zero-quota `fake_claude.py` shim the
  regular test suite uses — that harness explicitly defers "a real quota-costing smoke
  run" as a follow-up per its own docstring, and this ticket's gate is that follow-up).
  `claude-sonnet-5@low`, `mechanical-edit` archetype (models.yaml's routed default for
  this archetype), minimal scope to keep the real spend small. Result recorded below.
