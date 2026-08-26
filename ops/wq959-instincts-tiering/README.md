# wq#959 — instincts.yaml 3-tier restructure

Design source: `foreman/reports/R1_instincts_efficacy.md` §6; corpus verdicts from
`reports/opsaudit_a7_rule_corpus.md` §4.1. Sibling tickets: wq#958 (replace
`head -c 4096` in minerva/context/claude_context.sh with tier-aware rendering),
wq#960 (purge superseded copies in the wider corpus), wq#961 (retire stress7
tripwire post-tiering; amend tag_sections rule with its live path).

## What changed

- `/home/lumen/instincts.yaml` moves from a flat 57-string list (schema 1) to
  **schema 2**: three tiers with per-rule metadata `id, tier, added, source,
  supersedes` (+ `domain, trigger, playbook` on tier 2). Canonical copy:
  `instincts.yaml` here; installed live by `apply.sh` (timestamped backup).
- **Tier 0 — hard constraints** (8 rules, rendered ~466 tok, budget ≤600):
  thor-only-hard-rule, full-autonomy, mask-before-print,
  detached-only-vault-cloudflared-commander, no-master-key-public-container,
  consolidation-proposal-only, fabricated-evidence, stress7-dead-identity.
  Always injected FIRST, everywhere — **including every fworker prompt** (before
  this change workers received zero instinct text; MASK-BEFORE-PRINT's two
  in-window leaks, wq#920/wq#948, were both worker-side).
- **Tier 1 — operating doctrine** (26 rules): design/class/test discipline,
  Frank-primacy, findings-delivery, evidence rules, autonomy doctrine,
  product-liveness pointer, session-continuity, time rules. Injected into
  orchestrator/audit/chat (`--core`) sessions after tier 0.
- **Tier 2 — domain** (4 index entries → playbooks, texts moved verbatim):
  `playbooks/quest.md` (5 rules), `playbooks/fleet-ops.md` (8),
  `playbooks/llm-jobs.md` (5), `playbooks/hardware-claims.md` (2), installed to
  `/home/lumen/instructions/playbooks/`. Only the one-line trigger index reaches
  `--core` (~238 tok instead of ~3.5KB of Quest trivia in every session).
- **Renderer**: `harness/instincts_render.py` — whole-rule tier-ordered
  rendering, `--audience worker|core`, `--check` validator (dup ids, metadata
  completeness, tier-0 budget, supersedes hygiene, playbook existence on disk).
  Fail-open contract: any parse/format problem renders "" so no session spawn
  ever breaks on this file.
- **fworker wiring**: `harness/fworker.py` substitutes `{{INSTINCTS_TIER0}}` in
  `prompts/worker.md`, `prompts/fire-diagnosis.md`, `prompts/fire-apply.md`.

## Rule accounting (57 schema-1 rules → 38 schema-2 entries)

- 8 tier 0 + 26 tier 1 + 4 tier-2 index entries; 20 domain texts live in playbooks.
- **Dropped: reboot-gate** ("Gate any reboot on … Frank's explicit permission").
  Not a new decision — Frank retired it in the 2026-08-22 autonomy grant, whose
  own text says so; A7 C1 flagged the survivor as a direct self-contradiction.
  `thor-only-hard-rule` carries `supersedes: reboot-gate`.
- **Merged: self-generated-evidence → fabricated-evidence** (A7 §4.1 #28:
  near-duplicate merge candidate). Metadata records the merge.
- Every other rule kept; tier-1/tier-2 texts verbatim (trimmed banners only),
  tier-0 texts condensed per R1 §6 ("the autonomy grant one-liner"). Disputed
  content (A7 C6 wrapper claim, C10 search→recall) kept verbatim with the
  dispute noted in the playbook — content fights belong to wq#960/#961/Frank,
  not to a restructure.
- Deviation from R1's "~15" tier-1 estimate: 26. Nothing in R1 marks the excess
  rules retired, and retirement is wq#960/#961's scope; tiering only decides
  *where* a rule goes. Tier 1 renders ~1776 tok, only into --core sessions.

## Injection matrix

| Audience | Renders | How |
|---|---|---|
| fworker (build + fire) | tier 0 | `fworker.py` → `instincts_render.tier0_block()` |
| orchestrator/audit/chat `--core` | tier 0 + tier 1 + tier-2 index | wq#958: swap `head -c 4096` for `python3 /home/lumen/foreman/harness/instincts_render.py --audience core` |
| in-domain work | full playbook | session reads the file named by the trigger index |

## For wq#958

`claude_context.sh:23`'s `head -c 4096` can be replaced by the one-liner above.
Until that lands, schema 2 degrades gracefully under the byte cut: tier 0 sits at
the top of the file, so the 4KB window now delivers hard constraints + early
doctrine instead of the stale reboot-gate + Quest trivia. The old
`grep -q '^rules: \[\]'` emptiness check still behaves (schema 2 always has rules).

## Ordering vs wq#960

wq#960 purges the reboot-gate copy at old instincts.yaml line ~17 plus copies in
change-safety.md / session-start.md / UNIFIED_DISPATCH.md / manifest.yaml. This
restructure already removes the instincts.yaml copy; either apply order
converges (960's other files are untouched here; this playbook set is new files
only, deliberately avoiding 960's edit targets).

## Verify

```
python3 harness/instincts_render.py --check          # live file
python3 -m pytest harness/tests/test_instincts_render.py -q
```
