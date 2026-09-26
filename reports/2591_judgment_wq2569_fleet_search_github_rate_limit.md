# wq#2591 — judgment on wq#2587's CONTRADICTED verdict against wq#2569 (PR#131)

Judge: claude-opus-5@high, fresh instance, worktree `wq2591-a1`.
The defect and both behaviours it must not break were reproduced here first, against a
copy of `fleet-search` at `035778c` (PR#131's only commit) under
`/tmp/fworker-wq2591/probe/` — no writes to `/home/lumen/bin`. This judgment rests on
that run, not on wq#2587's report.

## Verdict

**wq#2569 is NOT reopened. The defect lands as a P1 fast-follow (wq#2598) with a
corrected spec, and PR#131 should merge as-is rather than being held for it.**

Three findings drive that.

### 1. The reopen path re-dispatches the exact sentence that produced the defect

wq#2569's AC(2) reads: "when unauthenticated or rate-limited (403/429,
X-RateLimit-Remaining=0), return ok:false with reset time instead of empty ok:true".
It names `X-RateLimit-Remaining=0` in an unqualified trigger list, and `cmd_github`
implemented that list literally (`fleet-search:398` at `035778c`). The word "empty"
appears only in the clause describing what is being replaced, not in the trigger.

This matters mechanically, not just rhetorically: `wq claim` prints only the `detail`
field to the claiming worker (`/home/lumen/bin/wq:1752` selects
`id,pri,title,detail,deps,subsystems`; `:1809` prints title, deps and detail — notes are
never shown). A release-based reopen —
`wq release` is the only reopen mechanic the CLI has, there is no `reopen` verb —
therefore hands attempt 2 the same detail text, with the correction invisible. To reopen
safely I would have to rewrite `detail` into a corrected spec anyway, which is a
corrected spec attached to an old id, at the cost of retracting a `done` record whose
substantive deliverable is confirmed correct.

The spec conflated a header fact ("this budget is now exhausted") with a body fact
("this response carries nothing usable"). That is the wq#2481/wq#2492 shape, and the
same conclusion follows.

### 2. The defect is narrow, honest, and strictly less harmful than the status quo it replaces

Reproduced 2026-09-25 (`/tmp/fworker-wq2591/probe/tests/test_wq2591_judgment_probe.py`,
4 passed), input `(200, {"items": [<one real repo>], "total_count": 1}, None,
{"remaining": "0", "reset": ...})` through `fs.cmd_github`:

```
case1 exit=2 {"source":"github-repos","ok":false,...,"rate_limit_reset":"2025-01-01T00:00:00Z",
 "error":"github 200 — rate limited (X-RateLimit-Remaining=0, resets 2025-01-01T00:00:00Z)"}
```

The real item is dropped and no `results` key is emitted. Confirmed. But compare bases:
`/home/lumen/bin/fleet-search` on `main` (`a562378`) contains zero occurrences of
`_resolve_github_token` or `X-RateLimit-Remaining` — today every fworker searches GitHub
unauthenticated on the shared 60/hr IP budget and exhaustion returns `ok:true` with an
empty list (wq#2564's silent false negative). PR#131 replaces a silent wrong answer on
*every* exhausted call with a correct answer on all calls but the last of a window, where
it returns an honest `ok:false` naming the cause. Holding PR#131 to serialise a two-line
rework keeps the silent failure in the tree longer than shipping it does.

So the priority argument runs the other way from wq#2492's: there the fix converted a
false positive into an honest failure and was urgent on its own; here the fast-follow
converts an honest-but-wrong failure into a correct success. Still P1 — a discarded search
result is a wasted worker turn and the Search API's 30/min cap makes it routine, not rare —
but not a reason to block the merge.

### 3. A fast-follow must not branch from `main` before PR#131 merges

This is the constraint that decides the fast-follow's shape. `035778c` is not an ancestor
of `main` (`git merge-base --is-ancestor` → false), and `main` has moved two commits past
PR#131's base. A worker that branches from `main` today finds no rate-limit guard and no
`_resolve_github_token` to correct — it would re-implement wq#2569 from scratch or
conflict with PR#131. Hence `deps=2569` plus an explicit, checkable precondition in the
fast-follow's detail: if `grep -c X-RateLimit-Remaining /home/lumen/bin/fleet-search`
returns 0, PR#131 has not merged yet and the ticket must block rather than proceed.

## On wq#2569's gate, honestly

Its AC(2) passes on the wording and fails on the intent for the populated-response case;
AC(1) (credential-helper token resolution, never env/URL) and AC(3) (tests) hold, and
wq#2587 independently re-ran the 10/10 suite. The done notes' claim "returns ok:false ...
on 403/429 or X-RateLimit-Remaining=0 (incl. 200-with-empty-items)" is literally accurate
and materially incomplete — it does not say that the same guard fires on
200-with-populated-items. Not reopening is a judgment that a corrected fast-follow beats a
second attempt against unchanged spec text; it is not a finding that the gate was fully
met. The over-claim is annotated onto wq#2569 rather than erased.

The test gap is the reusable lesson: `test_200_with_remaining_zero_returns_ok_false_not_empty_ok_true`
asserts the empty-items case only, so the suite could not see the over-broad trigger. A
guard that keys on a header must be tested against both a populated and an empty body.

## Corrected spec handed to wq#2596

Gate the header-derived trigger on the body, keep the status-derived trigger absolute
(`cmd_github`, replacing `035778c`'s `fleet-search:398`):

```python
exhausted = (rl or {}).get("remaining") == "0"
if status in (403, 429) or (exhausted and not (data or {}).get("items")):
    emit(envelope(kind, False, error=..., ...))
```

Four constraints the fast-follow must not lose:

- **Gate on `items`, not `total_count`.** `out` is built from `data["items"]`
  (`035778c:fleet-search:409`), so `items` is exactly what a caller loses. A 200 carrying
  `total_count > 0` with `items: []` still returns nothing usable, and the `items` test
  catches it; a `total_count` test would wave it through as an empty `ok:true` — the
  original wq#2564 symptom, reintroduced.
- **Keep 403/429 unconditional.** Those responses carry no items; `exhausted` must not
  weaken them, and neither may the `status or 200` error wording that
  `test_403_rate_limited_returns_ok_false_with_reset` asserts.
- **Surface the exhausted budget on the success path as fields, not as a failure.** The
  `ok:true` envelope (`035778c:fleet-search:431-433`) carries no rate-limit information at
  all, so a caller that just spent the last unit of its budget cannot tell. Add
  `rate_limit_remaining` (as received) and `rate_limit_reset` (the already-computed
  `reset_at`) to it. This is the "still surface it as a warning" half of the judgment
  question, answered yes — as envelope fields on a successful call.
- **Test both bodies against the same header.** Required: (a) 200 + one real item +
  `remaining:"0"` → exit 0, `ok:true`, one result, `rate_limit_remaining` present;
  (b) 200 + `items:[]` + `remaining:"0"` → exit 2, `ok:false`, "rate limited" in error
  (the existing wq#2564 test, kept green); (c) 403 + `remaining:"0"` → unchanged;
  (d) `rl` absent/`None` + populated items → `ok:true`, no crash. (a) must fail against
  `035778c` before the change.

The probe that reproduced this also evaluates the proposed condition's truth table over
all seven of those combinations standalone
(`/tmp/fworker-wq2591/probe/tests/test_wq2591_judgment_probe.py:79-93`, passing), so the
spec is known to fix case (a) without disturbing (b), (c) or (d).

## Actions taken

| Action | Rationale |
|---|---|
| wq#2569 left `done`, PR#131 left open and unedited, annotated via `wq note` | Token-broker deliverable is confirmed correct; a reopen re-dispatches the defective AC(2) sentence, since `wq claim` shows only `detail` |
| **wq#2598** filed, pri 1, `deps=2569`, `needs repo(/home/lumen/bin)`, sonnet-5@high | Corrected spec above; `needs` tag set so the worker gets a lumen-bin worktree instead of repeating ISSUE-LOG #708's cross-repo quirk |
| ISSUE-LOG **#714** appended | Records the header-vs-body conflation, the single-body test gap that hid it, and the unmerged-base constraint |
| PR#131 explicitly not held | Its failure mode is honest; `main`'s is silent. Blocking the merge maximises the time the silent one is live |
