# wq#4373 — verify wq#4356 (PR#2250, `research/WQ4356_A2A_PROTOCOL_DISSECTION.md`)

**Verdict: CONFIRM.** This is a documentation-only research ticket (no code, no
executable probe shipped or implied by the ticket text) — 254 lines added, one file.
The verify pass therefore targets the two ways a research doc can be wrong: (1) its
citations into this repo misquote or mislocate what they point at, and (2) its claims
about the external protocol (Google A2A v1.0) are stale, fabricated, or overstated.
Neither held up as a break.

## What shipped (re-read cold)

`git show b4c42f06` on `feat/wq4356-limpet-research-dissect-google-a2a-proto-a1`
(merged to main as PR#2250): one new file, `research/WQ4356_A2A_PROTOCOL_DISSECTION.md`,
254 lines, 7 sections — three-layer protocol, Agent Card discovery, canonical data
model, abstract operations, opaque execution, the authorization-omission CAVEAT, and a
LIMPET VALUE section mapping A2A primitives onto the existing `design/wq3999` recon/
hunters/reconcile topology. Note: `git log --all` shows a second, near-duplicate commit
(`1d38e7e2`, same 254-line content, different branch `feat/wq4370-...`, missing the
`Co-authored-by` trailer) — a leftover from a prior attempt/rebase, not something PR#2250
itself did; it isn't on `main` and doesn't affect this verify.

## Internal citations — checked against the files they point at

- `limpet/critic.py:18-19` — doc quotes "Limpet's agent core is still design-only,
  `design/wq3806_limpet_architecture.md` §6: model routing via LiteLLM is not yet built."
  Matches `critic.py` lines 18-19 verbatim.
- `limpet/critic.py:1-33` ("existing deterministic gate") — lines 1-33 describe exactly
  that: a structural gate (evidence citation, open-obligation check) that runs before any
  model call. Accurate.
- `design/wq3999_rex_skill_dissection.md:131-182` ("7-agent recon/5-blind-hunters/
  reconcile topology") — lines 131-182 describe 1 recon + 5 stance agents + 1 reconcile =
  7 agents. Matches.
- `design/wq3999...md:141-147` / `re-safety.md:28` ("may not see another's findings") —
  wq3999 lines 141-147 state this exactly, with the same inline citation.
- `design/wq3999...md:184-192` ("source + sink + broken-guard + affected-principal") —
  matches the finding-schema header and body at those lines verbatim.
- All cited paths (`limpet/critic.py`, `design/wq3999_rex_skill_dissection.md`,
  `design/wq3806_limpet_architecture.md`) exist in the repo; none of the line-range
  citations were off by section or misattributed.

## External protocol claims — spot-checked against independent sources

Ran `fleet-search web` (SearXNG suspended/CAPTCHA'd fleet-wide at the time, same as the
PR's own author note; DeepSeek keyed fallback used — consistent degraded-search state,
not something unique to this doc) against the load-bearing, falsifiable claims:

- **Well-known path.** Doc claims v1.0 uses `/.well-known/agent-card.json`. Confirmed:
  independent compatibility-matrix source shows `/.well-known/agent.json` was v0.2.x only;
  v0.3.0+ and v1.0 renamed it to `agent-card.json`. Doc has the current path right, not
  the stale pre-1.0 one.
- **Task state machine.** Doc lists `submitted`/`working` → `input-required`/
  `auth-required` (interrupted) → `completed`/`failed`/`canceled`/`rejected` (terminal).
  Confirmed independently against the A2A Java SDK's `TaskState` enum docs and a Dart A2A
  binding — exact same eight states, same interrupted/terminal split.
- **A2A-specific JSON-RPC error range.** Doc claims `-32001..-32009` A2A-specific on top
  of standard `-32700..-32603`. Found seven named codes (`-32001` `TASK_NOT_FOUND` through
  `-32007` `AUTHENTICATED_EXTENDED_CARD_NOT_CONFIGURED`) in an independent Ruby binding —
  consistent with, not contradicting, the claimed range (doesn't itself prove codes 8-9
  exist, but nothing found disputes it either).
- **Card signing.** Doc claims optional JWS-over-JCS-canonicalized-JSON signing. An
  independent "A2A v1.0 builder's guide" walkthrough shows the exact same mechanism
  (RFC 8785 canonicalization, JWS, `kid`-keyed JWKS fetch) with working SDK code. Matches.
- **GitHub Discussion #741 (registry/governance deferred).** Doc claims this closed
  without shipping a registry, deferred to AI-Catalog/ARD. Independent source: "The
  maintainers redirected the discovery need to two adjacent specifications, AI Catalog and
  ARD, leaving the registry question unresolved." Matches near-verbatim.
- **Authorization intentionally out of scope + hop-by-hop identity loss (the doc's §6
  CAVEAT, its single most load-bearing claim).** Independently corroborated two ways: (a)
  authzed.com and api7.ai-style sources describe A2A as deliberately offloading
  authorization to `securitySchemes`/external infra, consistent with the doc's "scoping
  decision, not a gap" framing; (b) search turned up live A2A ecosystem work (GitHub
  issues on an `agent-identity-protocol` extension, Ed25519 per-agent identity, an
  `actorChain` delegation-chain proposal with an explicit warning that "a verifier MUST
  NOT treat a monotone chain alone as evidence of granted authority") — i.e. the community
  is actively building the identity/delegation primitive the doc says the base spec lacks.
  That's strong independent confirmation the gap is real, not an invented caveat.

No fabricated quote, mislocated citation, or stale protocol detail found. The doc's own
epistemics are also honest about its one real weakness (degraded search, DeepSeek
fallback, disclosed in its own header) rather than hiding it.

## Scope check

Ticket (wq#4356) asked for a dissection covering six numbered points plus a LIMPET VALUE
section; the shipped doc has sections 1-7 covering exactly those six points (three-layer
protocol, Agent Card discovery, data model, abstract operations, opaque execution,
authorization caveat) plus §7 LIMPET VALUE with an explicit adopt/build-separately split.
Nothing in scope was skipped; nothing added was out of scope (no code changes, no
unrelated file touched — diff is the one new file).

## Verification performed

1. Read the full 254-line doc cold (`git show b4c42f06:research/WQ4356_A2A_PROTOCOL_DISSECTION.md`).
2. Checked every in-repo citation (`limpet/critic.py`, `design/wq3999_rex_skill_dissection.md`)
   against the actual file content at the cited line ranges — all accurate.
3. Ran five targeted `fleet-search web` queries against the doc's most falsifiable
   external claims (well-known path version history, task-state enum, A2A-specific error
   codes, card-signing mechanism, Discussion #741 outcome, authorization/identity-gap
   framing) and found independent corroboration for each, no contradictions.
4. Confirmed PR#2250's commit (`b4c42f06`) is the one actually merged to `main` (the
   near-duplicate `1d38e7e2` commit on a sibling branch is not on main and not this PR's
   concern).

No contradiction found — no escalation to opus-5@high required.
