# PREFIX_STABILITY — cache hit-rate audit + freeze-toolset rule (wq#630)
Depends on TOKEN_BASELINE.md (wq#629). Same 8 files, same zero-new-spend method (reads
`_runs/*.jsonl` + `token_baseline.json`, no new Anthropic calls).

## Premise correction carried from FOREMAN_RESEARCH.md ("NOT-APPLICABLE" ledger)
Manual `cache_control` breakpoints are not reachable from the CLI's OAuth `-p` surface —
"the CLI manages prompt caching itself." That rules out the request-body-level lever this
ticket's source research assumed. What's left, and what this ticket actually codifies, is
the one lever we DO control: the raw byte order of what we send. Anthropic's cache is
content-addressed per API call, not session-scoped — any two calls (even from different
`claude -p` processes / session_ids) that share an identical token prefix up to a cache
breakpoint can share a cache hit, subject to TTL. A single differing byte anywhere in that
prefix caps the reusable portion at everything *before* the divergence. Putting static,
repeats-every-spawn content first and per-run variables last is the standard mitigation —
it costs nothing and only helps.

## (1) Thrash sources, from baseline data

**In-session thrash: none found.** TOKEN_BASELINE.md already checked this directly — zero
turn-over-turn cache_read drops >5,000 tokens across all 8 files, zero real
`compact_boundary` frames (the `compact_boundary` string hits in 613/614/625 are the
worker reading CLAUDE_CLI_INTERNALS.md §F / worker.md's own doc text, not live compaction
events — confirmed by grepping the surrounding context). So nothing in the current
harness is breaking a session's own rolling cache mid-run.

**Cross-session reuse: never happened, but also never had a chance to.** Every one of the
8 files pays the full fixed-prefix cost (4.7k–10.7k tokens, or 120k–156k on a
`.resume`/`.land`) as a first-turn `cache_creation_input_tokens` write — none of them
shows a first-turn `cache_read`, meaning none piggybacked on another session's cache.
Two structural reasons, both real:
  a. **Prompt structure.** `harness/prompts/worker.md` (and the interim
     `FOREMAN_DISPATCH.prompt`) put the per-run volatile values (ticket id, title, detail,
     worktree path) *before* the large static RULES/GUARDRAILS/LAND block. Any two
     dispatches diverge at the first volatile byte, which sits near the *top* of the
     document — so the static block that follows, despite being byte-identical across
     every ticket, never gets a chance to be recognized as a shared prefix. This is the
     literal defect item (3) asks to fix, and worker.md / FOREMAN_DISPATCH.prompt are
     edited by this ticket to reorder it: static slab first, volatile trailer last.
  b. **Worktree cwd.** Each ticket runs in its own `git worktree` path
     (`WORKTREES_ROOT/wq{ticket}-a{attempt}`), and cwd is part of what the CLI injects
     ahead of/around the system context. Two different tickets can never fully converge
     on a shared prefix regardless of prompt ordering, because their cwd differs from the
     first divergence point on. Reordering the *prompt text* (a) is still worth doing (it
     maximizes whatever prefix survives before cwd-driven divergence, and it's free), but
     don't expect it alone to produce full cross-session cache hits — the worktree-per-
     ticket isolation (a deliberate, load-bearing design choice, not a bug) caps the
     upside. None of the 8 baseline sessions ran concurrently close enough in time to test
     this regardless (T2b's stagger + concurrency cap doesn't exist yet in the baseline
     era).
  c. **Settings/toolset churn** was checked (the `--strict-mcp-config` question
     TOKEN_BASELINE.md flagged as a follow-up): only 1 of 8 files
     (`fbuild-613.wave1.jsonl`) has non-empty `mcp_servers` (8 inherited, all
     needs-auth/pending, unused), and its fixed-prefix overhead (10,520 tokens) sits
     inside the same 4.7k–10.7k range as the 6 confirmed-clean files — one thin sample,
     not proof either way, but no evidence of toolset churn *within* this baseline. This
     ticket's freeze rule (item 2, below) is written to prevent it going forward
     regardless.

## (2) Freeze rule (codified)

Toolset, settings, and CLAUDE.md are FROZEN for the life of a worker run:
- Never toggle MCP servers mid-session (adds/removes tool schemas → the cached
  system+tools prefix no longer matches, full rewrite for every turn after).
- Never edit `worker-settings.json` / any `--settings` file a running worker is using.
- Never edit CLAUDE.md (or any file Claude Code auto-loads into context) while a worker
  that reads it is still in flight.

Applied in:
- `FOREMAN_DISPATCH.md` — new "Prefix cache stability" section (operator-facing: what NOT
  to touch while workers are live).
- `harness/prompts/worker.md` — new RULES bullet (worker-facing: same rule, phrased as an
  instruction to the worker itself, since a ticket could otherwise plausibly touch its own
  settings/CLAUDE.md mid-run without realizing the cache cost).

## (3) Volatile-values-at-the-end (codified)

`harness/prompts/worker.md` and `FOREMAN_DISPATCH.prompt` reordered: the fully-static
block (identity, RULES, guardrails, land/done instructions, sentinel line) now comes
first, byte-identical across every dispatch; the per-run values (ticket id, title, detail,
worktree path, prior-attempt notes) are consolidated into a trailing labeled block. WQ_AGENT
itself is an env var, not prompt text, so there's nothing to reorder there — noted so a
future reader doesn't go looking for it in the prompt body.

## (4) CLAUDE_CODE_AUTO_COMPACT_WINDOW=500000 interaction

`fworker.py build_env()` already sets this for every worker spawn (T2a). Per
CLAUDE_CLI_INTERNALS.md §F, compaction fires in `-p` mode too and emits `compact_boundary`
with pre/post token counts — a compaction event rewrites the session's context, which is a
real cache-prefix loss for every turn after it (not the same as a full cache miss, since
the new shorter context still gets cached going forward, but the pre-compaction cache
investment is stranded).

Measured against the baseline: **zero `compact_boundary` frames in any of the 8 files**
(confirmed directly, not just absence of the string — see (1) above for why naive grep
over-counts). Per-turn live context size (`input_tokens + cache_read_input_tokens +
cache_creation_input_tokens` on that turn, i.e. what actually counts against the
auto-compact window, not the cross-turn *sum* of cache reads) tops out at **211,845
tokens**, in the longest/most turn-heavy baseline session
(`fbuild-625.jsonl`, 151 turns, still hit `error_max_turns` before ever nearing 500k).
So at current ticket sizes compaction has ~2.4x headroom before it would ever fire.
`fworker.py` already counts `compact_boundaries` per attempt (`classifier.py`
`RunState.observe()`) and records it in `attempt-N.json`, so this is self-monitoring going
forward — no code change needed here, just confirming the wiring already matches the ask.
Tuning verdict: **500000 is fine as-is**; revisit only if a future `attempt-N.json` shows
`compact_boundaries > 0` on a ticket that didn't need it (i.e. the window is smaller than
it needs to be for that archetype), or if `--max-turns` ceilings rise enough to push live
context past ~500k before a ticket naturally finishes.

## Gate status

This ticket's gate is "measured cache hit-rate delta on next worker runs vs baseline" —
that requires runs made *after* this change ships, through the real fworker harness (the
8 baseline files predate fworker.py per TOKEN_BASELINE.md's own caveat). Not measurable in
this session without spending new quota on a live dispatch. Next foreman-tok ticket that
pulls a fresh `token_baseline.py`-style rollup from `events/*.jsonl` +
`~/.claude/projects/*/<session_id>.jsonl` (TOKEN_BASELINE.md's own stated follow-up) should
diff cross-session first-turn `cache_read_input_tokens` (expect: still ~0, given the
worktree-cwd ceiling in (1)b, unless a future ticket also addresses cwd-level divergence)
against this baseline's confirmed-zero.
