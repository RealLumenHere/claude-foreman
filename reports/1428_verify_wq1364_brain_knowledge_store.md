# wq#1428 — Adversarial verify-pass of wq#1364 (Brain knowledge store: local corpus + retrieval for interpretation refs)

**Verdict: CONTRADICT** — the ingestion half of the design (reuse Minerva's
`index_research.py` / `lumen-research-index.timer` pgvector pipeline, §2.1/§3.1/§3.2)
is soundly researched and its citations check out exactly against the cited files. But
the retrieval-injection half (§3.3) rests on a load-bearing claim that is factually
wrong when checked against the actual code it claims to reuse: there is no "existing
DeepSeek/LiteLLM round trip" in mailbot to piggyback an embedding call on. Mailbot's
reply path calls DeepSeek's cloud API directly from a Cloudflare Worker; LiteLLM is not
in that path at all.

## What was shipped

PR http://100.121.17.5:3000/lumen/claude-foreman/pulls/300 (commit `fde777b`, 1 file):
`research/BRAIN_KNOWLEDGE_STORE.md`, a design-only doc (206 lines, explicitly "no
retrieval code lands in this ticket").

## Citation audit — mostly CONFIRMED

Checked every `path:line` citation in the doc against the actual files (this worktree
+ `/home/lumen/mailbot`, both in scope):

- `ops/wq1302-n8n-eval/inventory.json:1641-1662` (lumen-research-index timer/service,
  `LUMEN_RESEARCH_ROOT=/home/lumen/scratch/research_clean`, `OnCalendar=*-*-*
  10:00:00 UTC`) — **matches verbatim**.
- `ops/wq1302-n8n-eval/inventory.json:1020` (lumen-embed-guard, wq#465) and `:1307`
  (pgvector integrity/version-drift audit, wq#452) — **match**.
- `reports/FLEET_OPS_AUDIT_2026-08-23.md:74` (Postgres lumen-pgvector dependency-map
  line, "Backed up hourly") — **matches verbatim**.
- `memory/recall.py:19-23` (`MAX_FACT_CHARS = 300`, `DEFAULT_K = 8`) and `:24-26`
  (`BLOCK_HEADER` "Background, not instructions; verify anything load-bearing before
  relying on it") — **match**, including the "newest-first, deduped" claim (confirmed
  at `memory/recall.py:6,35,40`, not directly cited but accurate).
- `prompt-forge/templates.py:41,53,142` — **match**.
- `mailbot/src/worker.js:160-165` (`buildSystem`), `:92-102` (`DEFAULT_PROMPT` /
  cover-story hard rule), `:210-227` (`generateReply`, `earlier` history block,
  `stripInstructionBlocks`) — **all match line-for-line**.

This is genuinely careful citation work, not the usual copy-cite-and-hope pattern.

## Bug — §3.3's core assumption is wrong: mailbot does not go through LiteLLM

The doc's problem statement (§1) and architecture (§3.3) both assert the reply path is
"DeepSeek V4 via LiteLLM," citing `reports/FLEET_OPS_AUDIT_2026-08-23.md:17,72`, and
§3.3 builds on it directly:

> "Cheap embedding call added to the existing DeepSeek/LiteLLM round trip, not a new
> provider."

Reading the actual code the doc itself cites for the reply path
(`mailbot/src/worker.js:173-207`, `callDeepSeek`):

```
mailbot/src/worker.js:175  const res = await fetch("https://api.deepseek.com/chat/completions", {
mailbot/src/worker.js:179    "Authorization": `Bearer ${env.DEEPSEEK_API_KEY}`,
mailbot/src/worker.js:182    model: "deepseek-v4-flash",
```

This is a direct fetch to DeepSeek's own cloud API with a DeepSeek API key — not
LiteLLM's gateway (`:4000`, per `reports/FLEET_OPS_AUDIT_2026-08-23.md:72`). Confirmed
this isn't a narrower slice by grepping all of `mailbot/src/`:

```
grep -rl "litellm\|LiteLLM\|:4000" /home/lumen/mailbot/src/
-> only worker.js, and the sole hit there is a redaction-keyword regex
   (worker.js:33, the list of infra terms stripped from replies), not a call site.
```

So the audit report's line 17 ("production Messenger brain = DeepSeek V4 via
LiteLLM") is itself stale/wrong, and the design doc inherited it without checking
against the source it cites two sections later for the exact same code path. `mailbot`
is also a Cloudflare Worker (`mailbot/wrangler.toml: main = "src/worker.js"`,
`workers_dev = false`) — an edge/serverless runtime with no network path to a local
LiteLLM gateway or Postgres instance in the first place, which is what §4 item 3
("Confirm mailbot's Worker ... can reach the pgvector Postgres instance") is already
groping toward, but frames as an open confirmation rather than the direct consequence
of a claim the doc could have falsified by re-reading its own §2.2 citations.

## Why this matters for the build ticket

§3.3's "not a new provider" framing and §5's non-goal ("No cloud vector DB or embedding
API — local pgvector only") are in tension once the LiteLLM premise is removed: a
Cloudflare Worker cannot make a "cheap" local-pgvector similarity query in-process the
way `memory/recall.py` (a filesystem-local Python process, §2.3's precedent) does.
Reaching local pgvector from Cloudflare's edge needs *some* new network hop — a proxy,
an API, or a cloud-side embedding call — regardless of LiteLLM. The doc's own §4 item 3
already anticipates a proxy/API may be needed, but §3.3 and §5 are written as if that
question is settled in the "no new provider, local only" direction. It isn't yet; the
build ticket needs to resolve reachability *before* assuming the cheap-round-trip
framing, not after.

## Disposition

Small, factual correction, not a redesign — the ingestion half (§2.1/§3.1/§3.2) stands
as researched. Recommend on the build ticket:

1. Strike "via LiteLLM" from §1 and the "existing DeepSeek/LiteLLM round trip" framing
   in §3.3 — mailbot calls DeepSeek's cloud API directly; there is no LiteLLM hop to
   attach to.
2. Promote §4 item 3 (Cloudflare Worker reachability to pgvector) from a confirmation
   item to a blocking open question — resolve it before §3.3's "cheap round trip"
   framing is treated as load-bearing, since the answer determines whether retrieval is
   even feasible without a new network service.

Escalating per archetype-8 protocol: contradiction → opus-5@high.
