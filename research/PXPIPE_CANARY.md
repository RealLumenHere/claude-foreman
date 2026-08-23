# foreman-tok: pxpipe canary — vision-token context rendering (wq#634)

Session: wq#634, 2026-08-23. Research-only, no code shipped, no infra touched.
Dep: wq#629 (TOKEN_BASELINE.md) — done 2026-08-15.

## Executive summary

pxpipe renders dense text (system prompts, tool schemas, old conversation history) as PNG
images, exploiting the fact that vision-token cost is fixed by pixel area, not character
count. On dense content (~1 char/text-token) this yields ~3.1 chars/image-token — a real
~68% input-token reduction. On Fable 5, gist-level recall from imaged pages is 100/100
(controlled eval, zero loss) and SWE-bench Lite parity is 10/10 at half the token cost.

**Verdict for Lumen foreman: NOT RECOMMENDED for adoption. Three independent blockers.**

1. **Subscription limit accounting is unverified and likely unfavorable.** See §2.
2. **Exact-string recall is unsafe.** Our workers do git ops on commit hashes, file paths,
   and version IDs. pxpipe's own findings show 13% silent misread on exact hex strings
   (Fable 5, n=15) and 0/15 on Opus. Failure mode is silent confabulation — wrong but
   confident values, not errors. This is load-bearing for foreman workers.
3. **OAuth passthrough is unverified.** pxpipe works via `ANTHROPIC_BASE_URL` redirect to a
   local proxy. Whether the Max subscription OAuth flow survives this redirect is untested.
   The ticket flags this as a hard risk.

## 1. How vision tokens work (Anthropic, current as of 2026-08-23)

### Token formula

Per platform.claude.com/docs/en/build-with-claude/vision (fetched this session):

    visual_tokens = ⌈width / 28⌉ × ⌈height / 28⌉

This is a 28×28 patch grid, NOT the older `(w×h)/750` approximation that third-party
calculators still cite. The older formula was a rough estimate; the patch formula is the
actual tokenizer.

### Resolution tiers (determines max image cost)

| Tier            | Models                     | Max long edge | Max visual tokens |
|-----------------|----------------------------|---------------|-------------------|
| High-resolution | Claude 4.7+ (incl Fable 5) | 2576 px       | 4784              |
| Standard        | All older models           | 1568 px       | 1568              |

Images larger than the tier's limits are downscaled preserving aspect ratio before
tokenization. For the high-res tier, a full 2576×1449 4K image caps at 4784 tokens.

### pxpipe's render target

pxpipe renders to 1568×728 (~1.15 MP, fits within both tiers without downscaling). At
the patch formula: ⌈1568/28⌉ × ⌈728/28⌉ = 56 × 26 = 1,456 visual tokens per page.
The repo cites ~2,668 tokens (from the old w×h/750 formula: 1568×1276/750 ≈ 2,668),
but their actual measured per-API-call tokens on Fable 5 are ~1,104 per page. The
discrepancy is because the patch formula gives lower counts than the old approximation
for images within the standard tier.

### Compression arithmetic

Dense content (code, JSON, tool output, hashes) tokenizes at ~1 char/text-token.
pxpipe packs ~3,400-4,400 chars per page at the readable 22pt density.

At ~1,104 image tokens per page carrying ~3,400 chars of dense content:
- Text cost: ~3,400 tokens (at 1 char/token)
- Image cost: ~1,104 tokens
- Compression: 3.08× (67.5% fewer tokens)

This checks out against pxpipe's measured 68% figure and the repo's "3.1 chars per
image-token" claim. The math is real for per-request token count.

## 2. The subscription-limit question (critical for Lumen)

### What we know

Anthropic's Max subscription meters usage by tokens against a 5-hour rolling session
limit and a weekly cap. All surfaces (claude.ai, Claude Code, Cowork, `-p` sessions)
share one pool. Anthropic publishes no token counts, only relative multipliers (Max
20x = 20× Pro per session).

### What we don't know (and can't verify without a controlled test)

**Whether vision tokens are weighted the same as text tokens against the subscription
limit.** This is the make-or-break question. Three scenarios:

1. **Vision tokens count 1:1 with text tokens.** pxpipe saves ~68% of limit burn.
   This is the scenario pxpipe's economics depend on.
2. **Vision tokens are weighted higher** (e.g., by API-equivalent dollar cost:
   image tokens on Sonnet are priced the same as text input tokens per the API rate
   card, but Opus/Fable image tokens may differ). If there's a model-specific weight,
   the savings shrink or vanish.
3. **Anthropic's metering detects proxy-rendered text-as-image and penalizes or
   blocks it.** This is speculative but not impossible — Anthropic has previously
   blocked third-party OAuth tokens (April 2026) and could adjust metering.

**No public documentation distinguishes vision-token vs text-token consumption against
subscription limits.** The usage endpoint (`/api/oauth/usage`) that `foreman-usaged`
polls reports session/weekly progress bars, not per-token-type breakdowns. A controlled
A/B (same workload, pxpipe ON vs OFF, measuring `/api/oauth/usage` delta) would be
needed to answer this empirically. That A/B is exactly what the ticket's scope section
calls for, but it burns real subscription limit to run.

### API rate card (for reference, not directly applicable to Max)

Per-token API prices treat image tokens identically to text input tokens — you pay
the same $/Mtok for vision tokens as for text input tokens. This is consistent with
scenario (1), but API pricing ≠ subscription metering.

## 3. Exact-string recall: the foreman-specific blocker

### pxpipe's own measurements (FINDINGS.md, fetched this session)

| Model    | Test                     | Result    | Detail                                |
|----------|--------------------------|-----------|---------------------------------------|
| Opus 4.5 | 12-char hex verbatim     | 0/15      | returned empty, hard zero             |
| Opus 4.8 | 12-char hex verbatim     | 0/15      | same                                  |
| Fable 5  | 12-char hex (n=15, expanded) | 13/15 | 2 misses: single-glyph silent confab  |
| Fable 5  | 12-char hex (n=4 easy)   | 3/4       | 1 miss: silent single-char error      |
| Fable 5  | gist recall (100 probes) | 100/100   | zero loss at gist tier                |
| All      | SWE-bench Lite (n=10)    | 10/10     | parity, half cost                     |

### What foreman workers actually do with exact strings

From TOKEN_BASELINE.md and the foreman build plan: workers run `git` operations
(commit, diff, checkout by SHA), reference file paths, manipulate wq ticket IDs,
write version strings, produce PR titles. Every one of these is an exact-string
operation where a single-character error can be a wrong commit, wrong file, or a
git command that silently succeeds on the wrong ref.

The 13/15 Fable exact-recall rate means ~13% of exact hex strings get silently
corrupted. For a foreman worker doing 10+ git-hash-bearing operations per session,
the expected number of silent hash corruption events per session is >1. This is not
a tolerable rate for unattended autonomous workers — especially given that the
failure mode is silent (no error, just a confident wrong value).

pxpipe's architecture mitigates this by keeping recent turns as text (only old
history gets imaged), and the repo documents a "verbatim-risk guard" that keeps
blocks carrying IDs/hashes/secrets as text. But:
- The guard is not yet built (documented as "open item" in FINDINGS.md).
- Even with a perfect guard, any git hash, path, or ID that ages past the text-tail
  boundary into the imaged region becomes silently unreliable.
- Our workers routinely reference hashes from earlier in the same session (the
  `--resume` path re-primes accumulated context).

## 4. OAuth passthrough

pxpipe works by setting `ANTHROPIC_BASE_URL=http://127.0.0.1:47821`. The claude CLI
on Max sessions authenticates via OAuth (not API key). Whether the OAuth flow
survives a redirect through a local proxy is unverified. The repo's own usage is
on API-key accounts.

If OAuth passthrough fails, the entire mechanism is DOA on our stack — and there is
no way to test this without burning subscription limit budget on a real session.

## 5. What pxpipe IS good for (honest assessment)

For API-key users paying per-token on dense workloads (code review, log analysis,
document-heavy sessions), pxpipe is a legitimate 59-70% cost reduction tool with
gist-level recall parity on Fable 5. The repo is well-engineered (MIT, v0.7.x,
6.8k stars, active maintenance), the eval methodology is rigorous and self-critical,
and the measurements are reproducible.

It is specifically unsuited for:
- Subscription limit optimization (unverified whether it helps)
- Exact-string-dependent autonomous agents (silent confabulation risk)
- OAuth-only stacks (passthrough unverified)

All three describe us.

## 6. Actionable tickets seeded

### wq candidates (for Frank's triage)

1. **[foreman-tok][research] subscription-limit token-type weighting probe** —
   Controlled test: send identical workload to `/api/oauth/usage`, once with an image
   block, once without. Measure whether vision tokens burn session limit at the same
   rate as text tokens. ~2 minutes of real limit budget. Answers the §2 question.
   BLOCKING: all pxpipe-class optimizations, and any future vision-token-as-context
   strategy (e.g., screenshot-based code review, image-embedded documentation).

2. **[foreman-tok][research] OAuth passthrough probe (ANTHROPIC_BASE_URL redirect)** —
   Verify that `claude -p` on Max OAuth survives `ANTHROPIC_BASE_URL=localhost:PORT`.
   Prerequisite for pxpipe AND for any future local-proxy pattern (Headroom, custom
   middleware). ~1 minute of real limit budget, read-only probe.

### NOT seeded (rationale)

- No canary deployment ticket: all three blockers must clear first.
- No wiring-proposal ticket: gate file mechanism means Frank decides after blockers.
- No code changes: research-only scope.

## 7. Relation to existing foreman-tok work

| Ticket | Status | Relation |
|--------|--------|----------|
| wq#629 | done   | Token baseline — the instrument this research diffs against |
| wq#630 | done   | PREFIX_STABILITY — prompt-cache chain stability |
| wq#631 | done   | INSTRUCTION_DIET — prompt diet, ~5-8% token reduction |
| wq#632 | open   | Effort tiering — independent of pxpipe |
| wq#633 | open   | Headroom — same ANTHROPIC_BASE_URL class, same OAuth risk |
| wq#634 | THIS   | pxpipe canary |

## 8. Conclusion

pxpipe's token-count reduction is real and well-measured. Its value proposition is
clear for API-key per-token users on dense workloads. For Lumen's foreman on Max 20x
subscription with autonomous workers doing exact-string git operations over OAuth:
three independent blockers prevent adoption. Two are probeable (subscription metering,
OAuth passthrough) at small limit cost; one (exact-string safety) is architectural
and only partially mitigatable by the not-yet-built verbatim-risk guard.

Recommended path: probe §6 ticket 1 (token-type weighting). If vision tokens DON'T
reduce limit burn, the entire approach class is dead for subscription users regardless
of recall quality — and that closes pxpipe, Headroom image-mode, and any future
vision-as-context strategy in one measurement. If they DO reduce burn, probe ticket 2
(OAuth passthrough), then reassess exact-string risk with the verbatim-risk guard once
it ships upstream.
