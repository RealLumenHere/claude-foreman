## What

A binary-content sanitization layer for LIMPET (`limpet/sanitize.py`), applied on ingest
into the agent-facing workspace index. It sits between attacker-controlled binary content
and the LLM agent's context and does three things the ticket names:

1. **Strip/escape control + invisible chars** — C0/C1 controls, bidi overrides,
   zero-width/BOM, line/para separators, and the invisible Unicode *tag* block
   (U+E0000–E007F, an ASCII-smuggling channel) all become *visible* escapes. Nothing
   invisible survives and a value collapses to one logical line, so it cannot forge
   turn/line structure in the prompt.
2. **Limit length** — capped at `DEFAULT_MAX_LEN` with a visible `⟦+N⟧` marker.
3. **Escape prompt-like patterns** — chat/instruction delimiters (`<|im_start|>`,
   `[INST]`, `<<SYS>>`, `</s>`, role headers) and instruction-override phrasing are
   replaced with a legible, non-actionable `⟦scrubbed:<label>⟧` tag — defanged, not
   deleted, so the string stays searchable and an operator still sees what was there.

## Why

Research found target binaries can carry crafted strings that hijack the LLM agent. Those
strings, plus symbol and function names, are extracted by RE tools and surfaced to the
agent via `index.sqlite` — a classic indirect prompt-injection channel. This layer is a
v1 hard requirement alongside the energize-confirm gate. Prior art: OWASP LLM01 cheat
sheet + tldrsec/prompt-injection-defenses (fleet-search web, 2026-10-17).

## How

`sanitize.py` is self-contained (stdlib only), mirroring `workspace_report.mask_text`'s
posture. `workspace_index.build_index` runs `_clean` over every binary-derived free-text
field (string value/encoding, symbol/function name, xref kind) on the way into the
**derived** `index.sqlite`. The canonical raw evidence under `evidence/re-exports/*.json`
is never rewritten, so forensic fidelity and agent safety stay separate layers and an
operator can always drop to the raw bytes deliberately. `sanitize()` returns a `Sanitized`
dataclass with telemetry; `sanitize_text()` is the string-only form. Decision recorded in
`limpet/DESIGN-DECISIONS.md`.

## Testing

Falsifiable by construction (a no-op sanitizer fails them):
- `test_sanitize.py` (14 cases): control/C1/bidi/zero-width/tag-block escaping, length
  cap, each injection-pattern class, benign text untouched, None/non-str, combined
  payload has no actionable residue.
- `test_workspace_index.py::test_binary_content_sanitized_into_index`: a committed,
  rev-pinned malicious RE export (`tests/fixtures/re-export-injection.json`) is ingested;
  stored values are asserted free of live delimiters/newlines while the benign string
  stays FTS-searchable.
- 53 passed across sanitize + index + console + report suites.

## Risk / rollback

Low. Only the derived, rebuildable index and rendered surfaces see sanitized text; raw
evidence is untouched, so the change is reversible by rebuilding from evidence with the
layer removed. Sanitization is lossy for genuinely malicious strings (by design) — an
operator recovers the exact bytes from `evidence/re-exports/`. Benign content is left
untouched (tested). Rollback: revert the commit; `index.sqlite` is regenerated on next
`build_index`.

Note: `test_limpet_cli.py` has a pre-existing collection error (`registry` package shadows
a legacy `registry` module) unrelated to this change.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
