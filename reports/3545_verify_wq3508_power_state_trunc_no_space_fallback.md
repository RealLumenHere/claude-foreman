# wq#3545 verify-pass: wq#3508 [selfheal] fleet node thor offline on tailscale for 31 min — CONTRADICT

**Verdict: CONTRADICT.** PR #1619 (merged as `dfb7c4431f`, `ops/wq3354-fleet-selfheal/sh_nodes.py`)
fixes the mid-word truncation bug for the one shape of input its own test exercises (a value
that happens to contain a space within the first 200 characters), but the new `_trunc()`
helper's fallback branch — reached whenever a `power_*` fact has **no space in its first 200
characters** — reproduces the exact `v[:200]` hard cut the PR says it removed. A single long
token (a path, a device ID, a concatenated flag string) spanning the 200-char boundary gets
sliced mid-word again, re-creating the precise symptom (a selfheal ticket citing a truncated,
nonexistent-looking fragment) that wq#3508 was filed over.

## What the PR does

Diff (`dfb7c4431f`, 2 files, +25/-2), confirmed via `git show dfb7c4431f`:

```python
def _trunc(v, n=200):
    """Cut at the last space before n, not mid-word — a hard v[:n] cut split a fact's
    trailing file path mid-name (wq#3508: ".../thor-power" -> ".../thor-powe")."""
    if len(v) <= n:
        return v
    cut = v.rfind(" ", 0, n)
    return (v[:cut] if cut > 0 else v[:n]) + "…"


def power_state(ctx, preds):
    lines = [f"{k} = {_trunc(v)}" for k, v in sorted(preds.items())
             if k.startswith("power_") or k == "status"]
    ...
    live = _trunc((p.stdout or p.stderr or "").strip()) if p else "plug-ctl did not run"
```

Regression test `test_power_state_truncates_on_word_boundary_not_mid_path`
(`tests/test_resource_classes.py:193-204`) builds a 201-char fixture that has a space eight
characters before the cutoff and asserts the trailing `/home/lumen/fleet/thor-power` path is
dropped whole (not sliced to `thor-powe`) and the line ends with `…`. That test passes, and the
fix genuinely closes the reported case: `thor.power_mgmt`'s real value from wq#3508 has plenty
of spaces before character 200.

## The contradiction, with evidence

`_trunc()`'s own docstring promises "not mid-word." The code only delivers that when
`v.rfind(" ", 0, n)` finds a space, i.e. `cut > 0`. When no space exists anywhere in the first
`n` characters, `cut == -1`, `cut > 0` is `False`, and the function falls through to
`v[:n] + "…"` — the identical hard, mid-word cut the commit message describes as the bug. The
shipped test cannot catch this because its fixture places a space at offset 169, well inside
the 200-char budget; it never probes a value that is (or contains, at the front) a single token
longer than the budget.

Probe (`/tmp/fworker-wq3545/probe_trunc_no_space_fallback.py`), run directly against the
checked-out fix:

```
### control: author's own fixture (space exists before n=200) ###
-- author fixture --
input len: 201
output   : power_mgmt = TLP 1.8 auto AC/BAT; ... charge cap 85pct; kit…
ends with ellipsis: True
PASS: author's fixture does not reproduce the bug (as shipped test asserts)

### probe: single long token, no space anywhere in the first 200 chars ###
-- no-space token --
input len: 247
output   : power_mgmt = /home/lumen/fleet/aaaa...aaaa…   (190 'a's, word never completes)
ends with ellipsis: True
mid-word cut reproduced (tail is a run of 'a', word never completes): True
```

The negative control (the author's own fixture) passes exactly as the shipped test asserts,
proving the probe is falsifiable (wq#2630): it is not vacuously true, it only fails on an input
shape the test never constructed. On that shape — a `power_*` fact that is, or starts with, one
token >200 chars with no interior space — the output is byte-for-byte what `v[:200] + "…"`
(the pre-fix code) would have produced: a word sliced in half, now additionally decorated with
an ellipsis that implies the cut was clean when it was not.

This is not a contrived type error or an out-of-domain input. `power_state()` is called over
every `power_*`/`status` predicate `lumen_state current` emits for every fleet node
(`sh_nodes.py:50-58`, free-text parsed by a regex, no schema), plus raw `plug-ctl` stdout/stderr
(`sh_nodes.py:84`) — both are untrusted, variable-length strings from heterogeneous probes
across the fleet, not just thor's one logged value. A long device path, mount point, or
hardware ID with no embedded space is a plausible value for *some* node's `power_mgmt` or
`power_plug` fact, now or later; the fix only protects against the subset of those values that
happen to contain a space in the first 200 characters.

## Things checked that are NOT contradictions

- **The shipped fix is correct and sufficient for the exact case in wq#3508.** `thor.power_mgmt`'s
  real value has multiple spaces before offset 200; replaying it through `_trunc()` drops the
  kit path cleanly and does not reproduce `thor-powe`. The author's own claim about their own
  reproduction case is true.
- **Test suite: 14/14 pass in `test_resource_classes.py`, full `ops/wq3354-fleet-selfheal/tests`
  suite green** (`python3 -m pytest tests/ -q`), unaffected by this finding — there is no test
  that constructs a no-space-in-budget fixture, so the suite was never positioned to catch this.
- **No other hard-coded `[:200]`-style cut survives elsewhere in the module** (`grep -rn
  "\[:200\]"` over `ops/wq3354-fleet-selfheal` after the fix: zero hits outside `_trunc` itself).
  `selfheal_common.py:133`'s unrelated `title[:180]` is a separate, pre-existing truncation on
  ticket titles, out of scope for this diff.
- **`cut > 0` vs `cut >= 0`** (a value starting with a literal leading space) is a second,
  much lower-severity inconsistency in the same function — noted for completeness, not raised
  as a separate contradiction; it shares the same root cause and the same fix would resolve
  both.

## Disposition

`wq#3545` closes with verdict **CONTRADICT**, escalated to opus-5@high per ticket instructions
(archetype verify-pass policy: escalate_on_contradiction). Not re-implemented — verify-pass is
not a re-implementation gate (precedent `reports/wq3536_verify_wq3506_thor_charger_fault_blanket_ignore.md`,
`reports/872_verify_wq853_fleet_maint_run.md`). The follow-up judgment pass should decide how to
close the no-space gap — e.g. fall back to a hard cut at the *nearest* word-safe boundary by
also trying `v.find(" ", n)` (truncate forward to the next space instead of backward) when no
space precedes `n`, or simply cap `_trunc`'s hard-cut fallback with a shorter safety margin —
without reopening wq#3508's original ticket-citing-a-truncated-path problem.
