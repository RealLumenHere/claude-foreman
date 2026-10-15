## What

New `docs/agent-security-spec.md` (wq#4014): a content-sanitization layer at the MCP meta-server result boundary so attacker-controlled target text (strings, decompilation, disassembly) reaches the analyzing agent as fenced, labelled data — never as live instructions. Cross-linked from `agent-chat-spec.md` §7 as the upstream counterpart to UI inert-render. Design-only; nothing installed or registered.

## Why

A target binary's strings are hostile input aimed at the LLM, not just a human analyst: a crafted sample can embed `ignore previous instructions` or a forged `tool_call:` that the agent core reads verbatim during analysis (indirect prompt injection). Limpet had no layer between tool output and model context. REx@Skill solves this with `quarantine/` + `sanitize.py`; Check Point 2025 RE research demonstrated the attack class. This spec gives Limpet the equivalent, and unblocks agent-chat implementation and any autonomous analysis workflow.

## How

- Quarantine of raw tool output under `evidence/quarantine/` (byte-exact, 0600, in the signed bundle); the agent receives only a sanitized result plus a re-sanitizing quarantine handle — no raw-bytes path from the agent side.
- `sanitize.py` four-pass pure function — truncate oversized strings, escape special/control tokens, fence known injection/forged-protocol patterns, flag suspicious content. Defang-don't-decide: injection strings stay analyzable (they are an RE finding) but inert.
- TOML-driven, updatable rules that can only tighten, never loosen (fail-closed; a target cannot ship a relaxed ruleset).
- Per-workspace sanitization evidence chain recording raw hash, rules version, per-action tallies — "what the tool emitted" vs "what the agent saw" is auditable on replay.
- Integration is non-disableable: the layer wraps the meta-server proxy, so a `community` plugin cannot opt its output out.

## Testing

Design-only ticket; no code lands here. The spec defines a fail-able acceptance probe for the provisioning ticket (§7): a hash-pinned fixture binary whose strings carry a live `tool_call:` and `ignore previous instructions`, asserting the agent-facing text fences them, no un-fenced `tool_call:` survives, a `critical` flag is logged, and the raw original is byte-exact in quarantine. Evidence gate (AC1) verified: `docs/agent-security-spec.md:67-137` is in this branch's diff.

## Risk / rollback

Documentation only — no runtime behavior changes. Rollback is reverting the two doc files. Open item carried into the provisioning ticket (§8): exact-quote grounding of the Check Point 2025 passage and REx@Skill `sanitize.py` behavior was deferred — fleet-search (SearXNG) was down (all engines blocked, wq#2595) and the sandboxed WebSearch tool is ungranted on this build worker, so the spec is grounded on the ticket's named references and the REA server's existing `untrusted_content` provenance tag rather than fabricated quotes.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
