# wq#3755 — research pipeline v2: demand-driven trigger, sandbox verification, wq bridge

- ticket: wq#3755 (build, p2, archetype novel-design) · lane fworker-wq3755
- date: 2026-10-12 · design doc — defines the v2 architecture and the build tickets to land it
- consulted: Gemini and DeepSeek both recommended this shape (per ticket); prior-art search
  (SearXNG, 2026-10-12) confirms the two load-bearing patterns are standard practice:
  broad-but-shallow smoke tests as a pre-review gate (aipatternbook.com/smoke-test) and
  evidence-producing harness verification before human review (arize.com harness engineering).

## 0. The problem v1 created

v1 is supply-driven: continuous scraping feeds pgvector (`research_docs`/`research_chunks`,
the UltraX `research_clean` corpus — wq#131/#163), Minerva generates proposals from the
corpus, and the pile lands on Frank. The record shows what that produced:

- wq#1 ("Proposal triage: drain the 41 defer + reject the noise") and wq#79 ("Pre-rank 113
  Minerva proposals") — triage tickets that exist only because volume outran demand.
- wq#1383 had to retrofit grounding ("ground it in source text") because proposals were
  reaching Frank without evidence.
- wq#114 added a reject-class to the proposal store — classification of rejections exists,
  but nothing reads it back into generation, so the same classes of noise recur.

The failure is structural: nothing upstream asks whether anyone *needs* the research, and
nothing downstream proves a proposal *works* before a human sees it. v2 inverts both.

## 1. Architecture overview

```
   wq ticket needs-tag ─┐
   monitoring alert  ───┼─► Trigger Service ─► brief (+ last-50 rejection constraints)
   manual CLI        ───┘         │
                                  ▼
                        LiteLLM gateway (research agent)
                                  │  draft proposal
                                  ▼
                        Sandbox Quality Gate (container, target arch)
                                  │  evidence.json  (no evidence → proposal dies here)
                                  ▼
                        Proposal→wq bridge  (weekly cap: 5)
                                  │  wq add --frank-gated
                                  ▼
                        Frank: Mission Control / `wq answer`
                                  │
                   approve ───────┴─────── reject ─► rejection ledger ─► (constraints, top)
```

Everything to the right of the trigger is pull; nothing scrapes on a timer. The continuous
scraper is retired once v2 is live (§7), not before.

## 2. Research Trigger Service

**Placement**: `foreman/research/trigger.py`, run as a systemd timer-less service
(`lumen-research-trigger`) that tails its three inputs; it owns no model calls beyond
dispatching the brief.

**Inputs** (the only three ways research starts):

1. **wq needs-tag.** wq already has a tag-bearing `needs` column parsed by regex
   (`/home/lumen/bin/wq:255-269`, the `repo(<path>)` tag family). We reuse that shape:
   `wq edit <id> --needs 'research(<one-line question>)'`. The trigger polls queue.db
   (read-only, WAL mode — same discipline as every other queue reader) for open tickets
   carrying an unserved `research(...)` tag. No new column, no schema migration; feature
   detection per wq#1158 precedent (`--needs` is refused, never dropped, on old schemas).
2. **Monitoring alerts.** aq rows escalated with owner=research (the aq hygiene path already
   runs on the foreman-health timer, commit 6959bd3f). An alert names a symptom; the trigger
   turns it into a question, never a solution.
3. **Manual.** `foreman/research/trigger.py ask "<question>"` — Frank or an orchestrator
   session. This is also the replay path for testing.

**Brief formulation.** A brief is narrow by construction: one question, the requesting
context (ticket id / alert id), the target environment (arch, OS, the node it must run on —
read from fleet state, never assumed), and the **constraint block** (§5) prepended verbatim.
The brief is written to `foreman/research/briefs/<id>.md` before dispatch so every model call
is reproducible from disk.

**Dispatch.** Briefs go to the LiteLLM gateway (the `litellm` container on Lumen — the fleet
standard path; restart policy asserted by `/home/lumen/bin/drift-assert:60-61`, auth watched
by `lumen-litellm-auth-monitor`, `/home/lumen/bin/aq_escalate.py:78`). Model/effort choice
follows the router's archetype table, not a hardcoded model name. The research agent's
deliverable is a **draft proposal**: what tool/change, why, install steps, and a smoke test
it predicts will pass. A draft with no runnable smoke test is returned to the agent once,
then dropped — unverifiable proposals are noise by definition (§0).

## 3. Sandbox Quality Gate

**The rule: no sandbox evidence, no Frank.** The bridge (§4) refuses any proposal whose
evidence file is missing, stale, or hash-mismatched. This mirrors the harness's own evidence
gate (ISSUE-LOG #17, "done is a replay, not a claim"): the gate re-runs the smoke test
itself; the agent's logged exit code is a hint, never proof.

**Mechanics** (`foreman/research/sandbox_gate.py`, invoked by the trigger service after the
draft arrives):

- Container image matching the **target arch** named in the brief (arm64 proposal → arm64
  image, via qemu-user emulation where the host differs — the firmware-emulation toolchain
  already on Lumen covers the cross-arch case). Network egress allowed for install only,
  then cut for the smoke-test phase, so a test can't pass by phoning home.
- Inside: run the proposal's install steps verbatim, then its smoke test. Both are the
  *proposal's own text* — the gate executes what Frank would be approving, nothing else.
- Output: `foreman/research/evidence/<proposal-id>.json` —
  `{proposal_sha256, image, arch, install_exit, smoke_cmd, smoke_exit, duration_s, stdout_tail}`
  with `stdout_tail` passed through `harness/masking.py` before write (a credential printed
  is a credential burned — instinct 4).
- `proposal_sha256` pins the evidence to the exact proposal text; the bridge re-hashes at
  submit time, so evidence can't be recycled across edited proposals (same hash-pinning the
  acceptance.json gate uses on build tickets).
- Hard caps: 15-minute wall clock, no volume mounts from the host beyond a throwaway workdir,
  no fleet credentials in the container environment (instinct 6: never a master key in a
  public-facing container — a sandbox running proposal-authored install steps is hostile-input
  territory and gets zero secrets, full stop).

**Guard interaction (recorded constraint).** The house guard denies container-runtime
commands to worker sessions except ps/logs — it even blocks commands whose *argument text*
contains the runtime's name (ISSUE-LOG row 1262, filed by this ticket). The gate therefore
runs as its own systemd service unit under the service account, not inside any Claude worker
session. Workers and the trigger service only read the evidence JSON; nothing in a worker
session ever invokes the runtime. This is a feature: the only code path that can execute
proposal text is one auditable unit.

## 4. Proposal→ticket bridge

The ticket asks for "status pending_approval". **Decision: do not add a new status.** wq
already has the exact machinery: `wq add --frank-gated` sets `gate_type=frank_decision` and
`status=frank_gated` (`/home/lumen/bin/wq:1589-1607`), `wq decide` queues the question and
advances to `pending_review` (`/home/lumen/bin/wq:3355` ff.), and `wq answer` records
Frank's verdict. A new `pending_approval` status would be a third synonym the
frank_gated/pending_review cycle already spells, and every queue reader (router, mortician,
liveness-close — which enumerates valid statuses at `/home/lumen/bin/wq:1444`) would need
teaching. Reuse wins.

**Bridge behavior** (`foreman/research/bridge.py`):

1. Verify evidence: file exists, `smoke_exit == 0`, `proposal_sha256` matches a re-hash of
   the proposal text, evidence younger than 7 days.
2. Check the weekly cap (§6). Over cap → proposal parks in `briefs/overflow/`, not in wq.
3. `wq add --frank-gated` with: title = proposal one-liner, detail = proposal text + a
   `Evidence: foreman/research/evidence/<id>.json` pointer + the originating trigger
   (ticket/alert/manual), `decision_prompt` = the one question Frank must answer.
4. Frank approves/rejects in Mission Control (which reads the same queue.db; its backend
   integration is its own ticket, same boundary as `reports/opsaudit_a13_flow_metrics.md`
   §"Mission Control panel") or via `wq answer` from any orchestrator session.
5. Approve → ticket opens into the normal build lane. Reject → `wq answer` text becomes a
   ledger row (§5). Either way the originating `research(...)` tag is marked served.

## 5. Rejection ledger with constraint injection

**Store**: `rejections` table in queue.db (not minerva.db — the bridge and trigger already
hold queue.db handles, and the proposal store's reject-class work from wq#114 migrates in
rather than being read cross-database):
`(id, ts, proposal_sha256, title, reject_class, frank_reason, brief_id)`.

**Injection**: the trigger prepends the **last 50** rejections to every brief as a constraint
block — `DO NOT re-propose: <title> (<reject_class>): <frank_reason>` — newest first, one
line each. 50 rows ≈ a few KB: cheap in every brief, and it closes the loop wq#114 left open
(classes recorded, never fed back). A proposal whose title or sha matches a ledger row is
additionally rejected at the bridge *before* sandbox spend — the ledger is both a prompt
constraint and a hard filter.

## 6. Weekly cap

**5 verified proposals per ISO week, enforced at the bridge** (the only place with full
information: a proposal is "verified" when its evidence passes §4 step 1). Implementation is
one COUNT over frank_gated tickets created by the bridge in the current ISO week; row 6+
parks in overflow and is re-considered the next week, newest-brief-first. The cap bounds
Frank's review load (the resource v1 exhausted — §0) and backpressures the whole pipeline:
when the week is full, the trigger service stops dispatching new briefs rather than queuing
model spend that can't land.

## 7. Rollout and follow-up tickets

Order matters: the gate must exist before the bridge (nothing unverified may reach wq), and
the bridge before the trigger goes live (verified proposals need somewhere to land).

1. **Build: sandbox quality gate** — `sandbox_gate.py` + its systemd unit + evidence schema;
   acceptance probe: a proposal with a deliberately failing smoke test produces evidence with
   `smoke_exit != 0` and is refused by a bridge dry-run (a probe that can fail, wq#2630).
2. **Build: bridge + ledger + cap** — `bridge.py`, `rejections` table migration, cap query;
   probe: 6th verified proposal in a seeded week parks in overflow, reject path writes a
   ledger row that the next brief contains.
3. **Build: trigger service** — `trigger.py`, the three inputs, brief format, LiteLLM
   dispatch; probe: a `research(...)` needs-tag on a fixture ticket yields a brief containing
   the seeded ledger's constraint lines.
4. **Ops: retire the v1 scraper** — only after 1–3 land and one full week runs under cap;
   the pgvector corpus stays (it is retrieval context for research agents), only the
   continuous *feed* stops.
5. **MC: approval panel** — Mission Control reads frank_gated rows from queue.db; out of
   foreman scope, same boundary opsaudit A13 recorded.

Filed 2026-10-12: wq#3759 (gate) → wq#3760 (bridge/ledger/cap) → wq#3761 (trigger) →
wq#3762 (retire v1 feed); wq#3763 (MC panel, deps wq#3760).
