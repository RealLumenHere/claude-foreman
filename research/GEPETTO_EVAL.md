# GEPETTO_EVAL — adopt Gepetto for LLM-powered Ghidra decompiler annotation?

_2026-10-14 · RESEARCH ONLY (nothing installed, no service touched)_
_Evidence grades: **[V]** verified this session (search result/URL shown below) · **[R]** read in a README/doc, not executed · **[U]** unverifiable_

## 0. VERDICT

**Do not port Gepetto to Ghidra. It is an IDA Pro-only plugin with no Ghidra support, and the
Ghidra ecosystem already has native LLM-annotation tools (e.g. RevEng.AI's `reai-ghidra`,
various `GhidraMCP`-style bridges) that solve the same problem without a port.** If LIMPET wants
LLM-assisted decompiler annotation in Ghidra, build a small Ghidra-script-side client that calls
out to the limpet/agent-code LiteLLM alias, rather than adapting Gepetto's IDA-coupled codebase.

## 1. What Gepetto actually is

- Upstream: `JusticeRage/Gepetto`, "IDA plugin which queries language models to speed up reverse
  engineering" **[V]** (github.com/JusticeRage/Gepetto, surfaced via fleet-search web).
- It is explicitly an **IDA Pro plugin**: install instructions say "Drop this script (`gepetto.py`,
  as well as the `gepetto/` folder) into your IDA plugins folder (`$IDAUSR/plugins`)" **[V]**.
- Cisco Talos's "Using LLMs as a reverse engineering sidekick" writeup (2025-07-31) uses Gepetto
  directly against IDA, not Ghidra **[V]** (blog.talosintelligence.com/using-llm-as-a-reverse-engineering-sidekick).
- Community plugin indexes (vmallet/ida-plugins, the tenable awesome-llm-cybersecurity-tools list,
  tdmathison's 2025 IDA plugin roundup) all catalog Gepetto strictly under IDA Pro plugins; none
  list a Ghidra variant **[V]**.
- No search result surfaced a `JusticeRage` Ghidra port, nor a community fork retargeting Gepetto
  at the Ghidra Java/Jython API. The closest analog named in the "awesome" lists is **G-3PO**
  ("like G-3PO for IDA Pro" — i.e. G-3PO is itself the Ghidra-side sibling tool, not Gepetto) **[V]**.

## 2. Ghidra Script API compatibility — the blocker

Gepetto is built entirely on IDA's Python API (`idaapi`, `idc`, `ida_hexrays` for the Hex-Rays
decompiler hooks it uses to get pseudocode and to rename variables/set comments). None of that
API exists in Ghidra:

- Ghidra's scripting surface is the Ghidra Java API (optionally via Jython/PyGhidra), built around
  `Program`, `DecompInterface`, `HighFunction`, `Function`, and `SymbolTable` — structurally
  different from IDA's `ida_hexrays.cfunc_t` tree that Gepetto walks to find rename/comment targets.
- Gepetto's core loop (grab decompiled pseudocode for the current function → send to the LLM →
  parse structured rename/comment suggestions → apply via `idc.set_cmt` / Hex-Rays variable
  renaming calls) would need every API call substituted, not just the imports — this is a rewrite
  of the plugin's integration layer, not a port.
- **Conclusion:** there is no drop-in compatibility path. "Evaluating Ghidra Script API
  compatibility" for Gepetto itself returns zero — the APIs don't overlap.

## 3. LiteLLM integration — this part is reusable in spirit, not in code

- Later Gepetto versions did generalize their model backend to support multiple providers
  (OpenAI, Azure, Ollama, and other local/remote endpoints) via its `config.ini` provider
  selection, which is conceptually the same shape as routing through limpet's LiteLLM
  `agent-code` alias (point a base_url + model name at the gateway). This part of the design is
  sound and worth imitating.
- But reusing *this* piece doesn't require Gepetto's codebase: limpet already has a LiteLLM
  client pattern used elsewhere in this repo (`limpet/registry.py`, `limpet/cli.py`) that a
  Ghidra-side script could call the same way — a plain HTTP POST to the gateway with the
  `agent-code` alias, same as any other LIMPET-routed tool. No Gepetto import is needed to get
  this benefit.

## 4. Annotation quality

No local or cited benchmark run was performed (would require a licensed IDA install, which isn't
present on the fleet, or a working Ghidra port, which doesn't exist). Quality claims in blog posts
(Talos) describe Gepetto's IDA output as "useful but needs review" — consistent with general
LLM-decompiler-annotation literature and not a basis for adoption on its own **[R]**.

## 5. Recommendation detail

- Skip adapting/porting Gepetto. The effort is a full rewrite against Ghidra's API, for a payoff
  (an LLM annotation loop) that's cheaper to build directly against Ghidra's `DecompInterface` +
  limpet's existing LiteLLM routing.
- If LIMPET wants this capability, scope it as a new small Ghidra script/extension (own ticket):
  decompile current function via `DecompInterface`, POST pseudocode to the `agent-code` LiteLLM
  alias, apply returned renames/comments via `HighFunction`/`SymbolTable` + `Function.setComment`.
  That is a much smaller, Ghidra-native build than adapting Gepetto's IDA-specific internals.
- Re-check before any future attempt: search again for a maintained Ghidra-native fork of Gepetto
  or JusticeRage activity — none existed as of this search (2026-10-14).

## 6. Evidence sources (fleet-search web, SearXNG-backed)

- `github.com/JusticeRage/Gepetto` — repo description, IDA-only install path.
- `blog.talosintelligence.com/using-llm-as-a-reverse-engineering-sidekick/` — Talos use of Gepetto against IDA.
- `github.com/vmallet/ida-plugins` — IDA plugin index listing Gepetto under IDA, not Ghidra.
- `github.com/tenable/awesome-llm-cybersecurity-tools` — "Gepetto: An IDA Pro plugin... (like G-3PO for IDA Pro)".
- `tdmathison.github.io/posts/IDAPro-plugins-2025/` — 2025 IDA plugin roundup listing Gepetto.
