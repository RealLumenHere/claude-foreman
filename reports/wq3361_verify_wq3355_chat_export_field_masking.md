# wq#3361 verify: wq#3355 chat-export field masking (judgment on wq#3343 CONTRADICT of wq#3332)

Archetype 8 adversarial verify-pass. Author: claude-opus-5 (judgment). Verifier: claude-sonnet-5
(fresh instance). Commit under test: `7e286ab5` on this branch
(`fix(chat-export): mask every chat field by default, not body only`, Refs: wq#3355).

## What wq#3355 claimed

wq#3343 found `title`/`url` bypassing `bin/chat-export`'s masking guarantee. wq#3355's judgment:
centralize masking as opt-out (`OPAQUE_FIELDS = {"page_token"}`) rather than patch the two known
fields, so a future field is masked by default; re-mask on every `archive_payload()` call so a
pre-fix archive already on disk (Lumen + nephthys, synced hourly) heals on next write/`--print`;
and rename `install-chat-digest-local.sh` -> `install.sh` plus add a `--verify` handler, since
`ops-deploy-drift` only opens that exact filename.

## What I checked

- Read `bin/chat-export` (206 lines, full file) cold against the diff. `mask_block()`
  (`bin/chat-export:75-84`) masks every string field except `OPAQUE_FIELDS`; `parse_blocks()`
  masks at construction (line 98); `archive_payload()` (`bin/chat-export:118-126`) re-masks on
  every save and `--print`, the single funnel every record leaves through.
- Ran the shipped suite unmodified: `python3 -m pytest bin/tests/test_chat_export.py` ->
  **11/11 passed**. `python3 -m pytest
  ops/wq3332-chat-digest-local/test_verify_handler_wq3355.py` -> **7/7 passed**.
- Independent adversarial probe, different credential shapes and a different field than the
  author's own tests used (runtime-built, never literal): AWS-shaped key in `title` *and* in a
  `kind` attribute value (a field neither wq#3343 nor wq#3355's own tests touch), a JWT and a
  `Bearer` token and a 48-char hex run in `body`. Result: zero leaks in any field; `id` survived
  intact because it's read off the raw `url` before `mask_block()` runs (line 99) and is too
  short (36 chars) to trip either long-token regex (which require 40+).
  ```
  kind:  "rotate <redacted> now"
  title: "AWS leak <redacted>"
  body:  "...jwt <redacted> and bearer <redacted> and hex <redacted>"
  LEAKS: []
  ```
- Checked the `OPAQUE_FIELDS` exemption isn't a loophole: `page_token` is the only member,
  `id` is deliberately not exempt (verified it can't match `SECRET_RES`/`KV_RE` — correct, since
  a lowercased UUID is 36 chars and the two length-gated rules need 40+ contiguous).
- Verified the `install.sh` rename claim against the actual consumer, not just the PR's
  assertion: `grep -n install.sh .../ops-deploy-drift` (wq#3355 snapshot) shows
  `os.path.join(bd, "install.sh")` hardcoded at line 79/87 — the old name
  (`install-chat-digest-local.sh`) would in fact never be opened, and its `install-` prefix
  would have classified it under `ONESHOT` (line 31), not `RUNNERS`. The rename was necessary,
  not cosmetic.
- Traced `ops/_verify.sh`'s new `vremote_user_unit()` (lines 118-135): bus-failure substring
  match (`*bus*|*Failed*|*refused*`) → ERR before the MISS fallthrough; `waiting` counted as
  active for a timer (correct — a systemd timer sits in `waiting` between fires, not `active`);
  `$uctl` built with single quotes so `$(id -u)` survives to be evaluated on the *remote* shell,
  consistent with the existing `vremote_file`/`vremote_unit` convention of passing one quoted
  command string to `$_VSSH`.
- No dangling references to the old installer filename anywhere in code (only historical
  wording in old reports and the README's own "was X until wq#3355" note).

## Liveness probe — not run as literally specified, with reasons

The ticket's prescribed liveness check, `bash /home/lumen/foreman/ops/wq3332-chat-digest-local/
install.sh --verify`, could not be executed from this worker: the per-command Bash allowlist
(`harness/worker-settings.json`) has only `Bash(bash scripts/*)` (a relative path under
`scripts/`), no entry matching an absolute `ops/<bundle>/install.sh` invocation. Per the ticket's
own rules this is a "requires approval" refusal, not a retryable one, so I did not route around
it (e.g. via `python3 subprocess`) — that would defeat the allowlist's purpose. Flagging the gap:
`Bash(bash /home/lumen/foreman/ops/*/install.sh*)` (or similar) would need to be added for a
verify-pass worker to run a bundle's own liveness probe directly in the future.

In its place: (1) the handler's logic was verified by code trace plus its own stub-driven test
suite (7/7, no live node touched), matching wq#3355's claim; (2) `lumen_state.py current
nephthys` independently shows `nephthys.availability = offline (since 2026-09-29) [verified 2d
ago]` — so even had I run the literal command, `vremote_up` would fail first and `vdone` would
print `VERIFY ERROR` / exit 2, **not** exit 0. That is the *correct* behavior per the fix's own
design (an unreachable node must read ERR, never a false MISS or a false live) — it reflects
real infrastructure state, not a defect in wq3355's diff. The liveness gate mentioned in this
ticket (`ops_verified(wq3332-chat-digest-local)`) is not satisfiable right now for a reason
external to this code change: nephthys is down.

## Verdict: CONFIRM

No contradiction found. The centralize-and-opt-out design closes the exact gap wq#3343 found,
does not reopen the `page_token`/`id` exemptions it has to carve out, heals pre-fix archives on
next write as claimed, and the `install.sh` rename is independently verified necessary against
`ops-deploy-drift`'s actual lookup, not just asserted. All existing tests pass unmodified (18/18
across both suites) and an independently-constructed adversarial probe against untested fields
(`kind`) and fresh credential shapes found zero leaks.

Not re-implementing anything (ticket instruction: "Do NOT re-implement; verify"). No escalation —
confirming on the board per ticket policy for a non-contradiction.
