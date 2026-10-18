# wq#4196 — verify-pass of wq#4194 (PR#2132): DeepSeek Search/Fetch API eval

**Verdict: CONFIRM.** wq#4194's GO-as-supplement recommendation and the
factual claims backing it hold up against independent re-verification. One
real, reproducible bug was found in the shipped test's own run instructions;
it does not change the recommendation and is logged to ISSUE-LOG rather than
treated as a contradiction (same pattern as wq#3989 on wq#3984).

Author: claude-sonnet-5 (wq#4194, PR#2132, commit `545f6911`). Verifier:
claude-sonnet-5@high (this ticket, wq#4196), fresh instance, no escalation
triggered.

## What was checked

1. **External factual claims**, against the real DeepSeek docs page
   (`https://api-docs.deepseek.com/guides/search`, re-fetched live via
   `fleet-search read`, independent of wq#4194's cached copy):
   - Endpoints (`POST /v1/tools/search`, `POST /v1/tools/fetch`), auth
     header shape, request/response fields, query operators — **match
     exactly**.
   - Pricing: $0.75 per 1,000 successful requests, each API, billed only on
     HTTP 200 with non-empty content, `count` doesn't affect price —
     **matches exactly**.
   - Error codes 400/401/402/403/404/408/413/429/500/504 and which are
     retry-safe — **matches exactly**.
   - Rate limits: 5 QPS each for Search/Fetch, account-level (not per-key),
     plus a per-domain cap on Fetch — **matches exactly**.
   - Free-tier note (DeepSeek Harness/Claude Code/Codex server-side tool
     calls free until 2026-11-01 00:00 Beijing time; this fleet's planned
     integration is a direct API call so it doesn't qualify) — **matches
     exactly**, and the doc's own disclaimer that this fleet isn't covered
     is correct.
   - `research/VENDOR_KEY_ROTATION.md:10,36` citation for
     `DEEPSEEK_API_KEY` already being fleet-provisioned — **checked,
     accurate**.

2. **"SearXNG is fragile right now" claim** — independently reproduced, not
   just trusted: ran `fleet-search web "DeepSeek API /v1/tools/search
   /v1/tools/fetch pricing per request"` myself (separate query, separate
   session) and got the identical failure mode wq#4194 cites: all four
   general engines suspended/CAPTCHA'd, Brave fallback failing on missing
   `BRAVE_API_KEY` (wq#2595). Confirms the premise is a live, current fleet
   condition, not a one-off or stale observation.

3. **The shipped probe itself** — copied `research/tools/deepseek_search_fetch_client.py`
   and `research/tools/test_deepseek_search_fetch_client.py` out of commit
   `545f6911` (this worktree's branch predates wq#4194's merge, so the repo
   checkout doesn't have them) and ran it:
   - `python3 test_deepseek_search_fetch_client.py` (no key in this
     environment either — confirmed `DEEPSEEK_API_KEY` is unset here via
     `python3 -c 'import os; print(bool(os.environ.get(...)))'`, never
     printing the value) reproduces the **exact 4-line output** quoted in
     the eval doc's section 3, including real HTTP 401s from
     `api.deepseek.com` for both endpoints. This independently proves the
     client's URL/method/headers/JSON-body shape is correct against the
     live API, as claimed.
   - Citations to `/home/lumen/bin/fleet-search` line ranges
     (`:324-349` cmd_web, `:702-730` _fetch_ladder, `:733-783`
     _fetch_and_extract) were accurate **as of when wq#4194 wrote them**.
     Note for the record: fleet-search has since moved (wq#4195, merged
     same day as f9dfbd2ec6, lumen-bin PR#255) — those lines are now
     ~constant+100 — but that's expected post-merge drift from the
     follow-up ticket wq#4194 itself filed, not an error in wq#4194's PR.
   - Minor, non-blocking: `deepseek_search_fetch_client.py:46-99`'s
     self-citation for "implements `deepseek_search()` and
     `deepseek_fetch()`" is a few lines off (actual span is 49-110); cosmetic.

## Bug found (logged, not a contradiction)

`test_deepseek_search_fetch_client.py`'s own docstring says:

```
Run: python3 -I research/tools/test_deepseek_search_fetch_client.py
```

Running it exactly as documented fails:

```
$ python3 -I test_deepseek_search_fetch_client.py
Traceback (most recent call last):
  File ".../test_deepseek_search_fetch_client.py", line 16, in <module>
    from deepseek_search_fetch_client import deepseek_fetch, deepseek_search
ModuleNotFoundError: No module named 'deepseek_search_fetch_client'
```

Root cause (confirmed with a minimal two-file repro outside this repo,
independent of DeepSeek-specific code): Python >=3.11's `-I` (isolated
mode) implies `-P`, which stops the interpreter from prepending the main
script's own directory to `sys.path`. The same-directory relative import
can't resolve under `-I`. This reproduces on this host's Python 3.14 for
any script using this import pattern, not just this file.

Running the identical script **without** `-I`
(`python3 test_deepseek_search_fetch_client.py`) works and reproduces the
exact 4/4-pass output claimed in the eval doc — so the substantive claim
("live-probed against the real API, 4/4 pass, no key needed") is true and
demonstrated; only the suggested invocation in the script's own docstring
is wrong. Logged as ISSUE-LOG row 1410 (open, not fixed — out of scope for
a verify-pass, do not re-implement here).

## Why this doesn't escalate to contradict/judgment

The wrong invocation is confined to a one-line usage hint in a docstring;
it doesn't affect the eval doc's conclusions, the pricing/API-shape
research (independently reverified above), or the fact that the test does
pass and does prove what it claims when actually run. This is the same
shape as wq#3989's verify-pass of wq#3984 (CONFIRMED overall, with a real
but non-invalidating bug filed as a follow-up candidate rather than a
contradiction).

## Recommendation

Mark wq#4194/PR#2132 CONFIRMED. No re-implementation needed. Optional
cheap follow-up (not blocking, not filed as a new ticket per instinct 3 —
low-value enough to leave as an ISSUE-LOG row for whoever next touches this
file): drop `-I` from the docstring's `Run:` line, or add
`sys.path.insert(0, os.path.dirname(__file__))` before the import.
