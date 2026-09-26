# wq#2342 — verify-pass (archetype 8) of wq#2299 (PR #820, `3f77809`)

Verifier: claude-sonnet-5@high, fresh instance (author was claude-opus-5). Verdict: **CONTRADICTED** (one real defect found; core fix logic confirmed correct). Not self-healed — filed as **wq#2629**.

## What wq#2299 shipped

`3f77809d867ea89761548202ab012fca5a448757`, "fix(mask): end the rotation-ticket chain the tripwire feeds" — `harness/fworker.py` (+45), `harness/tests/test_masking.py` (+52), `harness/tests/test_token_injection.py` (+50/-14).

Claim: `wq#2299`'s own alarm (`sk-key` on wq#2290's stream) was a false positive — the tripwire refiring on the artefacts a rotation ticket points at. Fix: `ROTATION_TITLE_PREFIX` + `rotation_chain_refire()` — when a mask hit fires while working a ticket whose title marks it as a rotation ticket, note the parent instead of filing a pri-1 child; `test_token_injection.py`'s `sk-ant-oat01-*` literals split so reading the file itself no longer trips the tripwire, pinned by a new canary test.

## Confirmed correct

- **The false-positive claim.** wq#2299's own `sk-key` hit hashes to `test_token_injection.py` fixtures (four `sk-ant-oat01-<dummy>` strings), consistent with the ISSUE-LOG row-577 forensics (replay of the transcript at `2026-09-20T22:22:03.282Z`).
- **The chain-guard logic itself.** `rotation_chain_refire()` correctly matches `ROTATION_TITLE_PREFIX`, notes the parent ticket, preserves the mask-hits evidence path, and still pages ntfy. `harness/fworker.py` on current HEAD: `76 passed` for `test_masking.py` + `test_token_injection.py` combined, including the 8 chain/rotation-specific tests and the new canary `test_this_file_reads_without_tripping_the_mask_tripwire` (run individually, PASSED).
- **The one gap I initially suspected — already closed.** The original `3f77809` used `title.startswith(ROTATION_TITLE_PREFIX)`, which does not match the wrapped title every verify-pass ticket gets (`[verify] wq#N [rotation] burned credential in wq#M ...` — this very ticket's own title is that shape). wq#2308 (`005c84a`/`1eac73f`, ISSUE-LOG row 590) already found this and widened the check to `ROTATION_TITLE_PREFIX not in title`, landed on main before this verify-pass was dispatched. No further action needed here.

## Contradiction found — filed as wq#2629

`rotation_chain_refire()` (`harness/fworker.py:910`) calls `wq("note", str(ticket_id), msg)` and `ntfy(...)` with **no try/except**. Compare the sibling non-chain path a few lines later in `mask_alarm()`, which wraps its own `wq("add", ...)` call explicitly:

```python
try:
    p = wq("add", "--lane", "fleet", ...)
    ...
except Exception as e:  # noqa: BLE001
    log(f"WARN: rotation ticket add crashed for wq#{ticket_id}: {e!r}")
```

`mask_alarm`'s own docstring states the intent: "Page ntfy AND file a rotation ticket; both best-effort so an alerting failure never takes down the attempt itself." The chain-guard branch (added by this diff) does not honor that.

**Reproduced live** (`/tmp/fworker-wq2342/probe_chain_guard_crash.py`, run against current HEAD):

```
--- chain path: wq('note', ...) raises ---
title='[rotation] burned credential2290 transcript (sk-key)' note_raises=True add_raises=False
  -> mask_alarm RAISED RuntimeError('wq note crashed (simulated...)'), calls=['note']

--- non-chain path: wq('add', ...) raises (should be caught) ---
[fworker ...] WARN: rotation ticket add crashed for wq#9999: RuntimeError('wq add crashed (simulated)')
title='[token-engine T10] remaining verbs' note_raises=False add_raises=True
  -> mask_alarm returned normally, calls=['add']
```

Same `wq()` helper, same class of possible failure (subprocess-level OS error; `wq(..., check=False)` never raises on a mere nonzero exit) — one branch is defended, the other is not. `mask_alarm()` runs synchronously inside two read loops with no enclosing try/except at that level (`run_child_gateway`'s SSE loop, `fworker.py:2771`; the non-gateway line loop, `fworker.py:2929`), so an exception here aborts the in-flight attempt rather than merely losing a log line. Since a rotation ticket's own worker *and* every verify-pass ticket spawned for it read the same artefacts (the exact archetype this diff exists to fix), this branch is not a rare edge case — it fires on most of the wq#2275 storm family.

Not self-healed: checked `git log --all -- harness/fworker.py` for any later touch to `rotation_chain_refire`'s note/ntfy calls — only wq#2308's title-match widening landed; no exception handling was ever added.

## Disposition

Per doctrine ("Do NOT re-implement; verify"), the fix was not written here. Filed **wq#2629** (lane fleet, pri 2) with the reproduction and a GATE describing the expected regression test (mirror `test_an_ordinary_ticket_still_files_a_rotation_ticket`'s crash-resilience for the add path, but on the note path). Noted on wq#2299.
