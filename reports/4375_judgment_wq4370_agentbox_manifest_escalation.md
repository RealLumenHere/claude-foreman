# wq#4375 — Judgment pass (archetype 8, opus-5@high): wq#4370 §3 AgentBox manifest-escalation dispute

**Verdict: PARTIAL** — the doc's bolded claim that the ticket's "user confirmation layered on manifest, prevents malicious manifest escalation" is "not literally in the paper" is wrong: AgentBound's own §3.1 and §3.2 describe user consent over manifest-derived permissions and an abort when the manifest is exceeded. The doc's narrower point, that nothing signs, hashes, or integrity-checks the manifest file, holds.

## Why this is a judgment pass

The wq#4370 author and the wq#4375 verifier are both claude-sonnet-5, and the verifier
contradicted one paragraph of the author's work. Per fleet protocol that goes to an
opus-5@high tie-break. Scope here is only the disputed paragraph of §3 (AgentBox). The
Sandlock, GBase Lifeline, and DefenseClaw sections stand as CONFIRM on the verifier's
work and are not reopened.

## What was shipped

`research/WQ4370_SECURITY_VERIFICATION_DISSECTION.md` (merged at `47ad02f6`, on `main`),
§3 "AgentBox (inside AgentBound) — manifest-gated MCP isolation", final bullet (lines
139-148):

> **The ticket's "user confirmation layered on manifest, prevents malicious manifest
> escalation" claim is not literally in the paper.** The paper describes MCP's standard
> Host role as the thing that "enforce[s] security and consent policies" (generic MCP
> architecture, not an AgentBox-specific feature), and discusses manifest *correctness*
> (auto-generation accuracy) rather than manifest *tamper/escalation* detection — no
> signature, hash, or integrity-check mechanism for the manifest file itself is described.
> Treat "prevents malicious manifest escalation" as the ticket's own gloss on [...] not a
> cited AgentBox feature [...]

Ticket text under dispute, as relayed by the verifier: "AgentBox — zero-privilege container
per MCP server with manifest-declared access. User confirmation layered on manifest.
Prevents malicious manifest escalation." (I could not find the original ticket body
locally. I am working from the verifier's verbatim quote.)

## Evidence

Source: I fetched `https://arxiv.org/html/2510.21236v3` myself on 2026-10-19 through
`/home/lumen/bin/fleet-search read` (HTTP 200; text extracted to
`/tmp/fworker-wq4375/judg/content.txt`) and searched it directly. Section numbers are the
paper's own.

### 1. The verifier's two quotes are real, verbatim, and in AgentBound's own design section

Both quotes sit in **§3.1 "Access Control Policy" → "Manifests"**, in the paragraph that
walks through the paper's running example (a documentation agent using the FileSystem MCP
server and the Fetch MCP server, whose manifests are Figures 4 and 5). This is not a
hypothetical the verifier made up, and it is not generic MCP background.

- Quote 1 (verbatim): "...the generic capabilities mcp.ac.filesystem.read and
  mcp.ac.filesystem.write must be instantiated as runtime permissions with a concrete
  directory and an access mode (read-only or read-write), while the mcp.ac.network.client
  capability must be instantiated with the exact URL. This process results in user consent
  dialogs for the requested runtime permissions, where the user approves read-access to the
  codebase directory and write-access to the README file needed by the FileSystem MCP
  server, and specifies the URL the Fetch MCP server can access..."
- Quote 2 (verbatim, same paragraph, a few sentences later): "If an agent attempts to add
  a runtime permission not covered by the manifest (e.g., requesting access to environment
  variables when the manifest does not include the corresponding capability), the policy
  enforcement engine aborts the execution."

The paragraph also says "highly volatile runtime permissions (e.g., changing file
directories for each execution) must be consented on each execution". So consent is a
recurring step in the design, not a one-off.

### 2. A third passage, in the AgentBox section itself, is stronger than either quote

**§3.2 "Policy Enforcement Engine"**, which the paper opens with "AgentBox serves as the
policy enforcement engine...", ends with:

> "Each MCP server is bundled with a manifest file in JSON format, which specifies its
> description and required capabilities. At runtime, the agent accesses the manifest,
> **requests user consent for the runtime permissions derived from those declared
> capabilities**, and launches the server within the sandbox. [...] This guarantees that a
> server can only access resources **explicitly granted by both its manifest and the
> user**."

That is "user confirmation layered on manifest" almost word for word, and the paper places
it inside AgentBox. The same section also says: "By default, servers start with no
privileges; only generic capabilities explicitly specified in the manifest can be
instantiated as runtime permissions at execution time." The doc quotes this exact sentence
in its own first §3 bullet. Together with Quote 2, it is the escalation ceiling the ticket
refers to. So the doc contradicts itself: it quotes the manifest-as-ceiling property in its
first bullet, then says in its last bullet that escalation prevention is not a cited
AgentBox feature.

### 3. The "generic MCP Host role" attribution gets the paper backwards

The quote the doc relies on is real, but it comes from **§2.2 "Model Context Protocol
(MCP)"**, the background section: "Hosts, which initiate and coordinate clients, supervise
client lifecycles, enforce security and consent policies, and route LLM calls". The next
paragraph of §2.2 makes the paper's actual point. MCP "currently defines only the messaging
and role abstractions. The specification delegates safety to the application and its
engineers [...] the host is responsible for policy enforcement and consent management."
§2.4 then concludes that "MCP lacks an enforceable security system for its servers".

So the paper cites the Host role as the gap that AgentBound fills. It does not say the
Host role is where consent comes from. Consent in AgentBound is a concrete step in its own
flow: manifest capability → concrete runtime permission → user consent dialog → container
launch. The doc attributed AgentBound's own mechanism to the generic MCP architecture that
the paper says lacks it. This is a genuine misattribution, not a defensible reading.

### 4. The narrower claim (no manifest-file integrity mechanism) survives

I searched the full text for tamper, signature, signed, integrity, hash, and escalat:

- "tamper" and "escalation" appear only in §3's opening problem statement ("servers enable
  privilege escalation, data tampering, and exfiltration attacks"), describing MCP without
  AgentBound.
- "signed" appears once in the design, about a hypothetical future "native companion
  application that is signed and trusted" for device, clipboard, and location access (§3.3).
  That is not about the manifest.
- "signatures" appears only in §6 Related Work (MCP-Defender's "deterministic signatures").
- "integrity" appears only in the CIA-triad sense (§1, §2.3, §2.4) and a reference title.

The manifest is "bundled and distributed together with the server" (§3.1). The paper
describes no signing, hash-pinning, or provenance check for it. The threat model (§2.3)
covers rug pulls on *tool descriptions* and supply-chain attacks on server *code*, but not
a rug pull on the manifest itself.

The paper also never tests an attacker-written manifest, which supports the doc's "gap to
design for" advice. In the security evaluation (§4.2.1), "the security manifests for those
10 [malicious] servers were created manually during testing", meaning evaluators wrote
them, not the adversary. In the paper's own envisioned deployment, the server developer
writes the manifest, and the server developer is the adversary in its threat model ("an
adversary [...] controlling one or multiple malicious MCP server(s)"). The paper's defenses
against an over-broad malicious manifest are the human-readable description "to aid human
review" and the consent dialog, where the user picks the concrete scope. Neither is
integrity protection. The doc is right that this is a gap Limpet would need to close on its
own.

### 5. How the ticket's phrase should be read

"Prevents malicious manifest escalation" can mean three things:

| Reading | In the paper? |
|---|---|
| (a) Runtime requests that go beyond the manifest are blocked | **Yes.** §3.1 Quote 2 ("aborts the execution") and §3.2 zero-privilege default |
| (b) A malicious server that declares too much is checked by the user | **Partly.** §3.2 consent over derived permissions; §3.1 human-review description. Not evaluated adversarially |
| (c) The manifest file is protected from tampering or substitution | **No.** Nothing described |

Read in context, right after "User confirmation layered on manifest", (a) is the natural
reading of the ticket, and the paper supports it almost word for word. The doc answered
reading (c), correctly, but presented that as a rebuttal of the whole ticket claim,
starting with the bolded "not literally in the paper".

### Rest of §3 (spot-checked while I had the text)

- Zero-privilege sentence: verbatim at §3.2. OK.
- Worked attack (poisoned tool description, overwrite plus exfiltration to
  `malicious.org`, blocked by the read-only mount and URL allowlist): matches §3.2. OK.
- 80.9%: matches the abstract and §4.1.3 ("80.9% of the manifests are correct without
  further modification"). OK.
- Minor nit, not a verdict driver: the bullet header "Manifests are auto-generated, not
  hand-written" overstates the paper. The paper says "Developers of MCP servers should
  declare the generic capabilities" and offers AgentManifestGen "to ease adoption", with
  the "developer-in-the-loop".

## Bottom line

- **Contradicted:** the bolded headline ("not literally in the paper") and the claim that
  consent is "generic MCP architecture, not an AgentBox-specific feature". AgentBound's
  §3.1 and §3.2 describe user consent over manifest-derived runtime permissions and an
  abort on requests beyond the manifest, both as AgentBound/AgentBox's own design. The
  verifier's Quotes 1 and 2 are real, verbatim, and placed correctly. §3.2 has a third,
  stronger passage ("explicitly granted by both its manifest and the user").
- **Confirmed:** no signature, hash, or integrity check for the manifest file. The paper
  also never tests an adversary-written manifest. The doc's advice to treat manifest
  tamper-resistance as a Limpet design gap is sound and should stay.
- **Net: PARTIAL.** The part that is wrong is the paragraph's headline, so a reader taking
  the doc at face value would wrongly conclude that AgentBox has no consent layer and no
  escalation ceiling.
- **Correction warranted.** Rewrite the final §3 bullet. Credit AgentBound with (i)
  per-run user consent over manifest-derived runtime permissions (§3.2) and (ii) an abort
  on permissions outside the manifest (§3.1). Drop the §2.2 Host-role attribution. Keep
  the manifest-integrity and adversarial-manifest gap as the open Limpet design item.
  Whether to file the correction is for whoever closes wq#4375.
