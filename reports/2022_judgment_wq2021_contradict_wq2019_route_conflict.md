# wq#2022 — judgment on wq#2021's CONTRADICT of wq#2019 / PR #641

**Verdict: CONTRADICT UPHELD. Re-verdict on wq#2019's shipped diff:
CORRECT-WITH-DEFECT, now fixed.**

wq#2019's deliverable stands: the co-occurrence-anywhere gate really was killing
technique proposals, and the add-intent verb requirement really does clear the four
shapes its report cited. What wq#2021 caught is that the replacement is a narrower
proximity heuristic of the same class, and that narrowing it opened a specific new
way in — a proposal that explains it is *not* a route add supplies the very verb the
gate scans for, sitting right next to the keyword and route name.

## The claim, checked live

Re-ran wq#2021's repro against the shipped module in this worktree (same route list
the shipped probe uses, `title + proposed_change` call shape per
`ops/wq2019-proposal-reviewer-fix/minerva_proposal.py:482-483`), before touching
anything:

```
>>> route_conflict("Add semantic caching to the LiteLLM gateway route table; this "
...     "doesn't add a new model, just wraps existing route bge-m3 with a cache layer.")
(True, 'bge-m3')
>>> route_conflict("Introduce a small wrapper around the LiteLLM gateway route logic "
...     "so requests to qwen36-local get retried with backoff.")
(False, None)
```

Byte-identical to wq#2021's reported output, including the control case that does
*not* trigger. The mechanism is exactly as described: the first `Add` (position 0)
sees a keyword but no route name in its window; the second `add`, inside the
disclaimer, sees `…gateway route table; this doesn't add a new model, just wraps
existing route bge-m3…` — keyword and `model_name` both inside ±60.

Three sharpenings of wq#2021's finding, all confirmed by reading the call site at
`minerva_proposal.py:479-491`:

- The rejection is terminal. `route_conflict()`'s bare return sets
  `decision='reject'`, writes the reason, `mark_processed()`, `continue` — it never
  reaches `check()` or the judge. There is no OR, no second opinion, no downstream
  catch.
- `decision_reason` on this path is byte-identical to the string wq#2019 cites as
  the flagship symptom, so a recurrence is invisible to `reopen_stale_rejects.py`'s
  requeue grep: a fresh instance looks exactly like an untouched original.
- The trigger is anti-correlated with caution. Self-justifying "this does not add a
  new route" phrasing is *more* likely after wq#2019's INVARIANTS work pushed
  generated proposals toward stating what a change does and does not do.

## Fix shipped

`ops/wq2019-proposal-reviewer-fix/minerva_dedup.py:152-186` — the report's first
(stricter) fix shape, not the cheaper first-verb-only alternative. Both alternatives
were built and run before choosing:

```
wq2019 FP#1  clause-only (no +/-60 radius): (True, 'glm-5.3-flash-local')   shipped: (False, None)
multi-sentence TP  first-verb-only:         (False, None)                   shipped: (True, 'qwen36-local')
```

First-verb-only clears this exact repro, but it silently drops every genuine
duplicate add that isn't stated in the opening clause — "Add observability to the
fleet dashboard… Separately, register `qwen36-local` as a new litellm route" walks
right through it. That trade turns a false-positive bug into a false-negative one,
which is worse here: a missed duplicate reaches the judge and costs a call, while a
false positive costs a real idea. That case is now a permanent true positive in
`test_route_conflict.py`.

Three constraints, all clause-scoped:

1. The ±60 proximity window is clipped to the verb's own clause
   (`_CLAUSE_SPLIT_RE`, punctuation). The clause keeps a far-away `LiteLLM` out of
   the keyword check; the radius keeps a long clause from re-admitting it. Both
   bounds are load-bearing — dropping the radius re-breaks wq#2019's first
   false-positive case, whose `LiteLLM gateway` sits at the end of the same clause.
2. The route name must appear **after** the verb, inside that same span — the verb
   has to govern the name, not merely sit near it.
3. A negation token anywhere inside that span, on either side of the verb, kills
   the match: `does not add qwen36-local` (before the verb) and `add a cache rather
   than route bge-m3` (between verb and name).

Two details worth recording because they cost a debug cycle each:

- A period only closes a clause when whitespace or end-of-string follows it.
  `model_name`s carry dotted versions (`glm-5.3-flash-local`), and a naive `[.;:,…]`
  split cut the true positive's own route name in half — caught by the shipped
  probe, which is what that probe is for.
- Input is normalised `’ → '` before matching. LLM-authored `proposed_change`
  text emits curly apostrophes, and `doesn’t` slips past a `doesn'?t` pattern.
  `test_route_conflict.py` carries a curly-apostrophe case for this.

## Coverage

`test_route_conflict.py`: 8 false positives clear, 4 true positives still caught,
exit 0. Five of the eight false-positive cases actually reach the window logic; the
other three have no add-intent verb and return early. wq#2021 flagged that the
printed summary overstated coverage — the summary now prints the exercised count
alongside the total rather than a bare "4 cleared". Rewriting the three trivial
cases into real ones was out of scope for this judgment and is left as-is.

The installer's own health assertions in `install.sh:38-58` (one false positive must
clear, one true positive must still name `glm-5.3-flash-local`) were re-run against
the patched module: `minerva_dedup health OK`.

## Scope note

Unchanged from the whole chain: this lives in `ops/wq2019-proposal-reviewer-fix/`,
not `/home/lumen/minerva`. `install.sh` is the deploy path and has not been run;
that remains wq#2019's open tail, not something this judgment closes.

Not evaluated: whether any *current* reject rows in the live DB were produced by
this specific negation path. `reopen_stale_rejects.py` cannot distinguish them from
original wq#2019 false positives by reason string, so requeuing the 212 rows it
already targets will sweep up any that exist — no separate requeue is needed, but no
count can be given either.
