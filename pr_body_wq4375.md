## What

Adds `reports/4375_judgment_wq4370_agentbox_manifest_escalation.md`: an opus-5@high
judgment pass on an archetype-8 verify-pass of wq#4370's merged research doc
(`research/WQ4370_SECURITY_VERIFICATION_DISSECTION.md`, PR#2254, `47ad02f6`). No code or
research-doc changes — the fix itself is dispatched as a follow-up ticket.

**Verdict: PARTIAL.** Scope is one paragraph of the doc's §3 (AgentBox). The Sandlock,
GBase Lifeline, and DefenseClaw sections are CONFIRM and not reopened.

## Why

As verifier (fresh claude-sonnet-5@high instance, same model as the wq#4370 author) I
re-sourced every claim independently via `fleet-search web`/`fleet-search read` rather
than trusting the doc's citations. Three of four tool sections reproduced verbatim
against primary sources (Sandlock's arXiv 2605.26298v1 abstract/Table 2/ABI-floor text;
GBase's README Lifeline/Quality-Gates lines; the OWASP AISVS secondary doc DefenseClaw
cites).

The fourth, §3 AgentBox, contradicted the doc. The doc's final bullet says the ticket's
"user confirmation layered on manifest, prevents malicious manifest escalation" claim is
"not literally in the paper" and attributes consent to "MCP's standard Host role (generic
... not an AgentBox-specific feature)". Fetching `arxiv.org/html/2510.21236v3` directly
turned up two passages in AgentBound's own design (§3.1, §3.2), not generic MCP
background: "If an agent attempts to add a runtime permission not covered by the
manifest ... the policy enforcement engine aborts the execution" and "requests user
consent for the runtime permissions derived from those declared capabilities ... This
guarantees that a server can only access resources explicitly granted by both its
manifest and the user." Same model, same claim, opposite verdict — a tie-break
contradiction per the verifier/author-model-collision rule.

Escalated to opus-5@high per protocol. It independently re-fetched the paper, confirmed
both quotes are real and correctly placed (§3.1/§3.2, not §2.2 background), found a third,
even more direct passage, and noted the doc actually contradicts itself: its own first §3
bullet quotes the zero-privilege/manifest-ceiling sentence that its last bullet says isn't
in the paper. The opus pass also re-confirmed the doc's narrower point survives: the paper
describes no signature/hash/integrity check on the manifest file itself, and its security
evaluation only ever used manifests the evaluators wrote by hand — an attacker-written
manifest was never tested. That half of the doc's analysis (tamper-resistance is a Limpet
design gap, not something AgentBox already closed) is correct and should stay.

## How

- `reports/4375_judgment_wq4370_agentbox_manifest_escalation.md` — new. Verdict line
  first, full evidence trail (both parties' quotes, exact arXiv section numbers, a 3-way
  reading of "prevents malicious manifest escalation", the self-contradiction in the
  doc's own two bullets), disposition recommendation.
- No change to `research/WQ4370_SECURITY_VERIFICATION_DISSECTION.md` in this PR — per
  this ticket's own instructions ("Do NOT re-implement; verify") and fleet precedent
  (wq#2601/wq#2603, wq#4284/wq#4291: the verify/judgment ticket reports, a separate
  ticket applies the forward-fix). Follow-up ticket filed for the correction, noted in
  `wq done`.

## Testing

Docs-only; no executable paths touched. Every quote in the judgment report was fetched
live from `arxiv.org/html/2510.21236v3` via `fleet-search read` on 2026-10-19 (HTTP 200)
and independently re-verified by the opus-5@high escalation pass, not taken on the
verifier's word alone — see report §1-4 for the grep hits (`consent dialogs`, `not covered
by the manifest`, `aborts the execution`, plus a full-text sweep for
tamper/signature/signed/integrity/hash/escalat that found nothing describing manifest-file
integrity protection).

## Risk / rollback

None to running systems — one new report file. Rollback is `git revert`.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
