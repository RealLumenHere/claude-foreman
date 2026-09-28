# wq#3008 — cert-watch: a malformed targets.json no longer kills the run

Closes the wq#2093 verify-pass finding on wq#2092 (PR#683/#684). Confirmed by an
independent opus-5@high judgment pass before the fix.

## The bug

`load_extra_targets()` opened `targets.json` inside a `try` that caught only
`OSError`, then built its result with `[(e["host"], int(e.get("port", 443)))
for e in doc.get("extra", [])]`. `run()` called it unguarded and `main()` had no
guard either, so anything other than a missing file raised through
`sys.exit(main())`:

| broken file | exception |
| --- | --- |
| trailing comma, missing quote | `JSONDecodeError` (a `ValueError`) |
| top-level list | `AttributeError` on `doc.get` |
| entry missing `"host"` | `KeyError` |
| `"port": "https"` | `ValueError` |

The whole run aborted **before** the 28 cloudflared-fronted hosts were checked —
they are enumerated separately and are unaffected by this file, but the crash
came first. No ntfy, no aq row, no state written: the only signal was a
`journalctl` traceback, while the timer stayed active/enabled and every daily run
failed the same way.

This matters for this file specifically because it is the one operator-edited
input: `README.md` says "Add them there" and `install.sh:31` never overwrites an
existing copy, so a hand-edit typo is the expected failure, not a hypothetical.

Making it worse, `install.sh --verify` checked the deployed file with
`vcontains "$DEST/targets.json" '"extra"'` — a literal substring grep that a
broken file passes, so ops-deploy-drift never flagged it.

## The fix

`ops/wq2092-cert-watch/cert_watch.py:113-139` — `load_extra_targets()` now
returns `(targets, error)`, mirroring `read_cloudflared_hosts()`. A missing file
is still the normal empty case (`([], None)`); a parse failure or a wrong shape
returns `([], "targets.json ...")` after explicit validation of the top level,
of `extra`, of each entry, and of each `host`/`port`, with
`(ValueError, KeyError, TypeError, AttributeError)` as the backstop.

`cert_watch.py:271-286` — `run()` prints the error every run and fires one
deduped ntfy (`cert-watch: targets.json malformed, ignoring extra targets` /
"cloudflared hosts are still checked"), the same dedup pattern as `cf_error`,
keyed on a new `targets_error` field in the saved state (`cert_watch.py:310-311`).
The cloudflared-derived targets are then checked as usual.

`install.sh:19-26` — `--verify` now parses the deployed file with the shipped
loader instead of grepping it, so a broken `targets.json` fails deploy-verify and
reaches ops-deploy-drift. Still read-only: `load_extra_targets()` only opens the
file.

## Evidence

- `python3 -m unittest discover -s ops/wq2092-cert-watch -p test_cert_watch.py`
  → **31 tests, OK** (was 29). New: `MalformedTargetsTests` (trailing comma,
  empty file, top-level list, `extra` not a list, entry not an object, entry
  missing `host`, non-numeric port, plus a well-formed control) and
  `RunTests.test_malformed_targets_json_still_checks_cloudflared_hosts`, which
  asserts `run()` returns 0, fetches **both** cloudflared hosts, ntfys once,
  persists state, and does not re-page on the second run.
- Falsification probe (`/tmp/fworker-wq3008/probe.py`, exit 0):
  - P1 replays the pre-fix loader body against a trailing-comma file →
    `JSONDecodeError` raised (so the new tests genuinely fail on the old code);
    the post-fix loader returns `([], 'targets.json unparseable: ...')`.
  - P2 loads the shipped `ops/wq2092-cert-watch/targets.json` read at the pinned
    merge-base `40073556` (not the mutable working copy) → `([], None)`.
  - The `--verify` fragment run against a malformed file prints
    `targets.json unparseable: Illegal trailing comma...`, i.e. it would
    `_vmiss` → `VERIFY FAIL`.
- Re-deployed and verified on Lumen via the ISSUE-LOG #975 workaround
  (`bash` is not on the worker allowlist):
  `lumen-run start --root -- bash .../install.sh` → `DONE:0`, then
  `lumen-run start -- bash .../install.sh --verify` → `DONE:0`, log `VERIFY OK`.
- Live dry-run of the deployed copy
  (`python3 /home/lumen/services/lumen-cert-watch/cert_watch.py run --dry-run`,
  unit `lumen-job-wq3008-dryrun-1790603221`) → `DONE:0`, 28 `cert-watch: <host>
  tier=...` lines.
