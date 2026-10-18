# LIMPET design — behavioral conformance suite (`limpet cts`) for HAL manifests and MCP tool plugins

> Status: DESIGN + TEST MATRIX ONLY (wq#4226). No code in this ticket.
> Source pattern: lampOS ships `make cts` — "is this a valid device" — so a new
> body or driver is checked *mechanically* before it is trusted
> (`/home/lumen/scratch/research/lampOS-evaluation.md`, wq#4209). Limpet's
> validation today stops at TOML schema (`hal-spec.md` ~L658 for HAL manifests;
> `limpet/manifest.py:249-309` `validate_manifest` for plugin manifests): a
> manifest can be schema-valid yet behaviorally broken or unsafe.
>
> Prior-art note: the doctrine's external web search was attempted via
> `fleet-search web` this session and all backends were down (SearXNG engines
> CAPTCHA/suspended, no Brave key, deepseek 402 — the known wq#2595 hole; same
> outage wq#4225 recorded). wq#4209's lampOS evaluation is the completed
> primary prior-art study for this pattern and is cited throughout. The
> two-layer shape below (static manifest checks + behavioral harness against a
> mock host) is also the industry-standard CTS/VTS split — a schema/vocabulary
> pass plus a black-box behavioral suite run against the device under test —
> which lampOS's `make cts` itself follows.
>
> Sibling tickets from the same evaluation: wq#4224 (frozen
> `limpet.capabilities.v1` vocabulary — this suite's Tier 0 vocabulary check
> depends on it), wq#4225 (capability-filtered tool exposure, DONE —
> `design/wq4225_limpet_capability_filtered_tool_exposure.md`), wq#4227
> (hal-spec dup §9 reconcile + bind/auth fail-closed — a precondition for the
> Tier 1 rules below having a single authoritative source).

## 1. Problem

Limpet has three validation surfaces today, and all of them are **static**:

| Surface | What it checks | What it cannot check |
|---|---|---|
| `limpet validate` / registry scan (`limpet/manifest.py:249-309`, `limpet/cli.py:32-52`) | TOML parses; name/version/kind/category/conflicts/deps/health/mutation/model_routes well-formed. Fail-closed on malformed input. | Whether the plugin *behaves* as its manifest claims. |
| `design/limpet/plugin-manifest.schema.json` (wq#3806, extended by wq#4056) | JSON-Schema shape incl. required `trust` tier, `[integrity]`/`[provenance]`/`[signature]` fields. | Same — shape only. |
| `hal install` (hal-spec.md ~L658) | HAL device manifest TOML schema. | Whether the driver respects the energize-confirm gate, logs evidence, or classifies its actions. |

Concretely, a schema-valid plugin can today:

1. **Omit `action_class`** on an action. hal-spec §9 says missing
   classification defaults to energizing/destructive (the two §9 duplicates
   state it at L704 and L833 — wq#4227 reconciles them), but nothing
   *mechanically verifies* every action carries a class, or that the
   fail-closed default is actually applied by the host rather than silently
   dropped.
2. **Bypass the energize-confirm gate.** The reference meta-server's
   `tools/call` path (`ops/wq3808-limpet-mcp-meta-server-a1/meta_server.py:252-279`)
   invokes any registered tool directly — there is no confirm step at all
   (recorded in `limpet/DESIGN-DECISIONS.md`: "the meta-server has no
   operator-confirm step"). The gate is doctrine
   (`docs/limpet_readme.md:68`, hal-spec §9) and a designed enforcement path
   (wq#4059 `limpet-sentinel` ConfirmToken flow), but no test exists that
   would go red when an implementation ships without it.
3. **Skip evidence logging.** `_meta.evidence` blocks and the append-only
   `evidence/tool-calls.jsonl` exist
   (`ops/wq3808-limpet-mcp-meta-server-a1/evidence.py:60-128`), but nothing
   verifies a *new* plugin or meta-server build still emits them, or that
   confirm decisions enter the chain.

lampOS's answer is a conformance suite a vendor runs locally: `make cts`
answers "is this a valid device" before the body is trusted. This doc designs
the Limpet equivalent: **`limpet cts`** — a behavioral conformance suite for
HAL device manifests and MCP tool plugins. Caveat carried over from wq#4209:
lampOS's own gate fails *open* (missing SAFETY.md ⇒ unrestricted, agents run
as root). We take the suite shape, not the policy — every Limpet CTS check
defaults fail-closed.

## 2. Relationship to wq#4172 (spec-conformance facade) — no duplication

wq#4172 shipped a *sanitization* spec-conformance suite:
`projects/limpet/src/sanitize.py` is a thin facade delegating to foreman's
`limpet/injection.py` + `limpet/binary_scrub.py`, and its
`tests/test_sanitize.py` (83 tests incl. seeded fuzz) asserts the facade's
*behavior* against the sanitize spec (role-marker defang, 500-char cap,
fail-closed on import failure). That is already a behavioral conformance
suite for one domain.

The CTS **generalizes the pattern and absorbs the domain by reference**:

- CTS defines the *runner, verdict model, and fixture conventions* (§4–§6).
- Sanitization conformance remains owned by wq#4172's suite. The CTS runner
  invokes it as one pluggable **conformance domain** (`CTS-SAN` in the
  matrix) and consumes its pass/fail — it does **not** re-specify sanitize
  rules, duplicate its pattern lists, or add parallel sanitize tests. Same
  rule as the facade itself: one source of truth, thin delegation.
- Precedent for the delegation shape: wq#4172's facade fails closed when the
  delegate is missing. CTS does the same — if a domain suite cannot be
  loaded, that domain reports FAIL (not SKIP).

## 3. What the suite certifies (scope)

Two certifiable subjects, one runner:

- **A HAL device manifest** (`hardware.d`-style TOML / hal-spec device
  manifest): "is this a valid Limpet device" — schema, vocabulary,
  action-class completeness.
- **An MCP tool plugin** (`limpet-plugin.toml` + its running server):
  "is this a valid Limpet tool" — all of the above **plus** behavioral tiers
  run against a mock meta-server/sentinel harness.

Out of scope: supply-chain trust (signatures, pinning — wq#4056 owns it; CTS
only *checks the fields exist and verify*, Tier 0), capability-filtered
exposure logic (wq#4225 owns the meta-server side; CTS checks the plugin
side declares `requires_capabilities` validly), model routing quality,
performance.

## 4. Suite architecture — four tiers, fail-closed aggregation

```
limpet cts <manifest-or-plugin-dir> [--kind hal|plugin] [--tier 0..3] [--json]

 Tier 0  STATIC      schema + vocabulary          (pure, no subprocess)
 Tier 1  SAFETY      action_class completeness    (pure)
 Tier 2  GATE        behavioral: mock meta-server + mock sentinel harness
 Tier 3  EVIDENCE    behavioral: evidence-chain of calls and confirms
          + CTS-SAN  delegated domain: wq#4172 sanitize suite (plugins only)
```

- **Tier 0 — static conformance.** Manifest parses and passes the existing
  validators (`validate_manifest` for plugins; hal-spec schema for HAL).
  Then the **vocabulary check**: every capability name in `provides` /
  `requires_capabilities` must be a member of frozen `limpet.capabilities.v1`
  (wq#4224). Unknown name ⇒ FAIL (never "assume it's new"). Until v1 is
  frozen the vocabulary check reports WARN-RESERVED, same staging wq#4225 §8
  uses for its fields — the check exists from day one so freezing the vocab
  flips it to enforcing without a suite change.
- **Tier 1 — safety classification.** Every action/capability the manifest
  declares carries `action_class ∈ {read_only, energizing, destructive}`.
  Missing class ⇒ the suite records that the fail-closed default (missing ⇒
  destructive, hal-spec §9 per wq#4227's L704/L833) *applies*, and the
  subject only passes if it is certified as behaving under that default —
  i.e. a manifest author cannot pass CTS while relying on an unclassified
  action being treated as read-only anywhere.
- **Tier 2 — gate cannot be bypassed.** Black-box tests against the plugin
  wired into a **mock meta-server + mock sentinel** (committed fixtures, §6).
  The mock sentinel implements wq#4059's ConfirmToken contract
  (`EnergizeRequest{action_class, capability, target_id, args_digest,
  workspace, nonce}` → signed `ConfirmToken{request_hash, exp, sig}`), with
  test hooks to approve, deny, delay, or go unreachable. The harness proves
  the *host-side contract* holds around this plugin and that the plugin
  cannot route around it (matrix rows G1–G8).
- **Tier 3 — evidence chain.** Every `tools/call` through the harness —
  success, error, denied-by-gate, confirmed-by-gate — must yield a
  `_meta.evidence` block and an append to `evidence/tool-calls.jsonl`
  matching `evidence.py`'s shape (`evidence_id = ev_<sha256(canonical block)>`,
  `args_sha256`, `ok`, `untrusted_content`). Confirm decisions must enter the
  chain as first-class records linked by `request_hash` (matrix rows E1–E6).

**Verdict model.** Per-check verdicts `PASS | FAIL | WARN | SKIP`; suite
verdict is fail-closed: any FAIL ⇒ NONCONFORMANT. SKIP is legal only for
structurally inapplicable checks (a `data`-kind plugin has no runtime, so
Tiers 2–3 SKIP; a HAL manifest with zero energizing capabilities skips G-rows
but then must contain zero `action_class = energizing|destructive` entries —
checked, so the skip itself is falsifiable). WARN never gates but is listed
in the report; the only WARNs are the two staged checks (vocabulary before
v1 freeze; dup-§9 source ambiguity before wq#4227 lands).

**Known-red at design time (intentional):** run against today's reference
meta-server, Tier 2 fails — `meta_server.py:252-279` has no gate. That is
the point: a conformance suite that cannot fail certifies nothing
(wq#2630). G-rows become green only when the wq#4059 sentinel path (or any
equivalent enforcement) ships. The suite is the mechanical definition of
"done" for that ticket.

## 5. Test matrix

Columns: **ID** · **Tier** · **Subject** (HAL manifest / plugin manifest /
plugin behavioral) · **Fixture / input** · **PASS iff** · **Falsified by**
(the committed negative fixture that must make it FAIL — every row ships
with one, per wq#2630).

### Tier 0 — static: schema + vocabulary

| ID | Subject | Fixture / input | PASS iff | Falsified by |
|---|---|---|---|---|
| S1 | plugin manifest | subject TOML | `validate_manifest` returns `[]` (`limpet/manifest.py:249-309`) | `fixtures/neg/s1_bad_kind.toml` (kind `widget`) |
| S2 | plugin manifest | subject TOML → JSON | passes `design/limpet/plugin-manifest.schema.json` incl. required `trust` tier | `fixtures/neg/s2_no_trust.toml` |
| S3 | plugin manifest | subject TOML | filename stem == `[plugin].name` (anti-spoof, `registry.py` scan rule) | `fixtures/neg/s3_name_mismatch/` |
| S4 | HAL manifest | subject TOML | hal-spec schema (~L658) passes | `fixtures/neg/s4_hal_malformed.toml` |
| S5 | both | subject TOML | every name in `provides` / `requires_capabilities` ∈ `limpet.capabilities.v1` (wq#4224); unknown ⇒ FAIL. WARN-RESERVED until v1 frozen | `fixtures/neg/s5_unknown_capability.toml` (`provides = ["quantum_flux"]`) |
| S6 | plugin manifest | subject TOML | wq#4056 integrity fields, when present, verify (pinned hash matches tree; signature parses). Absent fields ⇒ WARN not FAIL until wq#4056 ships | `fixtures/neg/s6_truncated_pin.toml` (reuses wq#4056 negatives in `design/limpet/test_plugin_manifest_schema.py:63`) |
| S7 | both | subject TOML | no undeclared-table smuggling: unknown top-level tables rejected (`additionalProperties: false`) | `fixtures/neg/s7_extra_table.toml` (`[totally_safe]`) |

### Tier 1 — safety classification (hal-spec §9)

| ID | Subject | Fixture / input | PASS iff | Falsified by |
|---|---|---|---|---|
| A1 | both | subject TOML | every declared action/energizing-capable capability carries `action_class ∈ {read_only, energizing, destructive}` | `fixtures/neg/a1_missing_class.toml` |
| A2 | both | `fixtures/neg/a1_missing_class.toml` | the *suite itself* classifies the unclassified action as `destructive` in its report (fail-closed default applied, hal-spec §9 / wq#4227 L704+L833) — the subject is reported NONCONFORMANT, never silently promoted to read_only | a suite build that reports it `read_only` (meta-test on the CTS runner) |
| A3 | both | subject TOML | no capability is classed `read_only` while also declaring a mutation boundary (`[mutation] escalates/network/writes`, `limpet/manifest.py:298-299`) that writes outside `workspace` — cross-field consistency | `fixtures/neg/a3_readonly_writes_device.toml` |
| A4 | HAL manifest | subject TOML | every `usb_ids`-bearing device entry with a flash/JTAG/glitch category declares `energizing` or `destructive`, never `read_only` (category→floor mapping table, frozen with the vocab) | `fixtures/neg/a4_flasher_readonly.toml` |
| A5 | both | subject TOML | `action_class` values are from the closed enum — a novel string (`"mostly_safe"`) is FAIL, not WARN | `fixtures/neg/a5_novel_class.toml` |

### Tier 2 — gate cannot be bypassed (mock meta-server + mock sentinel)

| ID | Subject | Fixture / input | PASS iff | Falsified by |
|---|---|---|---|---|
| G1 | plugin behavioral | energizing `tools/call`, sentinel configured to **not** be consulted | call is REFUSED (JSON-RPC error) and the device-side mock records zero energize operations — no confirm, no action | `fixtures/mock/gateless_host.py` — a host that proxies straight through (models today's `meta_server.py:252-279`); G1 must FAIL against it |
| G2 | plugin behavioral | energizing call, sentinel **denies** | call REFUSED; deny reason propagated; zero device operations | mock plugin `fixtures/mock/defiant_tool/` that executes anyway — harness detects device-side op |
| G3 | plugin behavioral | energizing call, sentinel approves, token **expired** (harness clock skew past `exp`) | REFUSED | token-accepting-despite-exp host variant |
| G4 | plugin behavioral | energizing call, valid token for args X, call made with args Y (`args_digest` mismatch ⇒ `request_hash` mismatch) | REFUSED — token binds to the exact call `(capability, args_digest, target_id, nonce)` per wq#4059 | host variant that checks only capability, not digest |
| G5 | plugin behavioral | **replay**: same valid token presented for a second, identical call | second call REFUSED (nonce single-use) | host variant without nonce ledger |
| G6 | plugin behavioral | energizing call, sentinel **unreachable** (socket down) | REFUSED — gate failure is deny, never fail-open (explicit inversion of lampOS's missing-SAFETY.md ⇒ unrestricted, wq#4209) | host variant that proceeds on sentinel timeout |
| G7 | plugin behavioral | plugin attempts **direct HAL access** bypassing the meta-server (mock HAL device node requires a token on its control channel) | zero un-tokened device-side operations recorded during the full suite run | `fixtures/mock/defiant_tool/` opens the mock device node directly — the mock HAL records it |
| G8 | plugin behavioral | `read_only` call | completes with **no** sentinel interaction (gate must not creep over autonomous analysis — `docs/limpet_readme.md:68`) | host variant that prompts on read-only (over-gating is also nonconformance) |

### Tier 3 — evidence chain of calls and confirms

| ID | Subject | Fixture / input | PASS iff | Falsified by |
|---|---|---|---|---|
| E1 | plugin behavioral | successful read-only call | result carries `_meta.evidence` and one line appended to `evidence/tool-calls.jsonl`; `evidence_id == ev_<sha256(canonical block)>` recomputable (`evidence.py:102`) | host variant that drops `_meta` |
| E2 | plugin behavioral | failing call (child error) | evidence block still written with `ok: false`, `error` set (`evidence.py:74-82`) — failure is evidence too | host variant that logs successes only |
| E3 | plugin behavioral | G2's denied energizing call | a **confirm-decision record** enters the chain: `{request_hash, action_class, decision: deny, sentinel_id, ts}` linked to the refused call's `args_sha256` | host variant that refuses without logging the decision |
| E4 | plugin behavioral | approved energizing call (G-path happy case) | chain contains request → confirm(decision: approve, `request_hash`) → execution evidence, all three linkable by `request_hash`/`args_sha256` | host that logs execution but not the confirm |
| E5 | plugin behavioral | full suite run | `tool-calls.jsonl` is append-only and line-atomic (single `write()` per record, `evidence.py:119-128`); re-parse finds zero torn/interleaved lines under the harness's concurrent-call test | host using buffered multi-write appends |
| E6 | plugin behavioral | plugin output containing untrusted/target-derived content | `untrusted_content: true` propagated per `evidence.py:99-100` fail-open-to-untrusted rule (default `True` when source doesn't claim otherwise) | host variant defaulting to trusted |

### Delegated domain

| ID | Subject | Fixture / input | PASS iff | Falsified by |
|---|---|---|---|---|
| CTS-SAN | plugin behavioral | n/a (delegation) | wq#4172's `tests/test_sanitize.py` suite loads and passes; load failure ⇒ FAIL (fail-closed, mirrors the facade's own import rule) | removing/renaming the delegate suite must flip this row to FAIL |

## 6. Fixtures and harness conventions

- **All fixtures are committed** under `projects/limpet/cts/fixtures/`
  (`golden/` one fully-conformant plugin + one HAL manifest; `neg/` one file
  per matrix row; `mock/` the mock meta-server, mock sentinel, mock HAL
  device recorder, and `defiant_tool`). No CTS check may read a live mutable
  file outside the fixture tree — pinned inputs keep every probe re-runnable
  (wq#2630).
- **The mock meta-server is a test double of the contract, not a fork of
  `meta_server.py`.** It implements `initialize`/`tools/list`/`tools/call`
  plus the sentinel hook, ~200 lines, stdlib-only (house rule, matching
  `manifest.py`'s pure-core convention). The real meta-server is itself a CTS
  *subject* (run `limpet cts --kind host` against it), which is how the
  known-red in §4 is expressed.
- **The mock sentinel** exposes `approve_next() / deny_next() / drop_next() /
  expire_tokens()` test hooks and signs tokens with a fixture key generated
  at harness start (never committed, never credential-shaped in source).
- **Meta-tests (A2 and the "Falsified by" column) are part of the suite**: CI
  runs the negative fixtures and asserts each produces its designated FAIL.
  A CTS release where any negative fixture passes is itself nonconformant.

## 7. Integration points

| Where | Tiers | Behavior |
|---|---|---|
| `hal install` | 0–1 | pre-install gate; NONCONFORMANT ⇒ install refused (closes the "schema only" gap at hal-spec ~L658) |
| registry daemon reconcile | 0 | already effectively runs S1; adds S3/S5/S7 — rejected manifests land in `unified.json["rejected"]` with the CTS row ID as reason |
| `limpet cts` CLI | all | vendor-facing, the lampOS `make cts` equivalent; `--json` for CI; exit 0 only on CONFORMANT (scriptable, matching `cli.py` conventions) |
| community plugin PR gate | all | a community plugin is promotable past `community` trust tier (wq#3806 schema / wq#4056 gate) only with a CONFORMANT CTS report attached |
| wq#4059 sentinel acceptance | 2–3 | G/E rows are the acceptance criteria for the sentinel build ticket — the suite is written first, red, and the sentinel turns it green |

## 8. Dependencies and status

| Piece | Status | This doc's stance |
|---|---|---|
| `limpet.capabilities.v1` vocabulary | wq#4224 in flight | S5 staged as WARN-RESERVED until frozen, then enforcing |
| hal-spec dup §9 reconcile | wq#4227 open | A-rows cite the fail-closed default both duplicates agree on (L704/L833); reconcile removes the WARN on source ambiguity |
| sentinel ConfirmToken contract | wq#4059 designed | G3–G5 token semantics pinned to that design; if the contract changes, the matrix rows change with it (single source: wq#4059 §2) |
| sanitize conformance | wq#4172 DONE | delegated (CTS-SAN), never duplicated |
| CTS implementation | **not this ticket** | next: build ticket for Tier 0–1 (pure, stdlib, no new deps — implementable against today's code), then Tier 2–3 harness alongside the sentinel build |
