# wq#3014 — verify wq#3008: cert-watch survives a malformed targets.json

Adversarial verify-pass (archetype 8) of PR#1299 (commit `6b829258`), which closed the
wq#2093 verify finding on wq#2092. Author: claude-opus-5. Verifier: claude-sonnet-5@high,
fresh instance, read the diff cold. **CONFIRMED** — no contradiction, no escalation.

## What the PR claims

`load_extra_targets()` used to let anything but a missing file raise straight through
`run()`/`main()` — a hand-edited `targets.json` typo (trailing comma, wrong shape, bad
port) killed the whole cert-watch run, including the unrelated cloudflared-host checks,
with no alert. The fix makes `load_extra_targets()` return `(targets, error)` instead of
raising, wraps parsing in explicit shape checks plus a `(ValueError, KeyError, TypeError,
AttributeError)` backstop, has `run()` print + dedupe-ntfy the error while still checking
the cloudflared hosts, and adds an `install.sh --verify` step that parses the deployed
`targets.json` with the shipped loader so a broken file fails deploy-verify instead of
silently dropping the extra targets.

## What I did to try to break it

1. Read `cert_watch.py` in full (not just the diff) — `load_extra_targets`,
   `run()`, `install.sh`, `test_cert_watch.py`.
2. Ran the shipped suite: `python3 -m unittest discover -s ops/wq2092-cert-watch -p
   test_cert_watch.py` → **31 tests, OK**, matching the PR's own evidence.
3. Adversarial probe beyond the shipped test cases — inputs the tests don't cover —
   checking only that `load_extra_targets()` never raises:
   duplicate host keys, `"port"` as float/negative/huge/`null`/`NaN`, `"host"` as a
   number/list/`null`, deeply nested garbage in `"extra"`, a NUL byte in a string
   (invalid JSON control char), a duplicate top-level `"extra"` key, and a UTF-8 BOM
   prefix. **Every case returned a `(targets, error)` tuple — nothing raised.**
   (Some are semantically loose — a float port silently truncates, a list `"host"`
   stringifies to `"['a']"`, out-of-range ports aren't rejected — but none of that is
   a crash, and range/type strictness beyond "does it raise" is outside what wq#3008
   claims to fix.)
4. Replayed the exact `install.sh --verify` python fragment (`cert_watch.load_extra_targets(path)[1]`)
   against a trailing-comma file → non-empty error string (would `_vmiss` → VERIFY FAIL)
   and against a well-formed file → empty string (would pass).
5. Live verify on the deployed target: `lumen-run start --name wq3014-certwatch-verify
   -- bash <worktree>/ops/wq2092-cert-watch/install.sh --verify` → `DONE:0`, log
   `VERIFY OK`.
6. Live falsification-style dry-run against the real deployed `cert_watch.py` and real
   cloudflared hosts, pointed at a trailing-comma `targets.json` in scratch space
   (`--targets /tmp/.../targets.json --state /tmp/.../state.json --dry-run`, so no
   production state/aq/ntfy touched): `lumen-run start --name wq3014-certwatch-dryrun --
   python3 /home/lumen/services/lumen-cert-watch/cert_watch.py run --targets ... --dry-run`
   → `DONE:0`. Log shows `cert-watch: targets.json unparseable: Illegal trailing comma...
   (ignoring extra targets)`, a `[dry-run] ntfy` line for the malformed-targets alert, then
   ~28 `cert-watch: <host> tier=ok days_left=...` lines for the real cloudflared-fronted
   hosts (chat/claude/n8n/ntfy/beszel/status/analytics/langfuse/... .lumennode.uk) — i.e.
   the exact pre-fix failure mode (whole run aborts, hosts never checked) does not
   reproduce on the live target with the live host list.

## Verdict

CONFIRMED. `load_extra_targets()` cannot raise on any malformed input I tried, `run()`
degrades to "ignore extra targets, alert once, keep checking cloudflared hosts" exactly
as claimed, `install.sh --verify` genuinely fails on a broken deployed file (verified via
the same fragment it runs, not just read), and the live target passes `--verify` and a
live dry-run against a malformed file without touching production state.

Non-blocking observations (out of scope for this ticket, not contradictions):
- A non-`ENOENT` `OSError` opening `targets.json` (e.g. permission denied) is silently
  treated the same as "no file" — no alert. This is unchanged from the pre-fix code
  (`except OSError: return []`), not introduced by wq#3008.
- Port/host values aren't range- or type-strict (float ports truncate, non-string hosts
  are `str()`-coerced) — loose but not a crash.

## Evidence

- `python3 -m unittest discover -s ops/wq2092-cert-watch -p test_cert_watch.py` → `Ran 31
  tests in 0.177s` / `OK`.
- Adversarial `load_extra_targets()` probe (13 inputs outside the shipped test cases) —
  all returned `(targets, error)`, none raised.
- `install.sh --verify` fragment replay: malformed file → non-empty error string;
  well-formed file → empty string.
- `lumen-run` unit `lumen-job-wq3014-certwatch-verify-1790603561` → `DONE:0`, log
  `VERIFY OK`.
- `lumen-run` unit `lumen-job-wq3014-certwatch-dryrun-1790603572` → `DONE:0`, log shows
  the malformed-targets warning plus ~28 real hosts checked at `tier=ok`.

## Liveness

`ops_verified(wq2092-cert-watch)` — confirmed live via step 5 above.
