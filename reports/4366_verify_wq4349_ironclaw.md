# wq#4366 — Adversarial verify-pass of wq#4349 (PR#2247: dissect IronClaw (NEAR AI))

**Verdict: CONFIRM** — `research/WQ4349_IRONCLAW_DISSECTION.md` (merged at `592a01b2`,
present on `main`) holds up under independent re-sourcing and against the live Limpet repo
it cites. No fabricated specifics found; the one claim the author couldn't nail down
(AES-256-GCM) is correctly hedged rather than asserted.

## What was shipped

PR#2247 added `research/WQ4349_IRONCLAW_DISSECTION.md` (209 lines), a desk-research dissect
of NEAR AI's IronClaw agent runtime (WASM sandbox, capability-declared network/credential
model) with a gap-check against Limpet's current plugin system and six "patterns applicable
to Limpet" recommendations. PR branch was already merged into `main` by the time this verify
pass started (`git merge-base --is-ancestor 592a01b2 main` → true); no open diff to fetch.

## Probe

This is a research doc, not code — the probe is: (1) re-derive every external factual claim
from fresh, independent `fleet-search web` queries rather than trusting the doc's citations,
and (2) re-check every local-repo file:line citation against the actual files in this
worktree.

**Local citations — all exact, checked directly:**
- `plugin-manifest.schema.json:8` `additionalProperties: false` at top level — confirmed.
- `plugin-manifest.schema.json:43-51` free-form `capabilities` grab-bag
  (architectures/input_formats/output_formats) — confirmed verbatim.
- `plugin-manifest.schema.json:39` trust-tier description ("community=present but inert
  until promoted... meta-server refuses to proxy a community tool call") — confirmed
  verbatim, word-for-word match to the doc's §7 paraphrase.
- `limpet_tools.py:55-56` (`_git` via `subprocess.run`), `:167` (`_clone_pinned`), `:312`
  (`_cmd_verify`), `:338` (`_cmd_elevate`) — all four confirmed at the cited lines.
- Grepped `limpet_tools.py` for `subprocess\.|wasmtime|sandbox|seccomp|rlimit|cgroup`: only
  hit is the one `_git` subprocess.run call plus two string literals about trust-tier
  "sandboxing" (community tier being inert, not an OS sandbox) — confirms the doc's claim
  that Limpet has no execution-time resource/network boundary analogous to IronClaw's
  fuel/memory/rate caps.

**External citations — re-sourced fresh via `fleet-search web`, not trusted from the doc:**
- NEAR AI launch blog existence/framing (NEARCON 2026, "IronClaw... built in Rust and
  deployed inside encrypted TEEs... carries forward the original OpenClaw vision") —
  confirmed, near-verbatim match to §6.
- `WASM_FUEL_LIMIT=100000000` / `WASM_MEMORY_LIMIT=16777216` defaults — confirmed verbatim
  against `docs.ironclaw.com/capabilities/sandboxed-tools` (en + zh mirrors).
- Allowlist-bypass defenses (scheme pinning, `../` path-traversal normalization, IPv6
  bracket-stripping, the exact Rust guard `parsed.username().is_empty() ||
  parsed.password().is_some()`) — confirmed verbatim against
  `mintlify.wiki/nearai/ironclaw/security/network-security`, the doc's own cited source.
- Credential injection spec (`AuthorizationBearer {}`, `AuthorizationBasic {username}`,
  `Header {name, prefix}`, `QueryParam {name}`) — confirmed via three independent,
  mutually-corroborating sources: `mintlify.wiki/nearai/ironclaw/security/credentials` (the
  doc's own cited source, shows `{"AuthorizationBearer": ...}` / `QueryParam { name:
  "api_key" }`), the actual Rust source `github.com/nearai/ironclaw/blob/.../credential_injector.rs`
  (`:AuthorizationBearer => { ... format!("Bearer {}", secret.expose()) ... }`), and a
  `docs.rs/ironclaw` API-reference page for `enum CredentialLocation`. Three-source
  corroboration against the one injection-spec claim I expected to be the easiest thing to
  silently hallucinate (it reads exactly like LLM-plausible Rust-enum syntax) — it isn't.
- "Zero outbound by default" quote ("The only external connections are to LLM providers...
  You control which provider via configuration.") — confirmed verbatim, word-for-word,
  against `mintlify.wiki/logicminds/ironclaw/security/data-protection`, the doc's own cited
  source.
- AES-256-GCM (the one claim the doc explicitly flags as unverified, "ticket's prior claim,
  not something this pass independently re-verified"): my own fresh search only turned up
  `encrypted_value | Nonce + ciphertext + auth tag` from a third-party mirror — AEAD-shaped,
  consistent with but not confirming GCM specifically. The original author's hedge was the
  correct call, not an evasion.

## Adversarial checks that did not break it

- **Tried to catch a conflated/hallucinated schema.** A third-party mirror
  (`mintlify.wiki/logicminds/ironclaw/security/credential-protection`, NOT one of the doc's
  cited sources) describes credential config as a flat `{"secret_name", "location":
  "authorization_bearer", "host_patterns": [...]}`, and another uncited mirror
  (`docs.ironclaw.com/.../sandboxed-tools`) shows `capabilities.json`'s credentials field as
  `"credentials": [{"name", "inject_as", "format": "Bearer {value}"}]` — neither matches the
  doc's `"secrets": {"allowed_names": [...]}` + tagged-enum framing in §2. Initially read as
  a possible fabrication. On closer check this is mirror-quality noise, not a doc error: the
  doc's §2 cites `mintlify.wiki/nearai/ironclaw/security/credentials` specifically (the
  `nearai`-org-path mirror), which **does** use the tagged-enum shape, and that shape is
  independently confirmed by the actual GitHub source and a docs.rs reference the doc never
  even cited. The doc picked the right source among several inconsistent scraped mirrors;
  this is the original research holding up, not a flaw.
- **Chased a "Reborn" rework lead that would have been a real contradiction if confirmed.**
  Deeper search snippets surfaced what looked like a later commit (`b237ecd`, "docs: rework
  the security section against the shipped Reborn stack") replacing the entire
  `capabilities.json`-sidecar model with a `manifest.toml` + per-call runtime-authorization
  model — which, if real and chronologically after the doc's cited commit `29582a9`, would
  undercut the premise §1/§2/§7/§8 and the Limpet recommendations rest on. Checked it
  adversarially against itself: `b237ecd`'s claimed "before" diff text is identical to
  `29582a9`'s "before" text rather than to `29582a9`'s "after" text, which is inconsistent
  with `b237ecd` coming later in the same file's history. No `gh`/GitHub API token is
  available from this node (confirmed: `fleet-search github` → 401, same constraint the
  original ticket disclosed) to resolve this by timestamp. Separately, the same search pass
  also surfaced an unrelated `github.com/IronSecCo/ironclaw` repo — a **different** project
  with the same product name — raising a real namespace-collision risk for *any* snippet-level
  research into "ironclaw." Given the internal inconsistency and the collision risk, I'm not
  treating this as a confirmed finding — it doesn't clear the bar to contradict — but it's
  worth naming so a future pass with real `gh` credentials checks
  `github.com/nearai/ironclaw` directly rather than trusting search snippets on this one
  point.

## Non-issues

- §8's six Limpet recommendations are consistently framed as patterns to adapt, not a
  literal schema to copy-paste, and recommendation 4 explicitly scopes the WASM/fuel-cap
  pattern as "if/when," not "do this now" — so even if the capabilities.json-vs-manifest.toml
  question above turned out to matter, the recommendations don't hard-commit Limpet to a
  specific wire format that would need unwinding.
- "Non-adoption" scoping (PostgreSQL/TEE out of scope) matches what the ticket says it asked
  for.

## Bottom line

Every falsifiable, checkable claim in the doc — four local file:line citations and six
external technical specifics — reproduced correctly against fresh, independent sourcing.
The one claim flagged as uncertain was correctly flagged, not asserted. One secondary lead
(possible later IronClaw rework) could have been a real contradiction but didn't survive
its own internal-consistency check, so it's recorded as a caveat, not a finding.
No escalation.
