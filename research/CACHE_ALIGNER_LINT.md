# CACHE_ALIGNER_LINT — headroom CacheAligner run against worker prompts + settings (wq#887)

## VERDICT: zero volatile content in any target. The two fire prompts have a layout defect worth ~600 tokens on paper and **0 tokens in practice** — CWD_STABILITY §(i) already proved the region is not cacheable.

Tool: `research/cache_aligner_lint.py`. Data: `research/cache_aligner_lint.json`.
Engine: headroom-ai 0.36.5, `CacheAligner` + `detect_volatile_content` (no `[ml]` extra,
no proxy, no API spend). Ref: HEADROOM_EVAL.md §3.

## 1. What was run

CacheAligner is a detector, not a rewriter — upstream returns
`transforms_applied=[]` unconditionally (`transforms/cache_aligner.py:366`), so "read-only
lint" is the only mode it has. It classifies whitespace-delimited tokens as `uuid`,
`iso8601`, `jwt`, or `hex_hash` by parsing them (`uuid.UUID`,
`datetime.fromisoformat`, base64 segment decode), no regex.

Three checks per target:

- **volatile** — `detect_volatile_content` per line, so findings carry line numbers.
  Settings files are walked as JSON so a finding names a key path, not a line.
- **tail** — every per-run placeholder must sit in the trailing block, leaving the static
  head byte-identical across spawns. This is the wq#630 §(3) invariant, now checkable.
- **spawn** — renders each template twice with two different tickets (887/633, distinct in
  every substituted field including the fire-gate paths) and measures the shared byte
  prefix, then runs CacheAligner over each rendering for its `stable_prefix_hash`.

## 2. Targets

`worker-settings-interim.json`, named in the ticket, no longer exists. It was superseded by
`harness/worker-settings.json` (T2A_WORKER_HARNESS_DESIGN.md:59; `fworker.py:65` reads that
path). Both survivors are linted. `harness/prompts/worker.md` is added because it, not
`FOREMAN_DISPATCH.prompt`, is what fworker actually renders and dispatches
(`fworker.py:859`); the operator-facing `FOREMAN_DISPATCH.prompt` is linted as asked. The
two fire prompts (wq#717) and `/home/lumen/CLAUDE.md` are added because they are worker
prefix content by the same argument.

| target | status | size | shared prefix across 2 spawns | alignment |
|---|---|---|---|---|
| `FOREMAN_DISPATCH.prompt` | CLEAN | 3,849B / ~1,106 tok | 3,840B (~1,102 tok, 99.9%) | 100 |
| `harness/prompts/worker.md` | CLEAN | 2,985B / ~865 tok | 2,899B (~833 tok, 94.8%) | 100 |
| `harness/prompts/fire-diagnosis.md` | FINDINGS | 3,360B / ~902 tok | 1,052B (~276 tok, 29.7%) | 100 |
| `harness/prompts/fire-apply.md` | FINDINGS | 3,335B / ~897 tok | 1,037B (~266 tok, 29.7%) | 100 |
| `worker-settings-general.json` | CLEAN | 1,643B / ~515 tok | n/a | n/a |
| `harness/worker-settings.json` | CLEAN | 1,688B / ~608 tok | n/a | n/a |
| `/home/lumen/CLAUDE.md` | CLEAN | 1,201B / ~341 tok | 1,201B (100%) | 100 |

Token counts are tiktoken approximations — an exact count needs an Anthropic client and the
guardrails forbid API spend. Same limitation INSTRUCTION_DIET.md and CWD_STABILITY.md logged.

## 3. Findings

**No timestamps, session tokens, UUIDs, JWTs, or hex hashes in any target.** Alignment score
100/100 on every prompt. The detector is not vacuous: `--selfcheck` runs it against a fixture
carrying a UUID, an ISO-8601 stamp, an MD5 digest and a misplaced static block, and asserts
all six signals fire.

**The two fire prompts strand ~600 tokens of static instruction behind their first per-run
value.** `fire-diagnosis.md` puts `{{PLAN_PATH}}` at byte 1,024 of 3,360 and
`fire-apply.md` puts `{{DRIFT_PATH}}` at byte 1,009 of 3,335, then continues with a full
block of static procedure. Shared prefix across two dispatches is 29.7% for both, against
99.9% for `FOREMAN_DISPATCH.prompt` and 94.8% for `worker.md`. The reason is chronological:
wq#630 §(3) reordered the two prompts that existed then; the fire prompts arrived with
wq#717 and never got the treatment.

**The settings files are inert with respect to the prefix, and their hooks stay inert at
runtime.** Both files are static JSON with no volatile values. `guard.sh`'s `--lifecycle`
path calls `log_event`, which appends one JSONL line to `$EVENTS_DIR` and exits 0
(`guard.sh:27-49`) — nothing on stdout, so a SessionStart/Stop/SessionEnd hook injects
nothing into the transcript. There is no per-turn volatile content entering context from
either settings pack.

## 4. Against TOKEN_BASELINE

TOKEN_BASELINE §(b) reports **89–99% cache hit rate** across all 8 baseline files with
**zero turn-over-turn `cache_read` drops above 5,000 tokens** and no real compaction events.
This lint explains the clean half of that number: there is nothing volatile in the
dispatched prompt or the settings pack to cause intra-session thrash, so the in-session hit
rate is bounded only by transcript growth.

The lint does **not** touch the expensive half. TOKEN_BASELINE §(a) puts the fixed prefix at
4.7k–10.7k tokens per fresh spawn and 120k–156k for a `.resume`/`.land`, and that re-prime
is where the cost lives. HEADROOM_EVAL.md §3 already called this: CacheAligner cannot fix a
new-process cache chain. Confirmed, not contradicted.

## 5. Why the fire-prompt finding is worth 0 tokens

CWD_STABILITY.md §(i) enumerated every distinct first-turn `cache_read` value across 108
transcripts. Exactly two are reachable per toolset — end-of-tools (16,236 opus-5 / 24,649
sonnet-5) and end-of-system (21,723 / ~30,355). No intermediate value occurs anywhere. The
dispatched prompt is the first *user* message, past both breakpoints, so no rearrangement of
its bytes is independently cacheable. wq#705 reached the same conclusion for the reorder
that wq#630 shipped: "expect zero measurable win from it."

So the fire-prompt defect is a hygiene finding, not a savings finding. Fixing it costs a
five-minute reorder and buys nothing measurable today; leaving it costs nothing either. It
is recorded here so the number is known rather than assumed, and so the invariant is
enforced automatically if a future CLI ever exposes an interior breakpoint. No fix is
proposed under this ticket — the prompts are wq#717's, and editing live worker prompts to
chase a provably-zero win is not worth the blast radius.

One volatile token does reach every worker prefix and this lint cannot see it: the harness
injects the current date into the context block at spawn. It lives in no file, so a
file-based detector will never find it. Whether it sits before or after the end-of-system
breakpoint is not answerable from transcripts (they record messages, not the assembled
system block) — the same method limit CWD_STABILITY §(ii) hit. Left open; the corpus shows
`cr_t1` holding at 24,649 across three different calendar days, so it does not disturb the
end-of-tools breakpoint that workers actually hit.

## 6. Running it

```
python3 -m venv /tmp/hr && /tmp/hr/bin/pip install headroom-ai   # no [ml] extra
/tmp/hr/bin/python research/cache_aligner_lint.py            # human-readable
/tmp/hr/bin/python research/cache_aligner_lint.py --json     # regenerates the artifact
/tmp/hr/bin/python research/cache_aligner_lint.py PATH ...   # override targets
/tmp/hr/bin/python research/cache_aligner_lint.py --selfcheck
```

Exit 0 clean, 1 findings, 2 setup error. `FOREMAN_ROOT` overrides the target root. Sub-ms
per target; the venv is the only cost. Re-run it after any edit to a prompt template or a
settings pack — that is the regression this ticket leaves behind.
