# wq#3594 — skillhex proposer 402 fallback + no-fail-unit

## Problem
lumen-skillhex.service (timer, ~2d cadence) exited 1 on 2026-10-07 12:31Z:
`[skillhex] branch=incumbent parent=active evidence_items=0` then
`[skillhex] proposer failed; no cycle` 2s later (aq#1395). Root cause: the
proposer's only fallback (`deepseek-v4-flash`) was the *same* deepseek
account as the primary (`deepseek-v4-pro`), so the wq#3410 402 Insufficient
Balance took out both models — the fallback never actually fell back to a
different vendor. `/home/lumen/autonomy/agent5/skillhex.py` is outside the
wq#3586 worker's repo/dirs, so the proposer's real per-model error was never
logged to the journal, and the 2s runtime matched a fast 402.

## Fix (applied live at /home/lumen/autonomy/agent5/, mirrored here for review)
1. `dgm_v2.py:propose_prompt_mutation` now tries
   `(proposer, "cerebras-llama", "deepseek-v4-flash")` in order — cerebras-llama
   is on a different, funded account (same pattern as `a5_common.MODEL_CHAIN`
   and `ir_triage.py`'s `MODELS`/`NO_RETRY_HTTP`, PR #1675) — and prints the
   per-model failure (`returned no text` / `returned unusable JSON`) so the
   real error is visible in `journalctl -u lumen-skillhex.service`.
2. `skillhex.py:record_llm_unavailable()` files a deduped aq row
   (`aq add skillhex llm-unavailable ...` — aq upserts on open source+key)
   when no model answers.
3. `skillhex.py:main()` returns 0 (not a failing exit) when
   `DV2.propose_prompt_mutation` returns `None`, instead of letting the unit
   fail, after logging and recording the aq row.

## Verification
- Direct run `python3 /home/lumen/autonomy/agent5/skillhex.py --propose-only --n 1`
  on 2026-10-07: `deepseek-v4-pro returned no text` (still out of balance),
  fell through to `cerebras-llama`, which answered — hypothesis #25 recorded,
  exit 0. Confirms the fallback chain now reaches a genuinely different
  vendor/account instead of two deepseek models sharing one wallet.
- aq#1395 resolved (`python3 /home/lumen/bin/aq resolve 1395 ...`).

## Files (mirrors of the live, non-git /home/lumen/autonomy/agent5/ source)
- `skillhex.py` — see `record_llm_unavailable()` and the `return 0` in `main()`.
- `dgm_v2.py` — see the fallback model tuple in `propose_prompt_mutation()`.

Note: `/home/lumen/autonomy` is not a git repository; these are point-in-time
copies for review/citation. The deployed files are the source of truth.
