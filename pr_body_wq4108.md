## What

`limpet/binary_scrub.py` — the control-char + length-limit half of LIMPET's
prompt-injection defense — applied on ingest into the agent-facing workspace index.

- `scrub(value)` escapes every unsafe char to a *visible* form (C0/C1 controls, bidi
  overrides, zero-width/BOM, line/para separators, and the invisible Unicode *tag* block
  U+E0000–E007F — an ASCII-smuggling channel) and caps length with a visible `...[+N]`
  marker. Nothing invisible survives; the value collapses to one logical line.
- `scrub_for_context(value)` composes that with phrasing redaction, **reusing** wq#4091's
  landed `injection` classifier (no second pattern list): a value matching a known
  injection shape is replaced with `injection.REDACTION_TOKEN`, then control-escaped and
  length-capped.
- `workspace_index._clean` runs `scrub_text` over every binary-derived free-text field
  (string value/encoding, symbol/function name, xref kind) on the way into the derived
  `index.sqlite`.

## Why

Research found target binaries can carry crafted strings that hijack the LLM agent. Those
strings and symbol/function names are extracted by RE tools and surfaced to the agent via
`index.sqlite` — an indirect prompt-injection channel. wq#4091 already shipped the
injection *phrasing* classifier and a workspace gate, but left two of wq#4108's three
named requirements unaddressed: **stripping control chars** and **limiting string
length**. A string can also corrupt context with no injection phrasing at all (terminal
escapes, invisible bidi/tag-block smuggling, megabyte floods). This PR is that missing
half, wired into the binary-content→LLM boundary.

## How

`binary_scrub.py` is pure, stdlib-only, and mirrors `injection.py`'s posture so the two
feel like one system. It deliberately reuses `injection.is_injection`/`REDACTION_TOKEN`
rather than duplicating phrasing patterns (one source of truth). `_clean` scrubs values
in memory into the derived, rebuildable index; the canonical raw evidence under
`evidence/re-exports/` is never rewritten, so forensic fidelity and agent safety stay
separate layers. The transform is idempotent whether or not the wq#4091 gate ran first
(the redaction token carries no control chars). Decision recorded in
`limpet/DESIGN-DECISIONS.md`. Prior art: OWASP LLM01 + tldrsec/prompt-injection-defenses
(fleet-search web, 2026-10-17).

## Testing

Falsifiable by construction (a no-op scrub fails them):
- `test_binary_scrub.py` (10 cases): control/C1/bidi/zero-width/tag-block escaping, length
  cap, benign text untouched, `scrub` does NOT classify phrasing, `scrub_for_context`
  redacts phrasing + escapes controls, idempotency on the redaction token, None/non-str.
- `test_workspace_index.py::test_binary_content_scrubbed_into_index`: a committed,
  rev-pinned malicious RE export (`tests/fixtures/re-export-injection.json`) is ingested;
  stored values are asserted free of phrasing/control bytes/newlines while the benign
  string stays intact.
- 80 passed across binary_scrub + index + critic + sanitize + injection + console + report
  suites; wq#4091's own tests still green.

## Risk / rollback

Low. Only the derived, rebuildable index sees scrubbed text; raw evidence is untouched, so
the change is reversible by rebuilding from evidence. Scrubbing is lossy for genuinely
malicious strings (by design) — an operator recovers exact bytes from
`evidence/re-exports/`. Benign content is left untouched (tested). Rollback: revert the
commit; `index.sqlite` regenerates on next `build_index`.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
