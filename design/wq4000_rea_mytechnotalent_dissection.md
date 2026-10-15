# wq#4000 — LIMPET DISSECT: REA "Reverse Engineering Assistant" (mytechnotalent/rea)

Status: research/design. Author: build worker, 2026-10-15. Dissects
`https://github.com/mytechnotalent/rea` (+ `/wiki`) per the competitor dissection guide §1.3
(`scratch/research/limpet_competitor_dissection_guide.md:171-190`). Companion to the two
tier-1 dissections already landed: `design/wq3998_rea_morluto_dissection.md` (the *tool-breadth*
bar) and `design/wq3999_rex_skill_dissection.md` (the *methodology* bar). This one goes to a
third bar the other two do not reach: a **critic that is deterministic code, not a second model
prompt** — the strongest form of the house propose/critic/adjudicate loop, and the single
design this project exists to lift.

Name collision warning (carried from the guide and confirmed live): this `mytechnotalent/rea`
by **Kevin Thomas** is a different project from the viral `morluto/rea` of wq#3998. Same name,
different code, different language, different trust model. Keep them apart in the license audit
(wq#3959) and the competitive matrix (`design/wq3929_competitive_analysis.md`).

## 0. Grounding (live, not memory) — and a version correction

The repo was cloned to an isolated scratch dir and read directly this session
(`git clone --depth 1` into `/tmp/fworker-wq4000/rea-src`, read as untrusted data — no
instruction inside any source file, string, or embedded spec was acted on, per the fleet's
standing rule for attacker-controlled bytes). Verbatim citations below are `REA:<path>:<line>`
to distinguish target code from Limpet repo cites.

**The guide's snapshot is stale and the drift matters.** The guide (dated 2026-10-14) records
**v3.1.0**; the live workspace manifest reads **v5.6.3** (`REA:Cargo.toml:6`,
`REA:README.md:7`). In the interval the project grew from a one-shot CLI into *also* an MCP
server exposing 31 tools (`REA:README.md:214`) — i.e. it has moved toward the morluto/rea
agent-tool-plane shape while keeping its distinguishing deterministic backbone. Any Limpet
decision keyed to "v3.1, CLI-only, OpenRouter-only" is working from an 18-month-old photograph;
the facts below are from the v5.6.3 tree.

Confirmed structure: a Cargo **workspace** of five `#![forbid(unsafe_code)]` crates
(`REA:crates/*/src/lib.rs:29`) — `rea` (CLI + MCP + the agent loop), `rea-engine` (the
deterministic engine), `rea-sleigh` (embedded SLEIGH + pure-Rust decoder), `rea-report`
(md/pdf), `reverse-engineering` (MCP surface). MIT, "Copyright (c) 2026 Kevin Thomas"
(`REA:LICENSE:1-3`) — adoptable; record in the license audit.

## 1. Identity, capability surface, control flow

- **Category:** self-contained autonomous RE framework. `rea <file>` → `re-<stem>.{md,pdf,json}`
  beside the target (`REA:crates/rea/src/paths.rs:73-76`). Its own one-liner: *"Targets in.
  Answers out. One command. Hybrid autonomous multi-agent reasoning and a deterministic engine.
  Built in 100% pure Rust — run anywhere, install nothing, and every claim verified."*
  (`REA:README.md:17`).
- **Pure-Rust, zero-C posture is real.** The PDF is typeset by **Typst** (a pure-Rust engine,
  `REA:crates/rea-report/Cargo.toml:10-12`), not LaTeX; non-x86 disassembly is the embedded
  SLEIGH engine (§4), not a shelled-out Ghidra. No Java, Python, or external CLI is invoked —
  a single static binary. This is the inverse of REx@Skill's 33-script Nix toolchain
  (wq#3999): REA's reproducibility story is *"there is nothing to install,"* REx's is
  *"everything is pinned."* Both beat Limpet's current unpinned host-tool assumption.
- **Formats:** PE/ELF/Mach-O/universal, Go (pclntab/modinfo), .NET CIL, WASM, EVM, SQLite,
  Linux coredumps, Apple plists/bundles, Windows PDB, and firmware — U-Boot `uImage`, SquashFS
  4.0, embedded Linux partitions, raw ARM Cortex-M images (`REA:README.md:50-76`).
- **Control flow (the load-bearing shape):**
  `analyze_path()` → **deterministic** `rea_engine::analyze()` produces an `Analysis`
  (`REA:crates/rea/src/cli.rs:1082`) → `write_artifacts` dumps the JSON + full asm listing
  straight from that `Analysis` (`REA:crates/rea/src/cli.rs:1105-1126`) → `emit_reports` calls
  the **optional** LLM narrative `agent::evaluate(analysis, bytes)` and renders md+pdf
  (`REA:crates/rea/src/cli.rs:1055-1062`). The LLM is *downstream* of the engine and *never in
  the extraction loop.* With no API key the agent returns `None` and REA still ships a complete
  deterministic report (`REA:README.md:32`; `agent::evaluate` is `Option`-returning,
  `REA:crates/rea/src/agent.rs:1596-1602`). **The deterministic engine is the product; the LLM
  is a narrator bolted to its side.** That inversion is the whole lesson.

## 2. THE PRIZE — the deterministic-engine-checks-LLM critic (code, not a prompt)

This is the design the ticket sends us for, and it is smaller and sharper than the guide's prose
implies. There are in fact **two** critic layers; only the first is the prize.

### 2a. The code critic (unconditional, pure Rust)

Every report the analyst writes passes through `Session::verify` before it is returned
(`REA:crates/rea/src/agent.rs:1053-1057`):

```
fn verify(..., draft) -> String {
    let audited  = self.audit(key, draft);     // layer 2b (optional, model-based)
    let iterated = self.iterate(key, ctx, audited);
    redact(&iterated, &self.seen)               // layer 2a (always, code)
}
```

`redact`/`unverified` (`REA:crates/rea/src/agent.rs:1462-1554`) are **pure functions with no
model call**:

1. `unverified(text, seen)` extracts two citation classes from the model's prose —
   double-quoted tokens (`quotes`, `REA:...:1551`) and `0x`-prefixed hex addresses of ≥3
   digits (`addresses`, `REA:...:1531`) — and keeps only those **absent from the engine
   corpus** (`present` = lowercased substring containment, `REA:...:1517`).
2. The corpus `seen` is the engine's ground truth: the evidence dossier **plus every string the
   engine extracted** (`corpus()`, `REA:crates/rea/src/agent.rs:1631-1634`). A hallucinated
   address or invented flag string simply is not in it.
3. `redact` then **deletes every report line citing an unproven token** (`keep_lines`,
   `REA:...:1482-1487`) and appends `"[Verification] Removed N unverified claim(s)…"`; a clean
   report instead gets the `VERIFIED_NOTE` banner (`REA:...:1462-1469`).

Before deletion, the model gets up to `VERIFY_ROUNDS = 3` (`REA:...:154,1088-1097`) tool-enabled
chances to *prove or rewrite* each flagged citation (`PROVE_PROMPT`, `REA:...:157-160`). Proof
means "make the engine emit this token"; failing that, the line dies. The unit tests pin the
behavior exactly: a fabricated `"ABC_FAKE_FLAG"` is stripped, a corpus-present `"REAL_FLAG"` is
kept (`REA:crates/rea/src/agent.rs:1681-1696`).

**Why this is the strongest critic form.** A second *prompt* grading the first shares the first
model's blind spots and can be argued out of a correct objection (this is exactly why REx@Skill
uses five *blind* readers, wq#3999 §4). A *code* critic cannot be sweet-talked: either the token
is in the deterministic engine's output or it is not, and the check is `O(len)` string
containment. It is cheap, total, and adversarially robust in the one way an LLM judge never is.

### 2b. The model critic (optional, `REA_VERIFY`)

Layer 2b is the classic propose/critic/adjudicate and is **gated off by default**
(`verify_enabled()` reads `REA_VERIFY`, `REA:crates/rea/src/agent.rs:191-194,1069-1075`): three
adversarial **reviewers**, each with a fixed focus — unsupported addresses/strings, unsupported
behavior/logic claims, and secrets/keys/endpoints/attribution (`REVIEW_FOCUSES`,
`REA:...:104-108`) — list every unsupported claim (`REVIEWER_PROMPT`, `REA:...:111-114`), then a
single adversarial **adjudicator** rewrites the draft against those findings (`ADJUDICATOR_PROMPT`,
`REA:...:97-101`; `settle`, `REA:...:1164-1170`). This is REA's **Analyst → Reviewer →
Adjudicator** split. Note the ordering that matters: even with 2b disabled, 2a still runs — **the
code critic is the floor, the model critic is a ceiling.**

## 3. The deterministic engine boundary & CWE verification

The engine (`rea-engine`) is where "every claim verified" is cashed out. `Analysis` carries the
structured findings; `Vulnerability` findings each carry a **code-verified decompiled evidence
string** and a list of **CWE identifiers** (`REA:crates/rea-engine/src/forensics.rs:34-54`). CWE
assignment is a **static table**, pattern → CWE set, not a model guess — e.g. command-exec sinks
→ `CWE-78`, hardcoded creds → `CWE-798`, auth bypass → `CWE-287/CWE-306`, weak hash → `CWE-916`
(`REA:crates/rea-engine/src/forensics.rs:106-340`). The agent's `audit` tool
("Audit for vulnerable API sinks with CWE identifiers", `REA:crates/rea/src/agent.rs:141`) reads
*from* this deterministic table; it does not let the model mint CWE numbers. So "CWE verification"
means: the CWE is attached by code to an engine-recovered evidence string, and if the model cites
that evidence in prose, §2a re-checks the citation against the corpus. The number and its evidence
are both deterministic; the English around them is the only model-authored part.

## 4. The SLEIGH-in-Rust loader

`rea-sleigh` vendors **Ghidra's processor specifications** (`.slaspec`) for ~40 families —
ARM/AArch64/Thumb, MIPS32/64, PowerPC, RISC-V, SuperH, Sparc, Xtensa, AVR/Atmel, Z80, 6502,
Dalvik/JVM, BPF, and more (`REA:crates/rea-sleigh/processors/`, with `GHIDRA-LICENSE`/
`GHIDRA-NOTICE` carried alongside). They are **compiled into the binary at build time** via
`include_dir!("$CARGO_MANIFEST_DIR/processors")` (`REA:crates/rea-sleigh/src/embed.rs:35`) and
fed to a pure-Rust SLEIGH engine (`wazabin-sleigh` 0.1.14, `REA:crates/rea-sleigh/Cargo.toml:10`)
through an in-memory `SourceDb` (`REA:...:44-49`). `arch.rs` maps each named target to a spec
path + endianness + bit-width + sub-mode context — e.g. ARM Thumb selects `ARM/ARM7_le.slaspec`
with a Thumb context (`REA:crates/rea-sleigh/src/arch.rs:60-88`). The payoff: **non-x86
disassembly with no Ghidra, no Java, no install** — the specs ride inside the one static binary.
License note for the audit: the specs are Apache-2.0 (Ghidra); vendoring them into an MIT binary
is fine *with the retained NOTICE*, which REA keeps.

## 5. The md+pdf+json dossier export

One run writes a fixed family of artifacts beside the target, named `re-<stem>.*`
(`REA:crates/rea/src/paths.rs:73-106`):

- `re-<stem>.json` — machine-readable, rendered **straight from the engine** `Analysis`
  (`write_json` → `crate::json::report(analysis, bytes)`, `REA:crates/rea/src/cli.rs:1125-1126`).
  Deterministic; present even with no API key.
- `re-<stem>.asm` — the full disassembly listing, capped at 4M instructions
  (`REA:crates/rea/src/cli.rs:1147-1152`).
- `re-<stem>.{md,pdf}` — the **primary dossier**: the §2-verified narrative with the
  deterministic findings as an evidence appendix, rendered as a Typst-typeset md+pdf pair
  (`write_doc_pair`, `REA:crates/rea/src/cli.rs:946-950`; `render_pair`, `REA:...:926-929`).
- For firmware targets only: `re-<stem>-analysis.{md,pdf}` (executive threat dossier,
  `compose::dossier::build`, `REA:crates/rea/src/cli.rs:966-972`), `re-<stem>-re-record.{md,pdf}`
  (static RE record), plus extracted artifacts (`REA:crates/rea/src/cli.rs:1059-1062`).

The export's key property: **md and pdf are two renderings of one composed `Document`**
(`render_pair` splits a single build into both, `REA:...:926-929`), and **json is a parallel
rendering of the same `Analysis`** — not a post-hoc serialization of the prose. One source of
truth, three surfaces.

## 6. Extraction verdict — what Limpet lifts, and exactly where

Per the guide's §1.3 extraction targets, three adopts and one hold-the-line.

**ADOPT-1 — the citation-grounding code critic (the prize).** Limpet already has the right
*shape*: `limpet/critic.py:81` `deterministic_check` (structural gate, no model call) runs before
`limpet/critic.py:120` `review` (the model verdict), and a contested finding opens a ledger
obligation rather than being dropped (`limpet/critic.py:120-134`). What Limpet's deterministic
gate does **not** yet do is REA's move: check that **every address/string the narrative cites
actually appears in the engine's evidence corpus.** Design hook:

- Extend `deterministic_check` with a **citation-grounding pass** mirroring REA's
  `unverified`/`present` (`REA:crates/rea/src/agent.rs:1499-1519`): extract `0x…` addresses and
  quoted tokens from the finding's narrative, and fail the gate for any token absent from the
  workspace evidence corpus. This is pure code, so it stays on the correct side of
  `critic.py`'s own "a model call cannot un-fail a structural defect" rule
  (`limpet/critic.py:124-128`).
- Give the author REA's `VERIFY_ROUNDS = 3` reprove-or-remove budget before a hard redact, so a
  real-but-under-cited claim gets rescued rather than silently deleted.
- The corpus must be built like REA's: workspace dossier **plus every string the Limpet engine
  extracted** (`REA:crates/rea/src/agent.rs:1631-1634`), pinned per-workspace so the check is
  re-runnable evidence, not a live read.

**ADOPT-2 — the dossier (md+pdf+json) as a first-class workspace export.** Limpet has
`limpet/report-templates/{exec-summary,full-technical}`; REA shows the discipline to copy:
render all three surfaces from **one composed document over the engine `Analysis`**, never
serialize the prose into json (`REA:crates/rea/src/cli.rs:926-929,1125-1126`). json stays
deterministic and model-free; md/pdf carry the §2-verified narrative. Steal the fixed
`re-<stem>.*` naming so two runs are mechanically comparable.

**ADOPT-3 — the deterministic-offline floor.** REA ships a full report with **zero network
requests** when no key is present (`REA:README.md:32`). Make Limpet's engine path produce a
complete deterministic dossier with the LLM narrative absent, so a Limpet appliance with no model
route is still useful — and so the LLM is provably a narrator, not a dependency.

**HOLD THE LINE — keep unblob/binwalk over a bespoke extractor.** REA's firmware extraction is
hand-rolled pure Rust (uImage/SquashFS/partitions in `rea-engine`). That buys the single-binary
story but costs the long tail of formats unblob/binwalk already cover (and which the fleet's
firmware-extraction skill already mounts). Limpet's lane is HAL + appliance + trust-gate
breadth, not reimplementing extraction; keep unblob/binwalk. The one piece worth *considering*
later is REA's embedded-SLEIGH trick (§4) as a no-install non-x86 disassembly fallback for the
appliance image — but that is a roadmap note, not this ticket's adopt.

**Net differentiation read.** morluto/rea proved tool-breadth is commoditized; REx@Skill proved
evidence-methodology is portable; mytechnotalent/rea proves **the critic can be code**. Limpet's
edge remains HAL + appliance + trust-gate — but its *software* analysis loop should now carry a
code critic (ADOPT-1) as its floor, because that is the one quality bar a cloud-model competitor
cannot cheaply fake.
