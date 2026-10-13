# wq#3791 — verify wq#3786: Install REA (Reverse Engineer Anything) on Lumen + Maksoor

Verdict: **CONFIRM**. No contradiction found; no escalation to opus-5@high.

PR: http://100.121.17.5:3000/lumen/claude-foreman/pulls/1786 (merged `b335c09c4d`,
now `3fb1b18d` on `main`, which is this verify worktree's HEAD). The PR UI/API
404s for an unauthenticated `curl` from this worktree (repo access requires
auth this session doesn't have) — not treated as fleet evidence per instinct 8,
since the repo/commit is directly readable from the local git history anyway.
Diff is docs-only: 48 new lines, one new file, `TOOL_REGISTRY.md`. No install,
no MCP registration, no script execution — the author declined the ticket's
literal ask (`npx rea-agents setup` on Lumen, install on Maksoor, register as
an MCP server through Commander) and wrote up why. The verify pass is about
whether that decision and its stated evidence hold up, not about a diff's
runtime logic — there's no code path to exercise.

## What I checked

1. **Diff fidelity**: `git -C /home/lumen/foreman show b335c09c4d -- TOOL_REGISTRY.md`
   matches the file currently on disk at this worktree's HEAD (`3fb1b18d`)
   exactly — 48 insertions, no further edits since merge.

2. **REA is real, not fabricated** (instinct 8 check — the author introduced
   `github.com/morluto/rea` / npm `rea-agents` into the ticket, so a
   not-found would only indict the author, not the fleet): ran
   `fleet-search web`/`github` independently.
   - `github.com/morluto/rea` — real repo, indexed by Google CSE as
     "Reverse engineer anything with agents, from app behavior down to
     native binaries," "2 days ago" at search time.
   - Independent press (Medium, dev.to-class outlets, Eigent AI blog) all
     describe the same MCP server/CLI bridging coding agents to Ghidra,
     ADB/APK, .NET, Electron, packet analysis — matches the PR's
     description of what the tool does.
   - Star-velocity claim corroborated independently: a Medium article
     (not sourced from the PR) is titled "...Hits 140,000 GitHub Stars in
     Five Days," and a Reddit r/BestGitHubRepos post from ~5 days prior
     put it at 18K. That's three independent data points (18K → 140K →
     the PR's own observed 171K) converging on the same explosive,
     days-not-months growth curve the PR calls an astroturf signature —
     not something the author invented or cherry-picked.
   - No socket.dev/Snyk/npm-advisory hit for `rea-agents` specifically,
     consistent with the PR's "absence of a flag, not a clean bill of
     health" framing. Broader search surfaced active, ongoing npm
     supply-chain campaigns (Shai-Hulud v2, TanStack compromise, both
     2025/2026) as current background risk — if anything this strengthens
     the hold rather than contradicting it.

3. **Nothing was actually installed or registered** (the load-bearing
   factual claim — "did not run npx rea-agents setup or register any MCP
   server"):
   - `find`/`grep` across every root this session can reach
     (`/home/lumen/foreman`, `/home/lumen/bin`, `/home/lumen/kb`,
     `/home/lumen/sentinel`, `/home/lumen/agents/work-queue`) for
     `rea-agents`/`morluto`: the only hits are `TOOL_REGISTRY.md` itself,
     `kb/ISSUE-LOG.md` row 1277, and one unrelated fixture file
     (`ops/wq3576-trending-velocity-feed/fixtures/daily_all_2026-10-07.xml`)
     whose match is coincidental filename/content noise, not REA-related.
     No `node_modules`, no install script, no npx cache artifact.
   - Grepped every file under `/home/lumen/foreman` and `/home/lumen/bin`
     containing `mcpServers`/`mcp_servers` (Commander's MCP registration
     surface): none reference REA/rea-agents/morluto.
   - Fleet state (`lumen_state.py current`) shows no `maksoor.*` key
     mentioning REA/rea-agents; Maksoor's only recent tool-install entry
     is the unrelated `e-waste-flasher` ship (2026-10-01). Can't SSH to
     Maksoor itself from this session to double-negative-prove absence,
     but there is no positive evidence of install anywhere the author
     or this session could have left a trace, and the diff contains no
     commands that would have reached Maksoor.

4. **Sandbox-allowlist claim** ("`npx *`/`npm install *` are allowlisted
   for this worker, so nothing would have stopped a live install"):
   confirmed directly — `harness/worker-settings.json` lists
   `Bash(npx *)`, `Bash(npm install *)`, `Bash(npm install)`,
   `Bash(npm ci *)` in the allow list. The claim that withholding was a
   judgment call rather than a forced gate is accurate.

5. **Precedent citation** (`kb/tooling-findings.md` gating
   Langfuse/Gatus adoption behind a findings writeup): file exists,
   content matches — "My recommendation: install Langfuse FIRST,"
   Gatus as the paired health-check tool, written as a findings doc
   rather than an install log. The analogy holds.

6. **ISSUE-LOG.md row 1277**: present, correctly numbered (1275/1276
   precede it, 1278 follows, no duplicate `| 1277 |` row — the exact
   collision class wq#3174 logged), and its text matches what the PR
   commit message and `TOOL_REGISTRY.md` both describe. Consistent with
   having gone through `issue_log_append.py`'s row allocation rather than
   a guessed max+1 edit.

7. **Scope check against the ticket's literal ask**: wq#3786's detail
   text asks for install-on-Lumen, Ghidra provider config, install-on-
   Maksoor, MCP registration via Commander, and a `TOOL_REGISTRY.md`
   update. The PR delivers only the last item, explicitly declining the
   rest. Under Tier-0 instinct 3 (full autonomy: decide and act; the only
   legitimate Frank-dependency is a physical act), a security hold on a
   2-week-old, virally-spiking, unaudited npm package with Ghidra +
   live-device-ADB + fleet-wide-MCP blast radius is a judgment call
   squarely in scope for the worker to make and record — not a
   mis-scoped block, and not parked on Frank (no `wq block` was called;
   the decision was made and documented in-repo, which is what instinct 3
   asks for).

## Judgment call reviewed, not re-litigated

Whether REA *should* eventually be installed is exactly the kind of
non-physical decision instinct 3 delegates to the worker. The PR doesn't
dodge the ticket — it does the security triage the ticket skipped, reaches
a conservative conclusion, and leaves a concrete reconsideration path (pin
a commit hash, sandbox the setup script, scope Maksoor's ADB access,
re-check after more scrutiny time). That's the documented-reasoning
pattern the autonomy instinct requires, applied to a case where the
expected value of "wait and verify" plausibly beats "install on sight
because of a star count," given fresh independent confirmation that the
star count itself is the viral-spike pattern, not organic signal.

No findings rise to contradiction. Confirming on the board.
