## What
Adds a flag-gated meta-review synthesis stage to the hallucination guard (proposal f75013a36dcd, doi:10.1002/sres.70163): after the primary judge + citation scores, one schema-constrained LLM call synthesizes the per-dimension outputs into a verdict whose `reject_reason` distinguishes `stale-premise` from `bad-idea`.

## Why
Proposals are mostly rejected at build for stale/duplicate premises rather than for being bad ideas — the reject signal is noisy, not absent. An explicit classification lets reject analytics separate the two without a new reviewer fleet or service.

## How
`meta_review_synthesis()` in `minerva_verify.py`, called from `verify()` only when `META_REVIEW_ENABLED=1`. Model = `META_REVIEW_MODEL` (default: existing `JUDGE_MODEL`), JSON mode, `reasoning_effort=disable`, output schema-validated. Advisory only: it never flips `passed`; on a primary reject it appends `meta:<class>` to the logged reason. Fail-open (returns None on any error) since the layer is advisory, unlike the fail-closed primary judge.

## Testing
`python3 test_meta_review.py` (offline, network/DB mocked): schema validation and sanitization, fail-open on HTTP/exception/no-key, wiring on/off, and the advisory-only invariant (meta accept cannot flip a primary reject). Import-health with flag unset: module loads, flag defaults off.

## Risk / rollback
Off by default (`META_REVIEW_ENABLED=0`); with the flag unset the existing judge/cross-agreement path is byte-identical in behavior. Rollback = unset the flag.

Refs: wq#1927

🤖 Generated with [Claude Code](https://claude.com/claude-code)
