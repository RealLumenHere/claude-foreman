# wq#4411 — verify-pass: wq#4407 [rotation] burned credential sha256:5983bbc098a4 (kv-secret)

**Verdict: CONFIRM** — the burned value is not a credential; wq#4407's "nothing rotated"
settlement is correct, and its own probe (`mask_replay.py 4405`) reproduces cleanly today.

No PR/diff exists for wq#4407 (its own `wq show 4407` notes say "No PR (no code change)."
This is a pure triage/ledger ticket, not a code-shipping one — there is nothing to re-
implement or diff-review. The thing to verify is the *claim*: that sha256:5983bbc098a4 is a
runtime UUID marker, not a secret, and that settling it with a ledger entry (no rotation)
was the right call.

## What wq#4407 did

Ran `mask_replay.py 4405`, found the kv-secret hit traces to a pytest traceback printing
`token = '<12 hex>'`, identified the value as `uuid.uuid4().hex[:12]` generated at
`plugins/limpet-plugin-remote-cli/mcp/server.py:242`-ish (a per-call exec completion-marker
nonce), concluded the plugin has no auth secret, and recorded
`NOT ROTATED - not a credential` in `/home/lumen/foreman/rotated_ledger.json` keyed on the
hash. It also pointed at wq#4409 (deps on wq#4405) to close the leak path by renaming the
`token` variable (whose literal name is what trips the `kv-secret` mask pattern) to `nonce`.

## Verification performed

1. **Re-ran the probe cold**: `python3 /home/lumen/foreman/harness/mask_replay.py 4405` →
   exit 0, "6 credential-shaped values in the run's model context, 0 unaccounted." 5 hits
   `[tracked]` to committed `tests/test_server.py` lines; the 6th
   (`sha256=5983bbc098a4`) shows `[ROTATED] ... rotated : 2026-10-19T21:42:51Z (4407)` and
   the script's own summary: "No rotation: every value is committed text, or a string
   today's masking.PATTERNS no longer reads as a credential." Matches wq#4407's claim
   exactly — this is an acceptance probe that could fail (nonzero "unaccounted" would have
   falsified it) and didn't.
2. **Checked the ledger entry directly**: `rotated_ledger.json`'s record for
   `5983bbc098a4` (ticket `"4407"`, `2026-10-19T21:42:51Z`) states the same conclusion as
   the `wq show 4407` notes, word-for-word consistent — no drift between ticket notes and
   the actual settlement artifact.
3. **Checked the raw mask-hit evidence**: `/home/lumen/foreman/_runs/wq4405/mask-hits-1.json`
   context field is `command = 'false; echo boom', token = 'MASKED'` inside
   `def capture(command, token): token_holder["token"] = token; text = real_payload(...)` —
   a pytest fixture/monkeypatch wrapper capturing real call args, consistent with "printed
   as a funcarg line in a failing-test traceback," not a hardcoded secret literal.
4. **Read the actual code**: `server.py:345` — `nonce = uuid.uuid4().hex[:12]`, generated
   fresh inside the exec-handling function on every call, used only to build
   `RCSTART-<nonce>` / `RCDONE-<nonce>-<status>` regexes for correlating one command's
   output in a shared PTY's scrollback (`_exec_marker`/`_exec_start_marker`, :261-266).
   Never compared against a stored value, never used to authorize anything — confirms "no
   auth secret" rather than taking the claim on faith.
5. **Searched the whole plugin for an auth mechanism**: only 4 files exist
   (`limpet-plugin.toml`, `mcp/server.py`, `tests/test_server.py`,
   `tests/fixtures/remote_cli_protocol.json`). `grep -i 'auth|secret|password|api_key|credential'`
   across `server.py` returns only docstring prose (PTY passwords a human might type) and
   wq#4409's own comment explaining the mask-pattern collision — no credential-checking
   code anywhere. `limpet-plugin.toml`'s `[mcp] env = {}` — no secret is even configured in.
   the plugin's own manifest. Access control is the Unix-socket file permissions on
   `/run/remote-cli/shared.sock`, not a token.
6. **Confirmed the cited leak-path fix actually landed**: wq#4407's note says the leak path
   (variable literally named `token`, which the `kv-secret` mask pattern matches on
   `token = '<12 hex>'`) is "closed by wq#4409 (rename token->nonce)." `git log --oneline`
   in `/home/lumen/foreman` shows `e45bc7a1 fix(limpet): wq4409 rename remote-cli exec
   marker token -> nonce`, confirmed on `main` via `merge-base --is-ancestor`. Post-rename,
   `grep -n "token = " tests/test_server.py` returns nothing — the exact string shape that
   fired the mask is gone from the tracked tree, matching this run's "0 unaccounted."

## Attempts to break it

- Checked whether the hash could instead match a real secret coincidentally sharing the
  `token = '<12 hex>'` shape: not falsifiable by inspecting the hash itself (sha256 doesn't
  reverse), but the *context* around the hit (a pytest capture wrapper forwarding real call
  args into `real_payload`) only makes sense if the value passing through is whatever the
  code under test actually generated at runtime — i.e. the nonce from `uuid.uuid4()` at
  line 345, not an operator-typed secret. No other code path in the 4-file plugin produces
  a 12-hex-char value.
- Checked for a second, independent auth mechanism outside `server.py` (e.g. a wrapper
  script, systemd env, or the `remote-cli` binary itself) that might consume this value as
  a credential: out of scope for this worktree's allowed dirs (the `remote-cli` binary
  source isn't under `/home/lumen/foreman`), but the MCP manifest's `env = {}` and the
  plugin's own comment ("the fleet's kv-secret mask fires on `token = '<12 hex>'`, and a
  pytest traceback prints exactly that for this value") are first-party confirmation from
  the same author who wrote the exec protocol that this is a correlation nonce, not a
  secret the `remote-cli` daemon checks.
- Re-ran the exact probe wq#4407 cites rather than trusting its reported output — it
  reproduced identically (exit 0, 0 unaccounted, same `[ROTATED]` line), so this isn't
  resting on the original run's self-report.

No contradiction found. The ticket's "no PR" framing is correct for this archetype (no code
diff exists to adversarially review); the thing that needed adversarial pressure — the
claim itself and its probe — held up under independent re-derivation.
