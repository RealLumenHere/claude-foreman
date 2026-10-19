# wq#4383 — verify wq#4368 (PR#2266, LIMPET RESEARCH: enterprise meta-frameworks)

**Verdict: CONFIRM, with two non-load-bearing defects found and fixed in this PR.**
`research/WQ4368_ENTERPRISE_META_FRAMEWORKS_DISSECTION.md`'s core deliverables — the three
framework dissections, the MAF-harness-vs-custom evaluation, and the sensitive-data masking
spec — hold up under adversarial re-checking against live re-fetched sources and the actual
Limpet code. Two secondary grounding claims did not hold up; both are corrected in place
here (ISSUE-LOG #1469), per the wq#4170 precedent (fix a non-load-bearing citation defect in
the verify PR rather than escalate).

## What I checked

Fetched PR#2266's head directly (`git fetch origin pull/2266/head`, commit `1818cd99`, one
file, 323 lines, confirmed byte-identical to the version later merged to `main` via
`git diff pr-2266-check:...md main:...md` — clean squash-merge, no divergence).

### 1. External sourcing — re-fetched live, not taken on the report's word

- **MAF Harness** (`learn.microsoft.com/en-us/agent-framework/concepts/harness`): the
  doc's core quote — "An agent harness is the runtime scaffolding that turns a language
  model into an agent that can perform work. It drives model and tool calls, manages
  conversation state and context, applies approval policies, and can keep the agent
  progressing through a multi-step task" — matches the live page near-verbatim (page adds
  "sometimes called an AI harness"; "multistep" vs "multi-step" is the only token diff).
  `fleet-search web` independently returned the same page text.
- **Databricks Omnigent** (`databricks.com/blog/contextual-policies-omnigent-...`): the
  French-mirror result `fleet-search` returned independently confirms the contextual-policy
  mechanism described in the doc — a policy listens to tool-call/LLM events, can
  allow/deny/transform/ask, and can additionally mutate session-scoped state — matching the
  doc's "(old_state, new_event) → (state_update, decision)" framing.
- **Nvidia NemoClaw + OpenShell**: independently re-fetched GitHub source
  (`NVIDIA/NemoClaw` README snippets, `docs/security/gateway-authentication-controls.mdx`)
  confirms both headline claims verbatim: "Agents never see real credentials; OpenShell
  adds them only to requests bound for approved endpoints," and the Memory Secret Scanner's
  "before_tool_call hook that scans for 14 high-confidence secret patterns" plus the CLI
  Secret Redaction section's exact wording ("automatically redacts secret patterns (API
  keys, bearer tokens, provider credentials) from command output and error messages before
  logging them").

No invented quote, stat, or product found across all three frameworks — this is real,
sourced research, not parametric-knowledge fill-in for a post-cutoff topic.

### 2. Internal repo citations — checked against the actual files, not the doc's prose

| Claim | Cited location | Verified |
|---|---|---|
| §6 agent-core description, lines 258-309 | `design/wq3806_limpet_architecture.md` | Exact section span, exact content |
| Energize-confirm boundary, line 294 | same file | Exact text at that line |
| Autonomy boundary + energize-confirm, lines 290-302 | same file | Exact span |
| 16GB-RAM-box constraint, lines 260-264 | same file | Exact span |
| `QUARANTINE_RELPATH`, line 66 | `limpet/sanitize.py` | Exact |
| `_quarantine_entry`, line 76 | same file | Exact |
| `_sanitize_tree`, line 80 | same file | Exact |
| `sanitize_workspace`, line 201 | same file | Exact (`grep -n "def sanitize_workspace"` → 201) |
| docstring "nothing is destroyed, only kept out of context", lines 6-7 | same file | Exact |
| `PATTERNS`, line 452 | `harness/masking.py` | Exact (`grep -n "^PATTERNS"` → 452) |
| "a hit is an ALARM, not a save... the token already reached the model and is burned", lines 8-12 | same file | Exact (paraphrase of lines 10-12, CLI-hook-gap claim at line 5-7) |
| Independent "one investigation per workspace session" conclusion | `research/WQ4367_ORCHESTRATION_DISSECTION.md` | Confirmed present at line 250, citing the same §5-§6 |
| propose/critic/adjudicate, `limpet/critic.py` | same file | Confirmed: module docstring describes exactly this three-step pattern with `VERDICT_CONTESTED` |

Every one of these resolved to the exact line or exact text claimed — the file:line
precision here is unusually good (`sanitize_workspace` and `PATTERNS` landed on the exact
line number, not a nearby one, across a combined ~1,500 lines of source).

## Things I tried to break and found real breaks on

- **Tried the "design doc calls three of four MAF features 'deferred' or 'future'" claim.**
  `grep -ni "deferred\|future" design/wq3806_limpet_architecture.md` returns exactly 3 hits
  (lines 169, 306, 325), none of which are about plan/execute mode, tool approval, or
  background-agent fan-out — they're about the fleet's own deferred-tool pattern, a future
  role-assignment ticket, and provisioning open-items (SKU/RAM, resident-model choice,
  tool-catalog seeding). The design doc never mentions plan/execute mode, tool approval, or
  background-agent fan-out by name anywhere. The claim, presented with quote marks implying
  literal sourcing, does not hold up. **Fixed**: replaced with a direct statement that the
  design doc never mentions the three, and that each is assessed on its own merits below
  (which the doc's own subsequent bullets already do correctly — todo tracking rejected as
  a downgrade, tool approval rejected as solving a problem Limpet doesn't have, fan-out
  skipped per MAF's own "experimental" flag and WQ4367's independent conclusion, plan/
  execute recommended as a genuine gap). Not load-bearing: the bullet-level analysis never
  depended on this framing sentence being true.
- **Tried the masking-spec's claim that the existing quarantine ledger uses an "injection"
  layer tag.** `grep -n "layer" limpet/sanitize.py limpet/injection.py` shows the actual
  values are `LAYER_STRONG = "strong"` and `LAYER_CUE = "cue"` (`injection.py:59-60`); the
  string `"injection"` is never assigned to the `layer` field anywhere in `sanitize.py`. The
  doc's spec item 2 ("a `layer` tag of `"credential"` alongside the existing `"injection"`
  tag") misdescribes the current ledger's taxonomy. **Fixed**: now names the real
  `"strong"`/`"cue"` tags and `injection.py`'s constants. Not load-bearing: the proposed
  addition (a third `"credential"` layer value) is unaffected by what the other two values
  are literally called — an implementer following the corrected text now gets the real
  before-state instead of a value that doesn't exist in the ledger.
- **Tried every other file:line citation for drift** (the recurring defect class per prior
  verify-passes, e.g. wq#1308/wq#4313) — all resolved exactly, see table above.
- **Tried the squash-merge for silent edits** — `git diff` between the PR head and `main`'s
  merged copy of the file is empty; the merge did not alter content.

## Why this is CONFIRM, not CONTRADICT

Per the wq#4170 judgment's standard ("CONTRADICT overstates what is wrong... reserve
CONTRADICT for a conclusion that does not survive"): both defects found are supporting/
grounding claims, not load-bearing for either deliverable. Delete or correct both sentences
and every other conclusion in the document — the three framework dissections, the MAF-vs-
custom evaluation (todo tracking stays, plan/execute pattern adopted without the MAF
dependency, tool approval and background fan-out skipped), and the five-point masking spec
— stands fully supported by the citations verified in the table above. Nothing downstream
consumed either wrong claim (no code was written against this doc yet). Fixing both
in-place here, rather than filing a CONTRADICT/judgment dispatch, matches the disposition
wq#4170 set for exactly this shape of finding.

## Not independently re-verified

- Databricks Omnigent's own source code / Omnigent server internals, and NemoClaw's full
  blueprint catalog (Cadence RTL, Blender/Omniverse workflows) — both the author and I are
  reading each project's own documentation/README claims, not their trees. The doc doesn't
  claim otherwise.
- The recommendation to adopt "plan/execute mode as a pattern, not the MAF package" is a
  judgment call, not a factual claim — its premises (Limpet's §6 loop, obligations.py,
  critic.py scope) are the internal citations already verified above.

## Disposition

CONFIRM. Two non-load-bearing grounding defects found, fixed directly in
`research/WQ4368_ENTERPRISE_META_FRAMEWORKS_DISSECTION.md` as part of this verify PR, logged
at ISSUE-LOG #1469. No escalation to opus-5@high warranted — no conclusion in the document
failed to survive adversarial re-checking.
