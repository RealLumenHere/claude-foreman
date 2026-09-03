# wq#1381 — Adversarial verify-pass of wq#1374 (orchestrator operability slice 1)

**Verdict: CONTRADICT** — 3 of the 4 shipped fixes are correct and verified by direct
execution against the real code. The 4th (`find_artifact()`'s `:N`/`:N-M` line-locator
strip, `bin/wq:1537-1544`) has a real, reproducible bug: the "does this look path-shaped"
pre-filter runs on the token *before* the new suffix-strip regex, so any path-shaped
citation without a `/` — including a bare filename with a real extension, or a repo-root
file cited relative to its own repo (`wq:1537-1544`, the natural citation from inside
`/home/lumen/bin` itself) — is rejected, contradicting the ticket's own stated acceptance
("`wq done --notes ... path/to/file.py:88-104` is accepted as evidence without rewording").

## What was shipped

Two PRs, per wq#1374's own done notes:
- **claude-foreman PR#266** (merged, commit `cb605f1`, 1 file): `dispatcher/dispatch_health.py`
  `classify_passes()` — widened `has_config_error` from `event=="error" and stage=="config"`
  to any `event=="error"`.
- **lumen-bin PR#60** (open, commit `96717f2` on branch
  `feat/wq1374-orch-operability-session-budget-blocked-reap-evidence`, 1 file, `wq`):
  (1) `ORCH_SESSION_RE`/`ORCH_SESSION_MAX_AGE_MIN`/`ORCH_SESSION_MAX_TICKETS` — orchestrator-
  class sessions get a separate, generous retirement budget read at `enforce_retirement`
  time; (3) `release_session_holdings()` now also reverts `status='blocked'` claims, not just
  `'claimed'`; (4) `find_artifact()` strips a trailing `:N`/`:N-M` before the `os.path.exists`
  check.

Note: `/home/lumen/foreman` (the primary tree/verify-repo root) is itself 8 commits behind
`origin/main` and does not have `cb605f1` checked out on disk — I read it via `git show
cb605f1:dispatcher/dispatch_health.py` rather than off the working tree. Not a defect of
this diff (unrelated stale-checkout state, matches the pattern noted in prior verify
reports e.g. wq#1267/wq#1310).

## Item 2 (dispatch_health.py crash classifier) — CONFIRMED

Ran the shipped `classify_passes()` (loaded from `cb605f1` directly) against synthetic
event streams:

```
wq1368-shape (error stage=spawn, no pass_end):        unhealthy, "error event (pass aborted)"
spawn error mid-pass, pass_end follows (regression):   healthy, "spawned=2 candidates=3"
config error, no pass_end (pre-existing baseline):     unhealthy, "error event (pass aborted)"
```

Confirms the fix catches the wq#1368 shape (an unhandled crash at any stage with no
`pass_end`) while not flagging a pass that logs a non-fatal per-candidate error (e.g.
`foreman_dispatcher.py:835`, `stage="spawn"`, `spawn_failed`, then `continue`s the loop) and
still completes normally — `has_pass_end=True` always wins the "healthy" branch regardless
of `has_config_error`, so no new false positives from the widened check. Read
`foreman_dispatcher.py`'s 6 `log.emit("error", ...)` call sites (`stage=` scan/spawn/config/
config_lane/pass/langfuse); the ones inside a loop (`scan`, `spawn`) all `continue` rather
than abort, so they don't suppress `pass_end`.

## Item 1 (orch session budget exemption) — CONFIRMED

`ORCH_SESSION_RE = re.compile(r"^(orch|foreman|forch)", re.IGNORECASE)` matches `row[1]`,
which `is_session()` returns as `base_name` (not the full session id) — correct column.
Cross-checked against `cmd_decide`'s docstring (`bin/wq:1788`, pre-existing, not touched by
this diff): "Caller must be a registered ORCH session (base name starts with 'orch' or
'foreman' or 'forch'...)" — the new regex is not a fabricated convention, it matches an
existing documented one (instinct 7 check: grepped for the pattern, it's real). My own
verifier identity this run (`fworker-wq1381`) doesn't match the prefix, so BUILD workers
correctly keep the tighter global budget.

## Item 3 (blocked-claim reap in release_session_holdings) — CONFIRMED

`release_session_holdings()` now selects/reverts `status IN ('claimed','blocked')` and
clears `block_reason`/`blocked_at` alongside the existing fields. Verified the premise
independently: `cmd_block` → `_finish(a, 'blocked')` (`bin/wq:1663`) UPDATEs
`status='blocked', notes=, block_reason=, blocked_at=` and does **not** clear `claimed_by`
— so a session that retires while holding a blocked ticket really did leave it stranded
under a dead `claimed_by` pre-fix, exactly as the docstring claims. All 4 call sites of
`release_session_holdings()` (`bin/wq:587,633,2024,2031`) still unpack the same
`(tickets, locks)` 2-tuple — return shape unchanged, no caller breakage.

## Item 4 (find_artifact `:N`/`:N-M` strip) — CONTRADICTED

`find_artifact()`'s path branch (`bin/wq:1533-1544`):

```python
for raw in notes.split():
    tok = raw.strip(".,;:()[]\"'")
    if not tok or tok.startswith("http"):
        continue
    if "/" not in tok and not re.search(r"\.[A-Za-z0-9]{1,5}$", tok):
        continue  # doesn't look path-shaped — skip rather than stat() every word
    path_tok = re.sub(r":\d+(-\d+)?$", "", tok)   # wq#1374's new line
    for cand in _artifact_path_candidates(path_tok):
        if os.path.exists(cand):
            return True, "path", cand
```

The path-shape pre-filter (`"/" not in tok and not re.search(...)`) runs on `tok`, which
still has the `:N-M` suffix attached at that point — the strip happens one line later. The
extension regex requires the extension to sit at the very end of the string (`$`), but with
a locator suffix appended the end of the string is `:88-104`, not `.py`, so the regex never
matches when there's no `/` in the token. Net effect: the fix works whenever the cited path
has a directory component (`bin/wq:1537-1544`, `path/to/file.py:88-104` — both pass), but
silently fails to recognize the *exact same citation shape* for anything cited without one
— including a bare filename with a real extension.

Reproduced against the live shipped code (`/home/lumen/bin/wq`, commit `96717f2`), executed
with `cwd=/home/lumen/bin` (the common case per `_artifact_path_candidates`'s own docstring:
"agents run `wq done` from inside their own worktree"):

```
find_artifact("closed via wq:1537-1544")          -> (False, None, None)   # wq is a real file
find_artifact("closed via bin/wq:1537-1544")       -> (True, 'path', '/home/lumen/bin/wq')
find_artifact("see dispatch_health.py:132")        -> (False, None, None)  # real .py file, no dir
```

`wq:1537-1544` is not a contrived case — it is the natural way to cite `bin/wq`'s own
top-level script from inside the `lumen-bin` repo, i.e. exactly the file this PR patches.
wq#1374's own done-ticket notes happened to avoid the bug only because they wrote
`bin/wq:1537-1544` (prefixed with the parent dir name), not `wq:1537-1544`. No test was
added for `find_artifact()` in this PR (grepped `/home/lumen/bin/tests/`; the only
`find_artifact*` hits are the unrelated plural `find_artifacts()` in
`test_wq_hygiene_reconcile.py`, a different function in a different module) — nothing in
the shipped suite would have caught this.

## Disposition

Minimal fix: strip the `:N`/`:N-M` suffix *before* the path-shape check, e.g. compute
`path_tok = re.sub(r":\d+(-\d+)?$", "", tok)` first and run both the shape pre-filter and
the extension regex against `path_tok` instead of `tok`. One-line reorder, no schema change,
consistent with the "each fix is independently testable and low-risk" framing wq#1374 shipped
under. Items 1-3 need no rework.

Escalating per archetype-8 protocol: contradiction on item 4 → opus-5@high.
