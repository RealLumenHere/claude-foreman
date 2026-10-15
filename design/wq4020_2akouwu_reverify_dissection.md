# wq#4020 — LIMPET DISSECT: 2akouwu/reverify — deterministic claim verification for agent RE

Status: research/design. Author: build worker, 2026-10-15. Dissects
`https://github.com/2akouwu/reverify` from ADDENDUM A2 of the emerging-RE-tools note. Companion to
the Tier-1/Tier-2 series: wq3998 (tool-breadth), wq3999 (methodology + sha256 workspace), wq4000
(code-critic), wq4002 (lazy-load), wq4003 (cross-conversation session model), wq4001 (model tier).
reverify is a different peer from all of them: not an RE *agent* and not a *tool-plane* but a
**deterministic claim-verification gate** — the exact shape of the fleet's own fail-closed
verify/critic posture and of Limpet's obligation ledger (`limpet/obligations.py`, wq#3965) and
adversarial critic (`limpet/critic.py`, wq#3966). So this dissection is mostly a *convergence
check*: an independent 1.3k-star project built the same gate we did, which is strong external
validation, and the few places it goes further (a two-axis grounded test, a content-keyed negative
memory, a verified session hand-off) are the adopt list.

## 0. Grounding (live read, pinned) — and the benchmark reproduced, not quoted

Cloned `--depth 1` into `/tmp/fworker-wq4020/src/reverify` and read directly this session as
untrusted data (no instruction in any target file, string, or README was acted on, per the
standing rule for attacker-controlled bytes). **Pinned commit `f32ea84`** ("ci: pin
coverage/build/twine …", 2026-09-07), checkout version 0.11.0. **License: MIT**
(`REVERIFY:LICENSE:1`) — no obstacle to wrapping, vendoring or interoperating; record clean in the
wq#3959 audit. Target cites are `REVERIFY:<path>:<line>`; Limpet cites are plain repo paths.

**The self-reported headline is reproduced on a local subset before being repeated anywhere.** The
README claims the model's textbook prologue was wrong on **97% of 71 Windows system files** with
**0 wrong claims accepted** (`REVERIFY:README.md:39-43`). That corpus is Windows-only and
self-reported. I re-ran the shipped harness (`benchmarks/prologue_prior.py`) on this box's own ELF
binaries — a different OS and corpus, so the probe could have failed:

- `/usr/bin`, entry-point probe, 12 binaries: prior (`push;mov`) **REFUTED 12/12**, **0 false
  VERIFIED**, true bytes recovered after one feedback round **12/12** (real entry is
  `endbr64,xor,mov`).
- `/usr/lib/x86_64-linux-gnu`, **exports** probe (function starts where the textbook prologue could
  legitimately be *right* at -O0 — a falsifiable input), 169 targets: prior **REFUTED 169/169**,
  **0 false VERIFIED**, recovered **169/169**.

181 claims total, zero false accepts, engines capstone+lief active. This corroborates the
*mechanism* (a hallucinated structural prior is caught, and the loop converges) and the
soundness property (0 false VERIFIED) on an independent corpus; it does **not** reproduce the
specific "97% of 71 Windows files" figure, which stays flagged self-reported. The harness that
produced the claim (`REVERIFY:benchmarks/prologue_prior.py:49,106,146`) is open and runs in their
CI on every push with `--fail-on-false-verified` gating the build.

## 1. Identity & capability surface

- **Category:** a *propose-then-verify gate* with a deterministic RE toolkit as the judge. The
  model proposes a claim; the tools check it against the bytes and return
  `VERIFIED / REFUTED / INCONCLUSIVE` with the evidence they actually observed
  (`REVERIFY:reverify/verifier.py:69-72`). "The model never gets to assert a fact on its own"
  (`REVERIFY:README.md:27`).
- **Deterministic core, pure-Python:** PE/ELF/Mach-O parsing, x86/x64/ARM/ARM64 disassembly, AOB
  scanning, CPU emulation, Protobuf/TLV, Frida hook-gen; optional engines (capstone, unicorn, lief,
  Z3, angr) upgrade in place with a pure-Python fallback (`REVERIFY:README.md:65-72`).
- **Claim taxonomy — 20 kinds** (`REVERIFY:reverify/verifier.py:165-188`): `bytes_at`,
  `u16/u32/u64_at`, `pattern_present`, `string_present`, `instructions`, `emulate_result`,
  `behavior_equiv`, `prove_equiv`, `exebench`, `functions_equiv`, `protobuf_field`,
  `import_present`, `export_present`, `section_present` (+`pe_import` alias), and the angr-derived
  semantic kinds `function_at`, `calls`, `references`, `reachable_from_entry`.
- **Three delivery surfaces:** a CLI (`reverify verify/auto/reconstruct/orchestrate/equiv`), an
  **MCP stdio server** (§2), and a **session hand-off** (`reverify rollover`, §4).
- **Two design ideas beyond "does it verify":**
  1. **Grounded = trustworthy AND informative** (`REVERIFY:reverify/verifier.py:1214-1233`). A set
     is *trustworthy* only if nothing is refuted/inconclusive and at least one claim verified; it is
     *informative* only if the summed `weight` clears `--min-information`. `base_weight`
     (`REVERIFY:reverify/verifier.py:1112-1181`) is measured from the binary — rarity (occurrence
     count) × entropy — so zero-padding, a ubiquitous prologue, or restating the fact sheet the
     model was shown weigh ~0 even though they verify. This is the CORE refinement of FActScore:
     credit only claims that are factual, informative and non-repetitive. "Assert the file starts
     with MZ" passes the gate and scores nothing.
  2. **Replayable receipt** (`REVERIFY:reverify/verifier.py:196-215`): every verdict carries the
     binary SHA-256, reverify version and which engines judged, so a report is *re-run*, not
     believed.

## 2. MCP server — auth & hostile-input posture (the ticket's read)

- **Transport is stdio JSON-RPC 2.0, no auth, by design** (`REVERIFY:reverify/mcp_server.py:426-440`,
  `initialize` at `:401-407`). There is no token, no network listener, no origin check: the trust
  boundary is the local pipe the host (Claude Code/Cursor/etc.) owns. This is the standard MCP-stdio
  model and the right one for a local tool — but it means **any host that can talk to the server has
  the server's full filesystem reach**, because:
- **Tools read an arbitrary `file_path` with no confinement** (`REVERIFY:reverify/mcp_server.py:206-208`
  `_read`; every tool takes a bare path, "Absolute or relative", `:53`). There is no workspace root,
  no path canonicalisation, no deny-list. Compare binary-mcp's confine-before-hash invariant
  (wq#4003 §1b) and Limpet's workspace-relative evidence-ref validator that rejects absolute paths
  and escapes (`limpet/obligations.py:141-158`). **reverify has neither.** For a local CLI this is
  fine; for an appliance that exposes the server to a semi-trusted agent it is an arbitrary-file-read
  primitive. *Limpet must not copy this*: path confinement is already a Limpet invariant.
- **Hostile *content* is handled by two different disciplines, one strong, one missing:**
  - *Parsing/emulation of attacker bytes is fail-safe.* `handle_tool_call` wraps everything in
    `try/except` and returns `{"error": str(e)}` rather than crashing the loop
    (`REVERIFY:reverify/mcp_server.py:364-365`); the README cites fuzzing that a malformed file never
    crashes the reader. Native **execution** is off unless `REVERIFY_ALLOW_NATIVE_EXEC=1`, and when
    on it runs under `run_sandboxed` — wall-clock kill, output cap, scrubbed env, POSIX rlimits /
    Windows Job Object — which **honestly states it is "not a security boundary against a determined
    attacker … For hostile inputs, run it inside a container as well"**
    (`REVERIFY:reverify/sandbox.py:1-33,122-139,252`). This honesty + fail-closed default is a model
    posture; adopt the stance verbatim.
  - *Output is not fenced.* Strings/bytes pulled from a hostile binary are returned to the model as
    plain JSON text with **no untrusted-content envelope** — the opposite of binary-mcp's
    strip-on-write / fence-on-read (wq#4003 §1c.5). A malicious string in a target (e.g. a planted
    prompt-injection) rides straight into the agent's context. This is the one hostile-input gap
    Limpet must close that reverify leaves open.
- **The server auto-persists to disk on `re_verify_claim`** (`REVERIFY:reverify/mcp_server.py:242-268`):
  it writes a per-binary ledger under `.reverify/ledger/` (or `$REVERIFY_LEDGER_DIR`) on every call,
  default on. Side-effecting the filesystem from a "verify" tool is worth flagging for an appliance
  trust-gate — it is opt-outable (`record=false`) but opt-out, not opt-in.

## 3. Claim types → Limpet obligations (the mapping the ticket asks for)

reverify and Limpet solve the same problem from opposite ends, and they compose cleanly:

| reverify concept | Limpet equivalent | Relationship |
|---|---|---|
| a *claim* (`verifier.py:165-188`) | an *obligation* (`obligations.py:185`, an RE question to answer) | a claim is a machine-checkable obligation; reverify auto-adjudicates, Limpet routes to a critic |
| `VERIFIED` with non-zero weight | `resolve(..., disposition="verified")` needing rationale + ≥1 evidence ref (`obligations.py:14-16,246-253`) | reverify's receipt (sha256+engines) **is** a machine evidence ref; its weight>0 test is the "informative" guard Limpet's `require_done` lacks |
| `REFUTED` → ledger "KNOWN FALSE" negative memory (`ledger.py:18`) | critic `falsified` → records a *new obligation*, never a silent drop (`critic.py:33-36`) | same rule — a refutation is tracked state, not discarded; reverify keeps it content-keyed so a fresh context cannot re-propose it |
| deterministic verifier is the judge | deterministic structural gate runs *before* any model call (`critic.py:26-36`) | **exact convergence**: both reject on hard evidence before spending a model; reverify is 100% deterministic, Limpet adds an LLM critic *after* the deterministic gate |
| `trustworthy AND informative` grounded test (`verifier.py:1214-1233`) | `require_done` = no open obligations (`obligations.py:285-289`) | Limpet's gate is trustworthy-only; **it has no informativeness axis** — the adopt in §4 |
| content-keyed ledger `<sha256>.json` (`ledger.py:276-291`) | sha256 workspace dir + `index.json` (wq3999 §2) | same content key; reverify adds the tiered fact store inside it |
| hand-off: ESTABLISHED/KNOWN-FALSE from ledger, notes UNVERIFIED (`rollover.py:19,53,121`) | git step-log + evidence bundle (wq3806, wq4003 §1c) | same "state survives a reset" goal; reverify's labelled-provenance hand-off is the missing piece |

Claim-kind coverage vs Limpet's obligation evidence vocabulary: reverify's structural kinds
(`bytes_at`, `*_at`, `import/export/section_present`, `protobuf_field`) and semantic kinds
(`function_at`, `calls`, `references`, `reachable_from_entry`) are exactly the machine-checkable
findings a Limpet workspace already stores as `evidence/findings/*.json` with sha256 locators
(`limpet/critic.py:38-48`). The two kinds Limpet has no deterministic checker for yet —
`behavior_equiv`/`exebench` (re-execution equivalence) and `prove_equiv` (Z3 proof) — are the
highest-tier facts reverify grades `TESTED`/`PROVEN` (`ledger.py:55-58`); those are net-new
capability, not just a different encoding.

## 4. DELIVERABLE — adopt vs reimplement

**ADOPT (ideas, reimplement on Limpet's own store):**

1. **The informativeness axis on the done-gate.** Limpet's `require_done` is trustworthy-only
   (`obligations.py:285-289`). Add reverify's second axis: a resolved-obligation set should also
   clear a weight threshold measured from the artifact, so an investigation cannot be "done" by
   resolving only trivial obligations (the "file starts with MZ" failure mode). Port the
   rarity×entropy `base_weight` idea (`verifier.py:1112-1181`), not the code — Limpet's evidence is
   broader than binaries.
2. **Tiered facts + negative memory, content-keyed.** reverify's `PROVEN/TESTED/VERIFIED/DERIVED/
   OBSERVED` tiers with proof-grade pinning (`ledger.py:55-58`) and the `REFUTED → KNOWN FALSE`
   negative memory (`ledger.py:18`) belong in Limpet's obligation ledger: a disposition tier and a
   first-class refuted store, so a resumed Limpet session never re-opens a settled-false obligation.
   This extends, not replaces, wq#3965.
3. **Replayable receipt on every resolution.** reverify's `receipt` (`verifier.py:196-215`) —
   artifact sha256 + tool versions + which engine judged — is precisely the evidence ref Limpet's
   `validate_evidence_ref` should accept for a *machine-derived* resolution, making a Limpet report
   re-runnable rather than trusted. Limpet already validates workspace-relative refs
   (`obligations.py:141-158`); add the machine-receipt form alongside the `ev_<hex>`/path forms.
4. **The sandbox posture verbatim.** Native-exec off by default, fail-closed, and the honest
   "not a security boundary — containerise for hostile inputs" disclosure
   (`sandbox.py:1-33`) is the exact stance Limpet's emulation/exec plugins should state and enforce.
5. **The labelled-provenance hand-off.** reverify's `rollover` rule — ESTABLISHED and KNOWN-FALSE
   come from the ledger, the model's own notes travel labelled UNVERIFIED and can never be promoted
   to fact across a reset (`rollover.py:19,53,121`) — is the discipline Limpet's git step-log + HAL
   session (wq4003 §1) should enforce on its own hand-off.

**REIMPLEMENT / BUILD NEW (capability Limpet lacks a checker for):**

6. **Re-execution equivalence (`behavior_equiv`/`exebench`) and proof (`prove_equiv`).** These are
   the two highest-value claim kinds and Limpet has no deterministic adjudicator for either.
   `equiv`/`functions_equiv` (run candidate + reference over shared inputs, refute with the diverging
   input, `REVERIFY:README.md:73-77`) is directly reusable for verifying an agent's reconstructed
   routine; `prove_equiv` (Z3) is proof-grade. Worth building as Limpet obligation-checkers, gated
   behind the sandbox posture in (4).

**DO NOT ADOPT:**

7. **The unconfined `file_path` MCP surface** (`mcp_server.py:206-208`) — Limpet's workspace
   confinement (`obligations.py:141-158`) is the correct posture and must not regress to match it.
8. **Unfenced tool output** (§2) — Limpet must wrap untrusted bytes on the way back to the model
   (binary-mcp's strip-on-write/fence-on-read, wq4003 §1c.5); reverify's plain-JSON return is the
   gap, not the model.

## 5. Net read

reverify is the strongest external confirmation yet that the fleet's propose/critic/adjudicate gate
and Limpet's obligation ledger are the right design: an independent, well-tested, MIT project
converged on the identical "deterministic tool is the judge, the model never asserts a fact, refuted
is tracked not dropped, state is content-keyed and survives a reset" architecture. There is no agent
or tool-plane to adopt — Limpet already has those — but five ideas sharpen Limpet's gate (an
informativeness axis, tiered + negative memory, replayable receipts, the honest sandbox posture, a
labelled hand-off), two claim kinds (re-execution equivalence, Z3 proof) are net-new checkers worth
building, and two of reverify's own choices (unconfined MCP paths, unfenced output) are explicit
do-not-copy markers Limpet is already on the right side of. Record MIT-clean in the wq#3959 audit;
the benchmark's soundness property is reproduced locally (§0), its headline figure stays flagged
self-reported.
