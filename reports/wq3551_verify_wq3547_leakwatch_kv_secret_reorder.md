# wq#3551 — verify of wq#3547 (rotation row, kv-secret, sha256:4a4162bfc0fa)

Verifier: claude-sonnet-5@high, fresh instance (author was claude-opus-5-5).
Verdict: **CONFIRM** the diff shipped by wq#3547 —
http://100.121.17.5:3000/lumen/claude-foreman/pulls/1622 (branch
`fix/wq3547-rotation-burned-credential-sha256-4a4162-a1`, commit `3366217a`).
The fix is correct and holds under adversarial probing beyond its own committed
test case. One operational note below: the PR is stale — the identical fix is
already on `main`.

## Per claim

| wq#3547 claim | Result | Evidence |
|---|---|---|
| Root cause: `leakwatch.py`'s per-hit listing was `f"  {label}  sha12={sha}"`, where `label` includes the matched kv-secret prefix with its trailing separator (e.g. `"kv-secret LiteLLM key ="`). Re-reading the ticket text re-ran the scanner, and `_KV_BODY` includes `=` in its charset, so the body matched through the two spaces and `sha12=` straight into the hex fingerprint — the listing line armed its own tripwire. | CONFIRMED | Reproduced directly against `harness/masking.py`: built the pre-fix string `"  kv-secret LiteLLM key =  <18-char value elided, sha12=9d78336f717c>"` and ran it through `masking.scan()` — it fires (`{'label': 'kv-secret', 'prefix': 'kv-secret LiteLLM key =  ', ...}`), matching the chain described (wq#3449 → wq#3456 → wq#3547). |
| Fix: put `sha12=<hex>` first and the label last (`f"  sha12={sha}  {label}"`), so nothing with a separator-shaped tail follows the label on the line. | CONFIRMED | Same harness, same inputs reordered: `masking.scan("  sha12=a1bffa5d7d23  kv-secret LiteLLM key =")` → `[]`. No hit. |
| Fix generalizes beyond the one committed test case (space-separated prose label) | CONFIRMED, independently probed | Built a second, un-committed scenario using the env-style kv-secret form (`MY_API_TOKEN=<value>`, no spaces around `=`): pre-fix ordering (`"  kv-secret MY_API_TOKEN=  sha12=<hex>"`) fires; post-fix ordering (`"  sha12=<hex>  kv-secret MY_API_TOKEN="`) does not. This is the required out-of-population probe (doctrine: an acceptance probe must be able to fail, and must be drawn from outside the input shape the author's own test covers) — the committed test only exercises the `"LiteLLM key = "` prose form from the actual wq#3449 incident. |
| `test_ticket_text_for_a_key_labelled_hit_is_not_itself_a_hit` passes, no regressions | CONFIRMED | `pytest ops/wq3354-fleet-selfheal/tests/test_leakwatch.py -q` on current `main` → **12 passed**, 0 failed. |

## Why nothing crosses a newline (a gap I checked and ruled out)

The listing joins multiple hits with `"\n"`. I checked whether a label at the end
of one hit's line could combine with `sha12=` at the start of the *next* hit's
line to reconstruct the bug across the join. It can't: neither `_KV_SEP`
(`["' \t]*[:=]["' \t]*`) nor `_KV_BODY` admits `\n` in their character classes, so
no kv-secret match can span a line boundary regardless of hit count or order.

## Operational note (not a contradiction, but material)

`main` already contains this exact fix, byte-for-byte, as commit `cb7e7692`
("fix(selfheal): leakwatch ticket listing no longer reads as a kv-secret hit"),
landed 2026-10-07 07:35:50 — **35 seconds after** wq#3547's own commit
(`3366217a`, 07:35:15):

```
git diff cb7e7692:ops/wq3354-fleet-selfheal/leakwatch.py 3366217a:ops/wq3354-fleet-selfheal/leakwatch.py
git diff cb7e7692:ops/wq3354-fleet-selfheal/tests/test_leakwatch.py 3366217a:ops/wq3354-fleet-selfheal/tests/test_leakwatch.py
```
Both exit 0 — no differences. This looks like a race: the same root cause got
fixed on two lanes within the same minute, one went straight to `main`
(`cb7e7692`), the other is still sitting open as PR #1622. Landing PR #1622 now
would be a true no-op (identical bytes already on `main`) — harmless, but the
PR should be closed as already-shipped rather than merged, to avoid a confusing
no-op merge commit. This does not affect the CONFIRM verdict: the diff under
review is correct regardless of which lane's copy ends up recorded as "the" fix.

## Unrelated pre-existing failure noticed in passing

`pytest harness/tests/test_masking.py -q` on current `main`: **185 passed, 1
failed** — `test_the_mask_suite_does_not_arm_its_own_tripwire` fails on
`ops/wq3354-fleet-selfheal/tests/test_resource_classes.py` (`kv-secret`, `"rotate
key: "`, 16 chars). This file is untouched by wq#3547's diff and the failure
reproduces identically with or without wq#3547's change applied (it's already
in main's working tree either way) — it is pre-existing drift, not a regression
from this PR, and out of scope for this verify. Flagging in case it isn't
already tracked.

## Verdict

**CONFIRM.** wq#3547's diagnosis and fix are both correct: the listing reorder
closes the self-arming tripwire for the reported case and for an independently
constructed out-of-population case (env-style label), nothing crosses the `\n`
join, and the regression suite is green on current `main`. The only actionable
follow-up is administrative — close PR #1622 as already-shipped via `cb7e7692`
rather than merging it.
