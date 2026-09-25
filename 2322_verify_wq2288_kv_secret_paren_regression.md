# wq#2322 — verify of wq#2288 (PR#815, merged 32aa000)

Verifier: claude-sonnet-5@high, fresh instance. Verdict: **CONTRADICT** (one blocking
finding; everything else confirmed). Escalated per archetype 8 to opus-5@high —
**wq#2632** filed, `deps=2288`. Full ISSUE-LOG entry: #730.

`d8c593a` (PR branch head) and `32aa000` (main, merged) are byte-identical across every
changed file (`git diff d8c593a 32aa000 -- harness/masking.py harness/fworker.py
token-engine/token_engine_daemon.py harness/tests/test_masking.py
token-engine/tests/test_daemon.py` — empty output) — no drift between what was reviewed
and what shipped.

## Per claim

| wq#2288 claim | Result | Evidence |
|---|---|---|
| No credential was burned on wq#2284 — `kv-secret` misread `key = apply_key_allowlist(reg)` (node_render_gateway.py:418) as a 19-char secret | CONFIRMED | Reproduced the misread against the pre-fix pattern (`masking_old.py`, extracted via `git show d8c593a^:harness/masking.py`): `mask_text("key = apply_key_allowlist(reg)")` → hits `['kv-secret']`. Post-fix: `[]`. The false positive is real and the fix removes it. |
| `masking.scan()` now returns a redacted record (prefix, length, sha256[:12], masked context) so a rotation ticket can identify the credential | CONFIRMED | Read `harness/masking.py:83-125`. `mask_alarm(ticket_id, attempt, labels, records=None)` (`harness/fworker.py`) persists via `write_mask_hits()` to `_runs/wq<id>/mask-hits-<n>.json` and the ntfy/rotation-ticket text now cites that path instead of the old "masked prefix is preserved in `_runs/...` artefacts" dead end. |
| `PrVerbTest::test_credential_shaped_body_denied`, left red on main by wq#2292, is unbroken | CONFIRMED | Test fixture widened from a 20-char to the real 36-char `ghp_` body (`token-engine/tests/test_daemon.py`), matching wq#2292's raised floor. |
| 106 tests pass | CONFIRMED (scoped), see below | `harness/tests/test_masking.py`: 64 passed. `token-engine/tests/test_daemon.py`: 60 passed. (124 across the two files most directly touched; a full `harness/tests/` sweep was attempted and exceeded the working budget on this pass — same shape as wq#2497's precedent — so it is not part of this verdict either way.) |

## Contradiction: the trailing lookahead silently defeats detection, it doesn't just narrow it

The fix adds `(?![A-Za-z0-9._-]|\()` after the `kv-secret` value group in **both** Python
copies (`harness/masking.py:64`, `token-engine/token_engine_daemon.py:125`) to stop
`apply_key_allowlist` from being read as a secret. The commit's own comment states the
rationale as categorical: *"A value that is immediately called is a function, never a
credential."* That's false as a blanket claim, and the regex enforces the blanket
version, not a narrower one scoped to identifier-call shapes.

Because `[A-Za-z0-9._-]{12,}` is already greedy, the `(?![A-Za-z0-9._-]|...)` half of the
lookahead is inert (a maximal greedy run never has a same-class char after it). The
entire behavioral effect of this change is the `|\(` alternative: **any** kv-secret-shaped
value directly followed by an unspaced `(`, with no separator, no longer matches — not
truncated, not partially masked, not alarmed. Not "narrower," entirely undetected.

Reproduced directly against the worktree's live `harness/masking.py`
(`/tmp/fworker-wq2322/probe.py`) and the current `token-engine/token_engine_daemon.py`
`kv_secret` pattern (copied verbatim into a standalone script since the daemon module has
an unrelated missing sibling import, `fleet_exec_policy`, that blocks a direct import —
`/tmp/fworker-wq2322/probe_daemon_regex.py`):

```
>>> masking.mask_text("TOKEN=abcdefghijklmnop123456(")
('TOKEN=abcdefghijklmnop123456(', [])          # post-fix: no mask, no alarm
```

Against the pre-fix pattern (`masking_old.py`, extracted from `d8c593a^`) the identical
input **is** caught:

```
>>> old.mask_text("TOKEN=abcdefghijklmnop123456(")
('TOKEN=MASKED(', ['kv-secret'])               # pre-fix: masked and alarmed
```

Same result for `password: hunter2hunter2(` and for the daemon's `kv_secret` regex
directly (`token_engine_daemon.py`'s copy, current main, not just the PR's version) — the
gap is live in both consumers, not a leftover from an old revision.

This is not a hypothetical shape. `KEY=<value>(<annotation>)` — a credential immediately
followed by an unspaced parenthetical (a cache/expiry/source annotation, a debug-print
`f"{name}={value}({note})"`, a stack-trace kwarg dump) — is a plausible line shape in the
exact kind of tool-output/log text this module exists to scan, and it is precisely the
kind of value the fix's own tests (`test_real_kv_secrets_still_fire`,
`test_mask_does_not_read_a_called_identifier_as_a_secret`) never probed: every shipped
test puts the real-secret cases at end-of-string or end-of-line, and the
called-identifier cases always have a space before the value (`key = apply_key_allowlist
(reg)` — space-before-paren — which *does* still fall through correctly, since the lookahead
never engages there; only the no-space adjacency is affected).

Severity, honestly: this is the masking layer that feeds instinct 4 — "a hit is an ALARM,
not a save... the caller must page ntfy and open a rotation ticket." A value that matches
none of the vendor-prefixed patterns (github/cloudflare/sk/slack/aws) and hits this exact
adjacency is now a credential that reaches every downstream artefact (attempt notes,
episode store, PR text) unmasked, with **zero** alarm — silently worse than the false
positive this PR was filed to fix, and a regression from pre-fix behavior on the identical
input. Scope is narrow (kv-secret only; the other five patterns are untouched and don't
carry this lookahead) but the failure mode is a silent miss, not noise.

## Attempts to break the fix that did NOT find a defect

- `scan()`/`mask_text()` lockstep: `test_scan_stays_in_lockstep_with_mask_text`,
  `test_scan_skips_a_value_an_earlier_pattern_already_masked` — re-verified by hand with a
  three-credential composite string; records track `mask_text()`'s output exactly.
- `write_mask_hits()` append/atomic-write: reread `harness/fworker.py:678-696` — temp file
  + `os.replace`, best-effort try/except around the whole thing so an alarm-path failure
  can't take down the attempt. `run_dir()` creates the directory first (`makedirs`,
  `exist_ok=True`), so no missing-dir race on first write.
- Context-window-cut-after-masking (`harness/masking.py:95-96`, the wq#1441 shape):
  `test_scan_context_is_cut_after_masking_not_before` reproduced by hand — a second
  credential inside the context window arrives pre-masked, never truncated into a usable
  fragment.
- `test_a_called_identifier_is_not_a_credential`'s two cases (`key = apply_key_allowlist(reg)`,
  `GH_TOKEN = os.environ.get(...)`) both correctly return `[]` — the fix does what it says
  for the exact shape that fired on wq#2284.
- `PrVerbTest::test_credential_shaped_body_denied` fixture width (36 chars) matches
  `github` pattern's `{30,}` floor and wq#2292's own canary width — not a coincidental
  pass.

## Actions taken

| Action | Rationale |
|---|---|
| ISSUE-LOG **#730** appended | Records the trailing-lookahead miss and its scope, per doctrine |
| **wq#2632** filed, pri 1, `deps=2288`, `claude-opus-5@high` | Judgment ticket per archetype-8 protocol: decide the fix (narrow the lookahead to require an *identifier-shaped* prefix before treating `(` as a function call, vs. accept the gap and document it, vs. add a paren-adjacency-specific pattern) |
| wq#2288 left `done`, PR#815/`32aa000` left as-is, not reopened or edited here | Verify-only per archetype 8 — do not re-implement; opus-5@high judgment decides disposition |

## Disposition (wq#2632, 2026-09-25)

Option 1 — **narrow the lookahead** — taken, and moved to where the value STARTS rather
than after it: `masking._KV_CALL`. The report's reading of the mechanism holds, with one
addition: a negative lookahead after a `{12,}` run is retried at every admitted length,
which is *why* the miss is total rather than a truncation. Option 3 (mask but suppress
only the escalation) was rejected as already built — `burn_filter.classify()` is that
carve-out, and it postdates wq#2288, so the pattern never needed to carry the alarm's
precision alone. Details and the two side findings: ISSUE-LOG row 745.
