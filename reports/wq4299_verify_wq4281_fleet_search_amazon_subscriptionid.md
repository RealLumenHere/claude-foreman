# wq#4299 — verify-pass of wq#4281 (fleet-search: strip Amazon SubscriptionId)

**Verdict: CONTRADICT.** Escalated to wq#4305 (claude-opus-5@high, judgment, deps=4281).

## What wq#4281 shipped

`/home/lumen/bin` commit `a3fef0eb4dd8` ("fix(fleet-search): strip Amazon
SubscriptionId/tag noise from result urls", `a7f051e92d` is the identical
feature-branch twin, landed to `main` as `a3fef0e`):

- Adds `AMAZON_NOISE_PARAMS`, `_clean_amazon_url()`, `_clean_amazon_noise()` to
  `fleet-search` (lines ~264–291).
- Wires those two functions into exactly 3 call sites: `searxng_search`
  (327–328), `brave_api_search` (353–354), `deepseek_search` (400–401) — the
  three backends `cmd_web`'s fallback chain uses.
- Adds `tests/test_wq4281_amazon_subscriptionid.py` (4 tests, fixture-built
  `SubscriptionId` value, never a bare `AKIA` literal).

Ticket AC (wq#4281, as recorded on the board): *"a committed fixture search
result containing a split-built SubscriptionId URL comes out with the param
removed."*

## What I checked

1. Extracted `fleet-search` and the test file at `a3fef0e` into an isolated
   tmp dir (`/tmp/fworker-wq4299/verify/`) rather than relying on the primary
   `/home/lumen/bin` tree, whose checked-out branch is stuck one commit behind
   `main` on `feat/wq4281-strip-amazon-subscriptionid` — a pre-existing,
   already-logged issue (ISSUE-LOG row 1441/wq#4281 landing note), not
   something to fix here.
2. Ran the committed test suite in isolation:
   `python3 -I -m pytest tests/test_wq4281_amazon_subscriptionid.py -v` →
   **4 passed**. AC as literally written is satisfied.
3. Tried to break it: `grep -n "_clean_amazon" fleet-search` → exactly 6
   matches, all inside the 3 web-backend functions. `fleet-search` has 7
   result-producing subcommands total (`web`, `reddit`, `github`, `hn`, `so`,
   plus `reddit --deep`'s comment-fetch sub-path, plus `read`). `reddit`
   reuses `searxng_search` so it inherits the cleaning for granted titles/
   URLs/snippets — but its `--deep` branch calls `_fetch_and_extract()` on
   each thread and assigns the raw fetched `comments`/`content` onto the
   result dict *after* the clean pass, uncleaned.
4. Built a fixture probe (`/tmp/fworker-wq4299/probe_hn_so_gap.py`): loaded
   `fleet-search@a3fef0e`, monkeypatched `_get_json` to return one HN hit and
   one StackOverflow item, each with `comment_text`/`body` containing
   `"https://www.amazon.com/dp/B07ABCDEFG?SubscriptionId=" + "AKI"+"A"+"Q"*16
   + "&tag=lumenresearch-20"` (split-built, never a literal, per the
   mask-tripwire-probe rule). Ran `cmd_hn(args)` and `cmd_so(args)` with
   `emit`/`cache_store` stubbed to capture output.

   Result:
   ```
   HN snippet scrubbed? False
   HN snippet: Got mine here: https://www.amazon.com/dp/B07ABCDEFG?SubscriptionId=<AKIA-shaped fixture, redacted>&tag=lumenresearch-20
   SO snippet scrubbed? False
   SO snippet: Buy it here: https://www.amazon.com/dp/B07ABCDEFG?SubscriptionId=<AKIA-shaped fixture, redacted>&tag=lumenresearch-20
   ```
   (The fixture value itself is never a literal in code — built at runtime as
   `"AKI" + "A" + "Q" * 16` — but printing the probe's rendered stdout here
   would re-paste the assembled string, so it's redacted in this report too.)

   Both `cmd_hn` and `cmd_so` pass the SubscriptionId value through verbatim
   into the emitted `snippet`, and `cache_store` runs on that same unscrubbed
   `out` list in both commands — so it would also land in the on-disk cache
   and prior-art file, which is exactly the "reaches cache, prior-art, or
   emit" surface the fix's own code comment claims to close.

## Why this matters

ISSUE-LOG row 1437 (the original wq#4279 incident write-up) frames the open
gap generically: *"fleet-search ... still passes Amazon SubscriptionId params
through, so future Amazon hits will re-fire with new hashes; stripping that
query param from result URLs in fleet-search would close the recurrence."*
The landed fix's own inline comment repeats that broad framing ("Strip the
noise before a result/snippet reaches cache, prior-art, or emit"). Hacker
News threads and StackOverflow answers routinely contain Amazon product/
affiliate links (hardware discussions, "where did you buy this" threads) —
this is not a contrived edge case, it's the same shape of hit that originally
triggered wq#4267/wq#4268's false AKIA burn, just arriving through a different
subcommand. The AC as narrowly written (searxng/brave/deepseek fixture) is
satisfied and the 4 tests are real and passing; the broader recurrence claim
is not.

## Disposition

- `wq#4299` closed with notes citing this report and wq#4305.
- `wq#4305` filed: `[judgment] wq#4281 ... hn/so/github/reddit-deep paths
  still leak the value`, model `claude-opus-5@high`, `deps=4281`, `--no-probe`
  (scope judgment call, not a deterministic probe).
- `kb/ISSUE-LOG.md` row 1444 appended with full issue/cause/solution.
- No re-implementation attempted here per archetype 8.
