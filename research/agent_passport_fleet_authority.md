# Agent Passport System (APS): cryptographic agent identity for fleet authority

wq#4864, 2026-11-05. Companion to `research/sysknife_fleet_evaluation.md` (wq#4859).

## Sources, pinned

All code citations are against shallow clones taken 2026-11-05. Re-run with
`git clone <url> && git checkout <sha>`:

| repo | commit | note |
| --- | --- | --- |
| github.com/agent-passport-system/agent-passport-system | `573608e9af06d84fecc18e3235eea4b54f8f7a08` (2026-11-03) | TS reference SDK, Apache-2.0, 63 stars, created 2026-02-18 (GitHub API, fetched 2026-11-05) |
| github.com/agent-passport-system/agent-passport-mcp | `71ee4947d6ee744b582af8a73f9f1e3c77d976df` (2026-11-03) | npm `agent-passport-system-mcp` 8.1.1 (its `package.json`) |
| github.com/aeoess/agent-governance-vocabulary | `d71dde1afef4a16702453cca44ab46e1e3ea5c01` (2026-11-04) | the URL in the ticket (`agent-passport-system/agent-governance-vocabulary`) is a 404. The vocabulary lives under the `aeoess` org (APS `README.md:240`) |

Published packages (fetched 2026-11-05): npm `agent-passport-system` 8.0.1 (deps `uuid`,
`libsodium-wrappers`; https://registry.npmjs.org/agent-passport-system/latest); PyPI
`agent-passport-system` 4.3.0, Python >=3.10, deps `pynacl`, `base58`
(https://pypi.org/pypi/agent-passport-system/json). The org also ships Go and Rust SDKs, an
`agent-passport-gateway` and a remote MCP (GitHub org repo list, fetched 2026-11-05).

Prior art I searched (fleet-search, 2026-11-05) that covers the same problem space:
IETF `draft-niyikiza-oauth-attenuating-agent-tokens`, `draft-prakash-aip-00` (Agent Identity
Protocol, also arXiv 2603.24775), and SPIFFE/SPIRE for agents (HashiCorp blog,
https://www.hashicorp.com/en/blog/spiffe-securing-the-identity-of-agentic-ai-and-non-human-actors).
I didn't read these past the search snippets. They're listed so a follow-up can compare them.

## Verdict

**Take the design and leave the product.** That's the same call wq#4859 made for SysKnife,
and it comes from the same root cause.

- APS's receipt and delegation formats are well built. Monotonic narrowing,
  `scope_of_claim` on every receipt, a two-layer verifier (crypto, then context), and negative
  conformance fixtures are all worth copying into the ops-apply and episode audit design.
- APS doesn't fix the fleet's actual gap. On Lumen every agent runs as uid `lumen` with a
  shell. Any signing key an agent's process can read is a key the agent can sign with. APS
  says this itself: "Key compromise ... APS limits the blast radius to the key's scope ...
  it does not detect that the human behind the key changed" (`THREAT_MODEL.md:62`). Its
  guarantee that the agent can't self-permit rests on the policy evaluator being "a separate
  signature in the chain" (`THREAT_MODEL.md:54`). On Lumen today, separate means a separate
  uid. Memoryd's own threat note says the same about its tokens: "on a single-user box any
  process running as lumen can read this file" (`minerva/v4/memoryd/daemon.py:96-98`).
- The MCP server is unsafe as-is under instincts 4 and 5. `generate_keys`, `issue_passport`
  and `rotate_key` "return private keys in the tool response", and "many signing tools take a
  private key as an argument" (agent-passport-mcp `README.md:344`). A private key in a tool
  argument or result ends up in the model context and the CC transcript. Don't install it on
  any fleet node.

So signed receipts become non-repudiable only after the same change SysKnife needed: a
**broker uid** that holds the keys and that workers can't read. With that in place, APS's
formats (or a minimal subset of them) are what the broker should sign.

## Corrections to the ticket's premises

| ticket claim | what the source says |
| --- | --- |
| "MCP server, 17 tools" | 154 tools by default. `APS_PROFILE=essential` gives 28 (agent-passport-mcp `README.md:9,17`; `tools-manifest.json` `count: 154`) |
| "Python + JS SDKs" | TS is the reference. Python, Go and Rust are checked against it by a conformance suite (`README.md:40`) |
| "Risk scoring 0-100 based on owner verification, age, usage history" | Two separate mechanisms. (a) **Passport grade 0-3** by attestation richness: `self_signed`, `issuer_countersigned`, `runtime_bound`, `principal_bound` (`src/types/attestation.ts:19-26`). (b) **Bayesian reputation**: `mu` 0-100, `sigma` 0-50, effective = `mu - k*sigma`, in tiers 0-4, with `effectiveAuthority = min(delegation, tier)` (`src/types/reputation-authority.ts:8,17-26,100-119`). Neither one scores on account age |
| "Governance vocabulary: allow, deny, throttle, suspend" | The vocabulary has no `throttle`. Its lifecycle terms are `issuance`, `suspension`, `expiry_or_exhaustion`, `revocation`, `external_restriction`, `authority_epoch`, `approval`, `policy_version`, `authorization_decision` (allowed/denied) (`vocabulary-lifecycle.yaml:153-230`), and that file's status is `INCUBATING`, `descriptive_only_pending_convergence` (`:31-32`) |
| "Authority delegation is currently prompt-based" | Partly wrong. The fleet already has non-prompt authority: wq claim receipts that memoryd checks (`daemon.py:113-118`, "the identity names its own ticket, the ticket's claimed_by names the identity back"), and `wq grant` F8 one-shot fingerprints (`bin/wq:3733-3742`, cited in wq#4859). What's missing is *cryptographic* proof, not machine enforcement |

## Pattern 1: Ed25519-signed passports

- Shape: `{version, agentId, agentName, ownerAlias, publicKey, mission, capabilities,
  runtime, createdAt, expiresAt, notBefore?, voteWeight, reputation, delegations, metadata}`
  (`src/core/passport.ts:68-84`).
- **The agent's own new key self-signs it.** `createPassport` generates the keypair and signs
  with that same private key (`passport.ts:48,86`). On its own, a passport proves only key
  possession, which is grade 0 (`attestation.ts:22`).
- An issuer countersignature covers `{passport, signature, signedAt}` (`passport.ts:155-175`).
  `verifyIssuerSignature` requires the caller to pin the issuer's public key (`:181-193`).
  `isIssuerVerified` only checks that the signature is 128 hex chars and is deprecated for that
  reason (`:195-203`). Copy the pinning and avoid the presence-check trap.
- Canonicalization: legacy `canonicalize` sorts keys and **strips nulls**, so `{a:1}` ==
  `{a:1,b:null}` (`src/core/canonical.ts:3-8`). New receipts use RFC 8785 JCS
  (`THREAT_MODEL.md:52`). If we sign anything ourselves, use JCS (Python `rfc8785`, which is
  already the APS Python test dep).
- Identifiers: `did:aps`, and it also accepts did:key, did:web, SPIFFE SVIDs and OAuth tokens
  (`README.md:61`).

**Fleet mapping.** A passport per *role*, not per worker: `fworker`, `commander@horus`,
`orchestrator`, `operator(frank)`. Workers are ephemeral (`fworker-wq<N>`, `daemon.py:113`),
so a per-ticket identity is a **delegation** (pattern 2) and not a passport.

## Pattern 2: delegated authority

- Two record types, and they don't interoperate. Use `AuthorityDelegationV1`
  (`aps:authority-delegation:v1`, `draft-pidlisnyi-aps-03` §3.1). The older `Delegation` is
  frozen and lets a bare grant `p` cover `p:x` (`README.md:65`).
- The V1 body is `{record_type, version, parent_delegation_id, issuer, subject,
  verification_method, issued_at, nonce, authority}`. The `authority` field carries **seven
  facets**: scope (grants), spend, depth (remaining), time (not_before/not_after), reputation
  ceiling, values required, reversibility ceiling (`src/v2/authority-delegation/types.ts:13-84`).
- Narrowing is monotonic: sub-delegation can only reduce each facet. The chain verifier
  returns valid / invalid / indeterminate / unsupported (`README.md:63-65`).
- **Revocation is stateful.** `revokeDelegation`, `cascadeRevoke` and `validateChain` in core
  are stubs that throw and point to `DelegationStore` (`src/core/delegation.ts:17-21,694-702`).
  Revocation therefore holds only where a live store is consulted. `credentialCheckPolicy`
  picks `on-accept` (snapshot, which misses later revocation), `on-process` (the default) or
  `both` (`README.md:171-198`).

**Fleet mapping.** The current chain is Frank → orchestrator → `wq claim` → fworker. A wq claim
maps well onto one V1 delegation: issuer = orchestrator key, subject = `fworker-wq<N>`,
scope = lane plus ticket ref, time = claim TTL, depth = 0 (no sub-delegation),
reversibility ceiling = from the ticket's risk. `wq release` / `--dead-worker` maps to
revocation, and the DelegationStore maps to the wq claim table we already have (wq's grant path runs
SQLite `BEGIN IMMEDIATE`, `bin/wq:3733-3742`). This
gives every memoryd/ops-apply check a signed record to verify where today it has a DB lookup
it trusts.

## Pattern 3: signed accountability receipts vs `lumen_episode.py`

APS receipt types: `aps:action:v1`, `aps:authority_boundary:v1`, `aps:custody:v1`,
`aps:contestability:v1`, `aps:bundle:v1`, `aps:payment_receipt:v1`. Each one carries
`scope_of_claim.asserts` / `does_not_assert` and a `capture_mode` / `self_attested` flag
(`README.md:95-123`). `receipt_id` = SHA-256 of the JCS body, and the signature covers it
(`THREAT_MODEL.md:52`). Enforcement uses three signatures: the agent signs intent, the policy
engine signs evaluation, the agent signs the execution receipt (`README.md:67`). Denials get
receipts too (`README.md:26`). Receipts can be batched into a Merkle ledger with inclusion
proofs and batch chaining (`src/core/receipt-ledger.ts:69-150`).

Verifier must-reject list, each with a golden negative fixture: `WRONG_CLAIM`,
`DELEGATION_EXPIRED`, `DELEGATION_REVOKED`, `REPLAYED`, `POLICY_NOT_EXECUTED`
(`README.md:125-136`).

| | `lumen_episode.py` today | APS receipt |
| --- | --- | --- |
| purpose | semantic memory: commit / recall / precheck (`minerva/lumen_episode.py:265,359,408`) | evidence of a single authority decision |
| who | `$WQ_AGENT` env var sent as `X-Memoryd-Agent` (`minerva_audit.py:87-88`, `lumen_episode.py:208,220`). Memoryd cross-checks it against the claim receipt | Ed25519 signature by the key holder |
| integrity | DB row, no signature | JCS + SHA-256 id + signature, optional Merkle batch |
| negatives | no denial episode type in `commit()`'s signature (`lumen_episode.py:265`); guard.sh denials are not episodes | denials signed |
| claim scope | free text | explicit `asserts` / `does_not_assert` |

**Complement, don't replace,** as wq#4859 concluded. Episodes stay the learning store. Add a
`receipt_id` column/tag on episodes and wq events that points to a signed receipt the broker
writes.

## Pattern 4: governance vocabulary

It's a crosswalk registry (`vocabulary.yaml` plus `crosswalk/<system>.yaml`). It maps each
system's internal signal name to a canonical term with a match grade of `exact`, `structural`,
`partial`, `false_analog` or `no_mapping` (vocab `README.md:3-27`). It's v0.2.0 draft
(`README.md:51`), and 40+ crosswalks exist, including `cursor-hooks.yaml`, `a2a.yaml` and
`cloudflare-aam.yaml` (`crosswalk/`).

Useful for us as a **naming discipline**. Fleet terms crosswalked by hand to the lifecycle
vocabulary (`vocabulary-lifecycle.yaml:153-230`):

| fleet term | canonical | match |
| --- | --- | --- |
| `wq claim` | `issuance` (of delegated_authority) | structural |
| `wq release`, `--dead-worker` | `revocation` | structural |
| claim TTL lapse | `expiry_or_exhaustion` | exact |
| `wq block` | `suspension` | partial (blocks the ticket, not the identity) |
| `wq grant approve` (F8) | `approval` | exact (scoped, single-use, withdrawable) |
| guard.sh deny | `authorization_decision` = denied | structural (unsigned, unrecorded) |
| Thor no-restart rule | `external_restriction` | structural |
| prompt / instincts version | `policy_version` | partial (not recorded per decision) |

The "throttle" concept the ticket asks about has no canonical term. Our closest is the
loop-breaker. Leave it unmapped and don't invent a term.

## Pattern 5: MCP server

154 tools across identity (15 tools), principal identity (6), delegation, enforcement,
commerce, reputation, governance, coordination and data, with profiles from `minimal` to
`full` (`README.md:9,17-19,87,122`). Session state lives in memory and is lost on restart
(`README.md:344`).

**Don't adopt it.** Private keys go in and out through tool arguments and results
(`README.md:344`), which breaks instincts 4 and 5. It also hands the agent the principal's
signing capability in the same process (`create_principal` "held in this process as the
session principal", `README.md:127`), which collapses the very boundary delegation exists to
create. If MCP access is wanted later, the only acceptable shape is a broker-side MCP that
exposes `verify_*` and `request_*` tools and never a key.

## Pattern 6: risk / trust scoring

See the corrections table. The useful part is `effectiveAuthority = min(delegation, tier)`
(`reputation-authority.ts:8`): reputation can only *lower* what a delegation grants. Tier
origin `fiat | earned | provisional`, where only `earned` may promote others (`:97-98`). For
the fleet, the honest equivalent is per-lane FALSE_COMPLETE/critic-reject rates already in
wq. Scoring LLM workers with a TrueSkill model adds machinery and no authority boundary.
Not recommended now.

## Recommendation: what to build, in order

1. **Broker uid first. It's a prerequisite, not an APS item.** A dedicated system user
   (e.g. `lumen-authority`) owns an Ed25519 key in a 0600 file under its own home, or in Vault
   transit (Vault changes only via detached systemd-run, instinct 6). Workers reach it over a
   Unix socket and get identified by `SO_PEERCRED` (the wq#4859 recommendation). This
   single piece is shared by ops-apply, the SysKnife bridge and receipts.
2. **Signed claim delegations.** On `wq claim`, the broker signs a minimal
   `AuthorityDelegationV1`-shaped record (scope = lane + ticket, time = TTL, depth 0). Memoryd
   and ops-apply verify signature plus live wq state, which is `on-process` semantics.
3. **Signed decision receipts, denials included.** The broker signs `{receipt_id (sha256 of
   JCS body), claim_type, subject, delegation_id, policy_version, verdict, action_ref, ts,
   scope_of_claim}` for guard denials, grant approvals/consumptions and ops-apply runs.
   Append to an append-only table and anchor a Merkle/batch root daily into git (closes the
   tail-truncation gap wq#4859 noted).
4. **Verifier with negative fixtures.** Port the five must-reject cases as committed fixtures
   so a verifier change has a probe that can fail.
5. Use `pynacl` + `rfc8785` directly. Don't take a dependency on the APS SDK: its surface is
   525 TS files, and the legacy null-stripping canonicalizer is a trap. Keep the field names
   APS-compatible so an APS verifier could read our receipts later.

Not doing now: passports with external issuers, wallet binding, commerce gates, reputation
tiers, the vocabulary WG.

## Open questions (decided here, recorded for review)

- Per-role vs per-worker keys: **per-role broker key, per-ticket delegations.** Ephemeral
  worker keys held by the worker would sit in the reachable uid, so they prove nothing.
- Commander on HORUS: it needs its own broker or a remote signing call to Lumen's broker over
  Tailscale. Unresolved, and it belongs to the ops-apply design ticket.
