# foreman-tok T0 — token-burn baseline (wq#629)

Source: Frank-provided Fable-5 token-reduction research (2 docs, 2026-08-15).
Premise filter: we run on Max 20x (no API) — token reduction matters because
session/weekly *limits* are token-driven (2026-08-15 governor breach, session
hit 96%), not because of per-token dollar cost.

Instrument: `token_baseline.py` in this directory, parses the 8 existing
`/home/lumen/foreman/_runs/*.jsonl` real session transcripts (the wq#613/614/
625 bootstrap builds — run under the interim ORCH-dispatched pattern per
BUILD_PLAN.md §4, since fworker.py itself didn't exist yet when these ran).
Zero new Anthropic spend: reads existing files only. Re-run: `python3
token_baseline.py > token_baseline.json`. This baseline is what every future
foreman-tok ticket should diff against.

## Per-ticket rollup

| ticket | title | sessions | turns | total tokens consumed¹ | cache hit rate | tokens/turn | cost |
|---|---|---|---|---|---|---|---|
| wq#613 | T1 governor daemon (sonnet-5) | 3 (orig + resume + land) | 293 | 526,564 | 97.6% | 1,797 | $9.34 |
| wq#614 | T2a design doc (fable-5) | 3 (orig + wave1 + wave2) | 154 | 221,289 | 96.4% | 1,437 | $10.53 |
| wq#625 | T2a build / PR#4 (sonnet-5) | 1 | 276 | 277,105 | 99.1% | 1,004 | $8.70 |

¹ "total tokens consumed" = `input_tokens + cache_creation_input_tokens +
output_tokens` (the three token classes actually billed against a turn;
`cache_read_input_tokens` is cache reuse, tracked separately for the hit-rate
column — see below for why it still matters on Max).

Full per-file breakdown (fixed prefix overhead, output share, thrash) is in
`token_baseline.json`.

## (a) Fixed prefix overhead per worker spawn

Estimated as the first turn's `cache_creation_input_tokens` (the system
prompt + tool schemas + settings pack, written to cache once and reused every
turn after). Ranges **4.7k–10.7k tokens** for a fresh session start (e.g.
fbuild-614.wave2: 4,734; fbuild-625: 10,695), but jumps to **120k–156k
tokens** for a `.resume`/`.land` follow-up session on an existing ticket
(fbuild-613.resume: 120,417; fbuild-613.land: 156,367) — those aren't a
bigger *fixed* prefix, they're re-priming the accumulated worktree/prior-diff
context a resume session has to re-establish before its own cache chain can
start. That's the real lever: a same-session `--resume` (staying inside one
cache chain) avoids this re-prime; a fresh-process resume (new session id,
even against the same ticket) pays it every time.

## (b) Cache hit rate per run + thrash sources

Cache hit rate (`cache_read / (input + cache_write + cache_read)`) is **89–99%**
across all 8 files — expected for a long single-session build where each turn
resends the full accumulated transcript, and it's fully cached after the
first write.

Thrash (defined as a turn-over-turn *drop* > 5,000 tokens in
`cache_read_input_tokens` — the cache chain breaking and re-priming from a
smaller base mid-file): **zero occurrences in any of the 8 files.** No
`compact_boundary` system frames appear in any of them either (checked
directly). So thrash isn't an in-session problem here — it happens exactly at
file/session boundaries (see (a) above): every `.resume`/`.land`/`.wave*`
file starts its own fresh cache chain, and that reset is where the cost
lives, not autocompact or mid-session cache churn.

## (c) Output-token share

Output tokens are **0.2%–3.3%** of the per-file total (input + cache-write +
cache-read + output). This looks small but is an artifact of prompt caching
in a long conversation: cache-read tokens accumulate every turn (the whole
prior transcript gets re-sent, now cached) while output per turn stays
roughly constant, so the ratio shrinks as a session runs longer — it is not
evidence of wasted output spend. The short contrast file
(fbuild-613.wave1.jsonl, 7 turns) has the highest output share (3.3%)
precisely because it's short: less accumulated cache-read to dilute against.

## (d) Tokens per closed ticket

See the rollup table's "total tokens consumed" column — wq#613: 526,564;
wq#614: 221,289; wq#625: 277,105 (wq#625's session actually hit
`error_max_turns` at 276 turns without closing the ticket in that file; PR#4
landed via a later session not captured in these transcripts). These three
numbers *are* the instrument other foreman-tok tickets should diff their
proposed changes against.

## `--strict-mcp-config` vs the wave-1 MCP-inheriting run

The ticket named `fbuild-613/614.wave1.jsonl` as the MCP-inheriting contrast
pair. Checked both directly (`mcp_servers` field in each file's `system/init`
frame): **only `fbuild-613.wave1.jsonl` actually has non-empty `mcp_servers`**
(8 servers: claude.ai Google Drive/Gmail/Calendar/Compute Engine/Linear/Slack/
CMS Coverage/Lumen Commander, all `needs-auth` or `pending` — inherited from
the ambient claude.ai integration state, unused, never authenticated).
`fbuild-614.wave1.jsonl` has `mcp_servers: []` throughout — it's a "wave 1"
*attempt*, not an MCP-inheriting session. Correcting the ticket's premise
here rather than reporting a comparison that isn't there.

The one real data point: `fbuild-613.wave1.jsonl`'s first-turn cache-write
(fixed prefix overhead) is 10,520 tokens with 8 inherited MCP servers present
vs a 4.7k–10.7k range across the 6 confirmed-`mcp_servers:[]` fresh-start
files — inside the same range, not clearly elevated. One 7-turn sample is too
thin to conclude inherited-but-unauthenticated MCP servers cost meaningfully
more prefix tokens; it's directionally consistent with "some," not proof of
"a lot." fworker.py already builds a clean env per spawn (`build_env()`
scrubs `ANTHROPIC_API_KEY`/`ANTHROPIC_BASE_URL`) but does not currently pass
`--strict-mcp-config` — worth a follow-up ticket to add it and re-sample,
since the mechanism (ambient claude.ai MCP inheritance bloating the tool
schema every spawn) is real even if this one sample doesn't quantify it well.

## What this baseline does NOT cover

These are real `claude -p` sessions from before fworker.py existed (run under
the interim ORCH pattern), not fworker-driven runs. Now that fworker is
built, future baseline updates should pull from `events/*.jsonl` (fworker's
own hook-emitted lifecycle events) cross-referenced with
`~/.claude/projects/*/<session_id>.jsonl` for the full stream, so the
baseline reflects the harness's actual settings pack / guard hooks / worktree
isolation rather than the bootstrap sessions that built them.
