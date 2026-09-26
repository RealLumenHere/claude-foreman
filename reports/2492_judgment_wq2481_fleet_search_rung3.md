# wq#2492 — judgment on wq#2487's CONTRADICTED verdict against wq#2481

Judge: claude-opus-5@high, fresh instance, worktree `wq2492-a1`.
Both failure modes reproduced independently here with `--no-cache` before deciding — this
judgment rests on its own evidence, not on wq#2487's report.

## Verdict

**wq#2481 is NOT reopened. The defect lands as a P1 fast-follow with a corrected spec.**

Three findings drive that, in order of weight.

### 1. The implementation matches its spec; the spec is what's wrong

wq#2481's BUILD text specified rung 3 as "headless Chromium — render + dump DOM + hand to
the existing trafilatura path" and the escalation rule as "each rung tried ONLY on
403/429/503". `_fetch_chromium` (`/home/lumen/bin/fleet-search:468-487`) does exactly that,
and `_fetch_ladder` (`:513-533`) honours the escalation rule exactly. A DOM dump has no
status code of its own, so line 487 synthesises `200` from "subprocess exited 0 and stdout
is non-empty" — which is true of a rendered Cloudflare block page and a rendered DataDome
challenge just as much as of a real article.

The premise the spec inherited — that a WAF announces itself in the HTTP status — holds for
rungs 1-2 and is false for rung 3 by construction. Reopening wq#2481 re-dispatches a worker
against the same instructions that produce the same defect. That is the decisive argument:
a reopen here buys a second identical implementation, not a fix.

### 2. The false positive is the real harm, and it is independent of rung 4

Reproduced 2026-09-24, fresh, `cached:false`:

```
fleet-search --no-cache read https://www.crunchbase.com/organization/anthropic
→ ok:true, http:200, tier:3, bytes:5409
  content: "This website is using a security service to protect itself from online
            attacks... the Cloudflare Ray ID found at the bottom of this page."

fleet-search --no-cache read https://www.g2.com/products/anthropic-claude/reviews
→ ok:false, http:200, tier:3, bytes:2643
  error: "fetched OK but extracted no content/comments"
```

The g2 case is a clean failure — the caller knows it got nothing. The crunchbase case is
not: a caller receives `ok:true` with plausible English prose and no signal at all that it
is reading Cloudflare's copy instead of the page it asked for. A research digest built on
that is fabricated by plumbing. This is the wq#991 near-miss class the ladder was built to
close, reintroduced one rung further down.

The ticket's framing — "fast-follow given rung 4 is unreachable anyway" — understates it,
and I disagree with that half. The detection fix's value is **not** that it lets the ladder
reach rung 4. Its value is that it converts a silent false positive into an honest
`ok:false` naming the challenge, and that value is fully realised with rung 4 absent.
wq#2482's `blocked` status is therefore irrelevant to this fix's priority. P1, not P2.

### 3. The escalation wq#2487 believed it filed never landed

wq#2487's ISSUE-LOG entry states "`wq review reject 2481` filed, escalates to opus-5@high
per archetype-8 protocol". The live record contradicts that: wq#2481 is `status=done`,
`review_verdict=None`, `review_reason=None`, `reopened_count=0`, `attempt=1`, `fence=1`.

Cause: `wq review` is not a build-review verb. Per `wq review --help` it is "wq#662 ORCH:
accept or reject Frank's response on pending_review ticket" — it acts on a Frank-decision
gate, not on a completed build. Invoked against a `done` build ticket it left no trace, so
the contradiction has been living only in ISSUE-LOG and wq#2487's notes while the queue
still reads a clean pass. Logged as ISSUE-LOG #656 and annotated onto wq#2481 with
`wq note`, so the record carries it regardless of this judgment.

## On wq#2481's gate, honestly

The gate read: "g2.com... returns ok:true with non-empty content and a tier field;
crunchbase.com likewise; a 404 URL still fails fast at rung 1 without escalating; ok:false
on total failure names the last rung tried; fail-soft one-JSON-object envelope preserved."

Of its five clauses: the g2 clause **fails outright** (`ok:false`). The crunchbase clause
passes on its literal wording and fails on its intent — `ok:true` with non-empty content
that is the block page. The remaining three hold (404 fast-fail at tier 1, named-rung
`ok:false`, single-object envelope — all three confirmed by wq#2487 and by the two live runs
above, which returned well-formed single-object envelopes carrying `tier`).

So wq#2481's done notes over-claimed. Not reopening it is a judgment that a second attempt
against an unchanged spec is worse than a corrected fast-follow — it is not a finding that
the gate was met. The over-claim is recorded on the ticket rather than erased.

## Actions taken

| Action | Rationale |
|---|---|
| wq#2481 left `done`, annotated via `wq note` | Ladder shape, tiering, 404 fast-fail and envelope contract are sound and shipped; reopen replays the defective spec |
| **wq#2494** filed at pri 1, `needs repo(/home/lumen/bin)`, sonnet-5@high | Corrected spec: content-signature check, priority set by the false positive and not by rung 4 availability |
| ISSUE-LOG **#656** appended | Records both the spec-gap root cause and the `wq review` misuse that swallowed the escalation |

## Corrected spec handed to the fast-follow

Status-code-only escalation cannot see a rendered interstitial, so the signal has to come
from the body. After a rung returns, match the decoded body against a small set of
challenge signatures (Cloudflare "this website is using a security service" / "Ray ID",
DataDome, Akamai, PerimeterX, Turnstile / `cf-chl` markers) and, on a match, treat the
result as retry-eligible — synthesise `403` so `RETRY_STATUSES` escalation resumes — rather
than returning it as a terminal `200`.

Two constraints the fast-follow must not lose:

- **Check it in the ladder, not inside `_fetch_chromium`.** Rungs 1-2 can serve a
  200-status block page too (Cloudflare frequently serves its interstitial as 200 over
  ordinary HTTP); a check inside rung 3 only covers rung 3.
- **When the ladder exhausts on a matched challenge, the envelope must say so.** `ok:false`
  with an error naming the challenge and the last tier — not the generic "fetched OK but
  extracted no content/comments", which is what g2 already emits and which reads as an
  extraction bug rather than a bot wall. Callers need to tell "the site blocked us" from
  "the page was empty"; today they cannot.

Signature matching is a heuristic and will drift as WAF copy changes. That is acceptable
here: a missed signature degrades to exactly today's behaviour, and a false match degrades
to `ok:false`. Both failure directions are safer than the current fabricated `ok:true`.
