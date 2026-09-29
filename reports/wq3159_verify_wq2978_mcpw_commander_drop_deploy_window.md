# wq#3159 — verify wq#2978 [mcpw] attribute Commander drops from Workers Logs once wq#2925 ships

Archetype 8 adversarial verify pass. Author: claude-sonnet-5. Verifier: claude-sonnet-5@high
(fresh instance, same model as author).

PR: http://100.121.17.5:3000/lumen/claude-foreman/pulls/1427 (merged `772629af`, docs-only,
`reports/wq2978_deploy_window_finding.md` new file, 56 insertions, no other files touched).

## Claim under test

wq#2978 was gated on Workers Logs observability existing so 400/401s on `/register`,
`/authorize-complete`, `/token`, `/mcp` could be attributed. The PR's report claims: (1) the
observability config (`9dedaee`, wq#2925 follow-up) is live as of the `171ab1e` deploy recorded
in wq#2925's own `done` notes (`done_at 2026-09-29 19:02:00`); (2) it is too early (minutes, not
48h) to run the actual attribution query, so the pass correctly stops short and files wq#3158,
gated not to dispatch before 2026-10-01 19:02:00 UTC.

## What I checked

- Read the full report (57 lines) and the full PR diff cold — confirmed it is the only file
  changed, no worker code touched, matching its own "read-only, no worker changes" framing.
- Re-ran every command in the report's "Reproduction" section independently, from scratch:
  - `git -C /home/lumen/projects/mcp-oauth-worker log --oneline -1 9dedaee` → identical message.
  - `git -C /home/lumen/projects/mcp-oauth-worker merge-base --is-ancestor 9dedaee 171ab1e` →
    exit 0, confirmed ancestor.
  - `git -C /home/lumen/projects/mcp-oauth-worker show 9dedaee` → diff matches report verbatim:
    `mcp-oauth-worker/wrangler.toml`, `+8` lines, adds `[observability]` / `enabled = true` /
    `head_sampling_rate = 1`, config-only.
- **Adversarial check the report's reproduction steps don't cover**: ancestry alone only proves
  `9dedaee` is in `171ab1e`'s history, not that the config survived to that exact tree state (a
  later commit before `171ab1e` could have reverted it). Pulled the file directly at that
  revision — `git show 171ab1e:mcp-oauth-worker/wrangler.toml` — and confirmed the
  `[observability]` block is present in the tree at `171ab1e` itself, not just somewhere in its
  ancestry.
- **Cross-checked the deploy-commit citation against its source ticket**: pulled wq#2925 directly
  (`wq show 2925`) rather than trusting the report's paraphrase. Its title is unrelated on its
  face (`deploy GET /pool/health: set MCP_POOL_HEALTH_SECRET, wrangler deploy, full MCP
  initialize e2e`) and `171ab1e`'s own commit message is *also* unrelated on its face
  (`chrome-mcp-oauth-worker: HEAD-only deploy guard... (wq#3114)`, a same-repo but
  different-subproject commit). This looked like a possible citation error — `wrangler secret
  put` and `wrangler deploy` are runtime actions, not commits, so wq#2925's notes record
  whatever the repo's HEAD happened to be at the time of that action, which just happened to be
  `171ab1e` (an unrelated commit that had already landed hours earlier the same day). Confirmed
  this reading directly: `git -C /home/lumen/projects/mcp-oauth-worker log --oneline -1 HEAD` on
  the live checkout is `171ab1e` on `master`, matching wq#2925's citation, and the file-content
  check above confirms observability config is present at that exact HEAD. Not a citation error.
- Confirmed no `wrangler.toml` changes landed between `171ab1e` and current HEAD
  (`git log 171ab1e..HEAD -- mcp-oauth-worker/wrangler.toml` → empty), so nothing has since
  reverted the config.
- Confirmed wq#3158 (the follow-up) actually exists with the properties the report claims:
  `deps: 2925`, `status: blocked`, detail carries the same `171ab1e`/`9dedaee` citation, and an
  embedded liveness gate `python3 -c 'import sys,time; sys.exit(0 if
  time.time()>=1790881320 else 1)'`. Decoded the epoch independently
  (`date -u -d @1790881320` → `Thu Oct 1 19:02:00 UTC 2026`) — matches the claimed 48h-after
  window exactly. Ran the gate script live: exit 1 (window still closed, as of 2026-09-29),
  confirming the gate is not prematurely open and wq#3158 cannot be picked up early by a worker
  that trusts the gate.
- Verified the underlying doctrine point: the report does **not** overclaim — it says the
  observability *pipe* is live (config deployed), not that Workers Logs data has been queried
  or attributed. That distinction is preserved correctly throughout, and the actual
  status-code/path query is explicitly deferred to wq#3158's post-window run. No premature
  attribution claim slipped in anywhere in the report text.

## Verdict

**CONFIRMED.** Every factual claim in the report — commit ancestry, config-file content at the
cited deploy revision, the wq#2925 done-notes citation (which looked suspicious at first glance
because both commit messages are on unrelated subprojects, but resolves correctly once you
account for `wrangler deploy` being a non-commit-producing runtime action), and wq#3158's
existence/dependency/gate — reproduce exactly under independent re-verification. The gate script
correctly still returns "not yet" today. No worker code was touched by this PR. No contradiction
found; not escalating to judgment.
