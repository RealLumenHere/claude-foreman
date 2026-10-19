# wq#4388 — Adversarial verify-pass of wq#4371 (PR#2283: LIMPET RESEARCH self-improvement + harness dissection)

**Verdict: CONFIRM (opus-5@high tie-break) — two defects, one worse than the one I
found.** Every falsifiable claim in the doc other than §3 (Dify/Sim) — local repo
citations and external sourced facts alike — reproduced correctly, several down to the
exact byte or git blob sha. §3 has (1) a conflated citation (not outright invention, per
the opus-5@high pass below) and (2) a materially false headline claim on current `main`:
a real Dify/Sim dissection (`research/WQ4365_VISUAL_WORKFLOW_BUILDERS_DISSECTION.md`)
already exists and is an ancestor of this doc's own merge commit, contradicting §3's
framing that "this repo hasn't dissected [Dify/Sim] yet."

## What was shipped

PR#2283 (merged at `3ff18e88`, on `main`) added
`research/WQ4371_SELF_IMPROVEMENT_HARNESS_DISSECTION.md` (288 lines): a dissection of
AgentScope 2.0's `HarnessAgent`/Middleware opt-in-capability pattern and GBase's
advertised-vs-wired recursive-self-improvement loop, plus a reference note on Dify/Sim,
evaluated against Limpet's agent core (`design/wq3806_limpet_architecture.md` §5-§6).

## Probe

Same shape as precedent (wq#4361/wq#4370 verify passes): (1) re-derive every external
factual claim from fresh, independent sources rather than trust the doc's own citations,
(2) re-check every local file:line citation against the actual files in this worktree,
(3) try to catch the framing claims (Limpet's agent core is still design-only; subagent
fan-out has no Limpet use case) as stale or overstated.

## External citations — re-sourced fresh, not trusted from the doc

All confirmed verbatim or to the exact falsifiable value:

- **AgentScope Java release notes** (`github.com/agentscope-ai/agentscope-java/releases`,
  fetched live via `api.github.com/repos/agentscope-ai/agentscope-java/releases`): the
  doc's blockquote — *"HarnessAgent: Extends ReActAgent through Middleware and Toolkit
  channels, adding workspace, memory, sandbox, subagents, skills, and plan mode as
  engineering infrastructure — the core reasoning loop is preserved, only augmented."* —
  is present **verbatim** in the releases payload (`grep -c` on the distinctive substring
  returned 1).
- **GBase `lib/evolution_engine.py`** (`api.github.com/repos/garyqlin/gbase/contents/
  lib/evolution_engine.py`): `"size": 0`, blob sha
  `e69de29bb2d1d6434b8b29ae775ad8c2e48c5391` — matches the doc's claim exactly, including
  the specific sha it cites as "the canonical SHA of a completely empty file."
- **GBase `lib/mirror.py` / `lib/experience.py` / `lib/lifeline.py`** sizes: fetched live
  via the same API — `63482` / `25959` / `14627` bytes respectively. All three match the
  doc's cited byte counts exactly.
- **GBase `tools/rollback.py`**: fetched raw (`raw.githubusercontent.com/garyqlin/gbase/
  main/tools/rollback.py`) — 72 lines exactly, and contains all four cited function names
  (`rollback_list`, `rollback_restore`, `rollback_cleanup`, `rollback_stats`) plus the
  cited docstring phrase "波段一：刹车装置" ("Wave 1: braking device") verbatim.

These are the doc's most falsifiable external claims (exact byte counts, an exact git
blob sha, a verbatim multi-clause quote) and all reproduced exactly — strong evidence the
author did the live `curl`/API research it claims, not parametric recall.

## Local citations — checked directly against this worktree

Confirmed, several verbatim:

- `design/wq3806_limpet_architecture.md` §5 (lines 233-257, workspace isolation /
  containment) and §6 (lines 258+, obligation gate / critic pass) — confirmed verbatim
  against the doc's framing and quotes.
- `limpet/workspace_cli.py:13,27,209,418` — "obligations verify" exit-3 behavior —
  confirmed (`help="the agent verify-step gate: exit 3 while any obligation is open"`).
- `limpet/limpet_tools.py:21`, `limpet/registry_trust.py:159,254` — sandbox/tier-policy
  language — confirmed verbatim at the cited lines.
- No `agent`/`orchestrator`/`hunter` module exists under `limpet/` — confirms the "agent
  core is still design-only" framing is not stale.
- `research/WQ4368_ENTERPRISE_META_FRAMEWORKS_DISSECTION.md:241-252` (Tier-1 plan/execute
  recommendation) — confirmed, matches the doc's "reuse that conclusion" claim.
- `research/WQ4367_ORCHESTRATION_DISSECTION.md` — "no multi-ticket backlog to pull from
  inside a single investigation" — confirmed present (line 253), supporting the doc's
  fan-out-skip claim.

## The confirmed contradiction

Doc lines 171-176 (§3, Dify/Sim):

> `grep -rn -i "dify\|Sim Studio\|workflow-as-mcp" research/ design/` across every
> WQ43xx/WQ44xx dissection... returns zero substantive hits — one false-positive grep
> match on "modify," and a single passing mention of Dify as a container in
> `research/FLEET_SERVICE_ATLAS.md:86`'s inventory list (Open WebUI, LibreChat, Dify named
> as chat/agent-interface containers already running on the fleet, no architectural
> discussion).

Checked directly against `research/FLEET_SERVICE_ATLAS.md` in this worktree (588 lines
total):

1. **Line 86 is not about Dify at all.** It reads: `| **desktop-commander-mcp :3100** |
   claude.ai browser sessions (this audit), mcp-pressure-guard restarts it, node-killer
   reaps its children, dc-config-guard/dc-patch-tripwire protect its config/patches |` —
   entirely unrelated content.
2. **The actual Dify/LibreChat mention is at line 35**, a table row: `searxng/vane
   librechat  dify (11 ctrs)  plausible(3)  mongodb  minio  glance  actual-budget
   postgres-mcp×2`.
3. **"Open WebUI" does not appear anywhere in the file.** `grep -ni "webui"
   research/FLEET_SERVICE_ATLAS.md` returns nothing, and `git log -p --follow` over the
   file's entire history (5 commits, oldest `2f076532` through `e61dd579`) also returns
   nothing for "webui" in any revision. This rules out the wq#4361-precedent explanation
   (an unrelated later commit shifting line numbers) — `e61dd579` (the file's last edit)
   is a confirmed ancestor of `3ff18e88` (this PR's commit), so the file had its *current*
   content, unchanged, at the time this doc was authored. The citation was wrong from the
   moment it was written, not drifted afterward.

So the doc invents a detail ("Open WebUI" named alongside LibreChat/Dify) that is not in
the source in any version, and cites a line number that points at unrelated content. The
underlying *substantive* claim this citation supports — Dify gets only a passing,
non-architectural mention in the fleet atlas — is independently true (line 35 is exactly
that: a container-inventory list entry, no architectural discussion). The defect is in the
citation's specifics, not the conclusion it's used to support, but it is still a fabricated
detail attributed to a named source in a doc whose own "Sourcing note (instinct 8 —
anti-fabrication)" opens by promising nothing is extrapolated from parametric knowledge.

## Opus-5@high tie-break (mandatory: author and verifier are the same model)

A fresh opus-5@high instance independently re-checked the dispute from scratch (own
`grep`/`sed`/`git log -p --follow`/`git merge-base --is-ancestor` calls, not trusting my
line numbers or quotes) and returned **VERDICT: CONFIRM**, with two refinements:

1. **Not pure fabrication — a conflation.** The author's own cited grep
   (`dify\|Sim Studio\|workflow-as-mcp` over `research/ design/`, run against the
   pre-rebase snapshot `3cce9460`) produces two adjacent hits:
   `research/DIGEST_Fleet_Monitoring_2_md.md:86` ("Open WebUI as the conversational
   interface to LiteLLM... plus LibreChat and Dify in the container set") and
   `research/FLEET_SERVICE_ATLAS.md:35` (the container-inventory row). The citation as
   written takes the **line number and names from the first hit** and the **file path
   from the second** — a transcription/merge error across two adjacent grep results, not
   an invented source. It still misdescribes content either way: in the digest, Open
   WebUI is a requirement (R19) marked HAVE because *other* interfaces cover it, not
   itself a running fleet container — only LibreChat and Dify are containers. The fix is
   to cite `FLEET_SERVICE_ATLAS.md:35` and/or `DIGEST_Fleet_Monitoring_2_md.md:86` and
   drop "Open WebUI" as a container claim. Low-to-moderate severity; the claim this
   citation supports (Dify gets only a passing, non-architectural mention) still holds.
2. **A more serious finding I missed: §3's headline claim is false on current `main`.**
   `research/WQ4365_VISUAL_WORKFLOW_BUILDERS_DISSECTION.md` — 167 lines, six sections
   (`## 0` problem framing through `## 5` caveats), titled "Dissection: visual workflow
   builders (Dify, Sim) vs the Limpet Target Device Wizard" — is a real, substantive Dify
   + Sim architectural dissection. It landed at commit `6849c305`
   (2026-10-19 05:12:02), and `git merge-base --is-ancestor 6849c305 3ff18e88` confirms it
   as an ancestor of wq#4371's own merge commit (`3ff18e88`, 05:21:04) — i.e. WQ4365 is
   present and reachable on `main` today. The wq#4371 branch, per its reflog, was created
   at 05:10:25 from `f90c1914`, **before** WQ4365 landed at 05:12:02 — `git merge-base
   --is-ancestor 6849c305 3cce9460` (the author's pre-rebase tip) returns false. So the
   author's claim was true in their own branch at authoring time, and the merge/rebase
   onto `main` made it false: §3's title ("a reference pointer the ticket names but this
   repo hasn't dissected yet") and its body ("that claim does not hold up... returns zero
   substantive hits") are both wrong as merged, and the WQ43xx list the doc enumerates
   conspicuously skips straight from WQ4364 to WQ4367 — WQ4365 and WQ4366 are absent from
   it. A reader taking this doc at face value today would wrongly conclude no Dify/Sim
   dissection exists and either redo the work or miss WQ4365's actual conclusions
   (§4 "Decision").

This is a concurrent-ticket race, not author sloppiness at authoring time — but as merged
on `main`, it is a materially false claim shipped in the final doc, which the "ordinary
drift" precedent (wq#4374: a line-number citation drifting ~12 lines) does not cover,
since there the doc's *conclusion* stayed true and only a line number moved. Here the
doc's conclusion is wrong.

## Adversarial checks that did not break it

- Tried the wq#4361-precedent "drift, not fabrication" explanation (a later concurrent
  ticket moved the cited line). Ruled out by `git merge-base --is-ancestor
  e61dd579 3ff18e88` (true) and a full-history grep for "webui" (zero hits ever).
- Spot-checked whether the external GBase/AgentScope numbers could be approximated or
  misremembered rather than fetched live — four independent exact-value checks (one git
  blob sha, three byte counts, one 72-line count, one verbatim multi-clause quote) all
  landed exactly, which is not consistent with parametric guessing.
- Checked the "agent core is still design-only" and "fan-out has no Limpet use case"
  framing claims for staleness — both confirmed against current repo state (no agent
  module exists; `WQ4367` holds the same conclusion on independent re-check).

## Bottom line

**CONFIRM, both findings.** Everything outside §3 — roughly a dozen external facts across
two frameworks and seven local repo citations — reproduced exactly, several to an exact
byte count or git blob sha, which weighs against this being part of a broader fabrication
pattern. §3 (Dify/Sim) has two real defects: a conflated (not invented) citation that
needs its line/path corrected and its "Open WebUI" container claim dropped, and — found
by the opus-5@high tie-break, not by me — a headline claim ("this repo hasn't dissected
[Dify/Sim] yet") that is false on `main` today because `research/
WQ4365_VISUAL_WORKFLOW_BUILDERS_DISSECTION.md` landed 9 minutes before wq#4371's branch
point reached main and is now in its ancestry.

**Disposition:** per fleet precedent (wq#4375→wq#4384: judgment pass finds the defect,
a separate standard-engineering ticket applies the forward-fix, this ticket does not
re-implement) — filing a follow-up `[fix]` ticket to correct §3 of
`research/WQ4371_SELF_IMPROVEMENT_HARNESS_DISSECTION.md`: fix the citation
(`FLEET_SERVICE_ATLAS.md:35` and/or `DIGEST_Fleet_Monitoring_2_md.md:86`, drop "Open
WebUI" as a container), and add an erratum pointing to `WQ4365_VISUAL_WORKFLOW_BUILDERS_
DISSECTION.md` as the existing Dify/Sim dissection, superseding §3's "hasn't dissected
yet" framing. Not reopening wq#4371; not re-implementing here.
