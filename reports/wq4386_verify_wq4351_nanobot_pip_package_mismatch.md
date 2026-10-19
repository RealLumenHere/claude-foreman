# wq#4386 — verify-pass on wq#4351 (LIMPET RESEARCH: dissect NanoBot (HKUDS))

PR `claude-foreman#2279` (http://100.121.17.5:3000/lumen/claude-foreman/pulls/2279),
commit `ee431d8d` on branch merged into `main`, single new file
`research/WQ4351_NANOBOT_DISSECTION.md` (280 insertions, no prior content). Author:
claude-sonnet-5 (fworker-wq4351). Verifier: claude-sonnet-5@high (fresh instance,
wq#4386).

Fetched the PR via `git fetch origin refs/pull/2279/head` from the worktree (the
plain `curl .../pulls/2279.diff` the ticket's worker-guidance suggested 404s
unauthenticated — Gitea gates that path and `git`'s stored credential helper is the
working route; noted for whoever hits this next).

## Method

Archetype 8: read the doc cold, then independently re-checked its primary-source
citations against live sources rather than trusting the diff's own word — same
method `reports/wq4387_verify_wq4382_nanoclaw_errata.md` used on the sibling
NanoClaw dissection. `github.com/HKUDS/nanobot` is a real, current repo (not fleet
fabrication, instinct 8 clear): GitHub REST API confirms `created_at:
2026-02-01T07:16:15Z`, MIT license, Python, 49,190 stars / 8,648 forks as of today.

## Claims independently re-checked and confirmed

1. **PR #554 "feat(tools): add mcp support"** — GitHub API
   (`api.github.com/repos/HKUDS/nanobot/pulls/554`) returns title, author
   (`SergioSV96`), merge date (`2026-02-15T07:03:14Z`), and a body that matches the
   doc's quotes verbatim: *"~120 lines of code across 5 files, using the official
   MCP Python SDK,"* the five touched files (`nanobot/agent/tools/mcp.py`,
   `nanobot/config/schema.py`, `nanobot/agent/loop.py`, `nanobot/cli/commands.py`,
   `pyproject.toml`), isolated-task multi-server connection, and custom-auth-header
   support. All four of those files still exist in the current tree.
2. **`core_agent_lines.sh` self-check script** — doesn't exist in the live
   `main` tree or current README (checked both directly), but *did* exist exactly as
   described as of the `2026-03-14` README snapshot (commit `a1241ee6`): *"📏
   Real-time line count: run `bash core_agent_lines.sh` to verify anytime"* — and the
   script itself, fetched from that same commit, is a real 17-line bash file that
   counts core lines "excluding channels/, cli/, providers/ adapters," matching the
   doc's description exactly. Not fabricated, just since removed as the project
   matured past the LOC-marketing phase — the doc doesn't claim it's still live, so
   this isn't a contradiction, just a staleness note worth flagging.
3. **"🔬 Research-Ready" / "🪶 Ultra-Lightweight ~4,000 lines ... 99% smaller than
   Clawdbot"** Key Features bullets — verbatim matches in both the earliest
   (2026-02-04) and March README snapshots. Current README has dropped this bullet
   list entirely (it's grown into a bigger WebUI-based product); again a staleness
   note, not a fabrication, since the doc frames these as "what NanoBot is" at the
   point its sources were gathered, not asserted as still-current marketing copy.
4. **Architecture class names** — `class LLMProvider(ABC)` in
   `nanobot/providers/base.py` and `class BaseChannel(ABC)` in
   `nanobot/channels/base.py` both confirmed verbatim in the live tree, matching the
   doc's architecture diagram terminology exactly.

## Contradiction found: the install instruction installs the wrong package

The doc's opening paragraph states, as fact: *"MIT licensed, `pip install nanobot` →
`nanobot onboard`."* This is wrong and reproducibly so:

- Every README snapshot checked — earliest (2026-02-04), mid (2026-03-14), and
  current (2026-10-19) — consistently gives the real install command as
  `pip install nanobot-ai` (or `python -m pip install nanobot-ai`), never bare
  `nanobot`. The PyPI badge in the README itself points at
  `pypi.org/project/nanobot-ai/`.
- `pip install nanobot` (the doc's literal command) is a **different, unrelated
  real PyPI package** — confirmed via `pypi.org/pypi/nanobot/json`: a "Minimalist
  robot navigation framework using cascading decision nodes" for NVIDIA Jetson
  robots, published by a "NanoBot Team," with its own unrelated CLI. Running the
  doc's instruction literally installs the wrong software and `nanobot onboard`
  would then fail or do nothing meaningful against it — the one command in the doc
  anyone would actually try to run is broken.
- `nanobot onboard` itself is real and correctly named (confirmed across all three
  snapshots) — only the package name in the pip invocation is wrong, most likely
  copied from a secondary source (press/wiki paraphrase dropping the `-ai` suffix)
  without checking the primary source (the PyPI page) the way instinct 8 requires.

This is a narrow, single-line defect, not a wholesale architecture problem — the
MCP/provider/channel/memory/skills architecture sections all independently checked
out against primary sources (see above). But it is a concrete, falsifiable,
reproducible factual error in a shipped research deliverable, which is what
archetype 8 exists to catch.

## Tried to break it further

- Checked whether `SpawnTool`/the "15 vs. 4 iteration" subagent-budget claim has a
  live primary-source match: current code names the class `SubagentTool`
  (`nanobot/agent/tools/subagent.py:59`), not `SpawnTool`, and that file's git
  history only goes back to 2026-10-04 — it didn't exist under this path in the
  Feb/March snapshots either. Inconclusive either way (could be a rename of an
  earlier differently-pathed file, or a press-garbled name) — flagging as unresolved
  rather than a second confirmed contradiction, since I couldn't find a primary
  source nailing down "SpawnTool" as ever having been the real name.
- Checked the ~16k-lines-full-repo and star-count (32k–41k) numbers: the doc already
  explicitly flags source disagreement on both rather than picking one silently, so
  these aren't contradictions, just honestly-hedged claims I didn't re-litigate.

## Verdict

**CONTRADICT.** One load-bearing, user-facing factual claim — the installation
command in the doc's opening sentence — is wrong and would send a reader to install
an unrelated package. Everything else independently checked (PR #554's content, the
MCP architecture, the LLMProvider/BaseChannel class names, the historical
LOC-marketing claims) held up verbatim against primary sources.

Per archetype 8, this escalates to opus-5@high for judgment: whether a one-line
correction (`pip install nanobot-ai`) lands as a follow-up fix to the existing doc,
given the rest of the deliverable is otherwise sourced correctly, or whether the
single error is reason enough to re-audit the doc's other unlinked/unquoted factual
assertions before calling it done.

Escalation ticket: wq#4401 ([judgment] wq#4386 NanoBot dissection: verify-pass
CONTRADICTED — pip install command names the wrong package), model claude-opus-5,
effort high, deps wq4386.

No ISSUE-LOG append: the Gitea `.diff` 404 (resolved via `git fetch
refs/pull/<n>/head`) is a tooling note folded into this report's Method section, not
a blocking environment gap — it didn't stop the verify pass, just changed the
fetch method.
