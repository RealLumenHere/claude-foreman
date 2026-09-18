# wq#1973 verify-pass: wq#1968 [secrets] rotate the 15 LiteLLM virtual keys leaked to Bes logs

Diff verified: commit `aa635c3` (claude-foreman/foreman, `main`, PR#602), files
`ops/wq1747-litellm-key-rotation/{README.md,rotate.py,rotate.sh,test_rotate.py,
never-restart-units.txt}` plus the bundled one-liner
`ops/wq1698-supergateway-log-redaction/no-body-logs-journald.conf` fix. Read
cold, ran the author's own probe, then wrote an independent adversarial
reproduction (no live proxy/systemd needed — same fake-HTTP-server + mocked
`subprocess.run` technique the author's own suite uses).

## What checked out

- `python3 -m pytest test_rotate.py -v`: 7/7 pass, matching the author's claim.
- Credential hygiene claims hold: master key only ever read from
  `LITELLM_MASTER_KEY` env, `_request`/`verify_key` use `urllib` in-process
  (never a `curl` subprocess, so no secret in argv/`ps`), only masked 6-char
  prefixes/paths/unit names are ever printed. Grepped the whole file for
  `print(` / f-strings near `new_value`/`old_value`/`raw_key` — none leak the
  raw value.
- Order of operations is correct and race-safe: `find_files_with_value` runs
  **before** `regenerate_key`, so the file list used for rewriting is captured
  while the old key is still valid; `--dry-run` returns before any mutation.
- Idempotency: `already_rotated` keyed on the *new* token sha256 (not the
  request), matches `test_rotate_one_happy_path`'s second-call assertion —
  confirmed by reading the state-write timing, not just trusting the test.
- `never-restart-units.txt` is empty by default with a correct note that Thor
  (CLAUDE.md instinct 2) is a host-level rule this script has no path to
  anyway (it only restarts units on the box it runs on) — accurate, not a
  shortcut.
- The bundled `wq#1698` journald conf fix (`StandardError=journal`) matches
  its own README's stated intent (keep `logger.error`/transport diagnostics
  in journald, only body/chatter to the file) — correct one-line diff.

## Contradiction: restart-scope detection is broken for any `--user` consumer

`rotate_one` (rotate.py:326-338) restarts each affected unit via:

```python
is_user = any(str(f).startswith(str(Path.home() / ".config" / "systemd")) for f in files)
restart_unit(unit, is_user, run_id, args.dry_run)
```

`is_user` is computed from the paths of the **rewritten env files** (`files`,
e.g. `~/.secrets/litellm-service-keys/env/<alias>.env` — the exact path
pattern already in use on this fleet, per
`ops/wq1594-ops-emergency-recurring-job/agent5-dgm-key.conf:31`), not from
which `unit_dirs` entry the matching unit was actually found under
(`map_file_to_units`, rotate.py:209-230, merges system-unit-dir and
user-unit-dir hits into one undifferentiated `units: set[str]`, discarding
scope). Since env files live under `~/.secrets/...`, never under
`~/.config/systemd`, `is_user` is False for every real alias regardless of
whether the consuming unit is loaded in the user manager — the default
`unit_dirs` (rotate.py:387) explicitly includes
`~/.config/systemd/user`, so the script itself anticipates `--user` consumers
existing, but can never correctly restart one.

Reproduced in isolation (fake proxy + monkeypatched `subprocess.run`, no live
host, mirrors `test_rotate.py`'s own harness): alias `lumen-agent-2` (one of
the 15 target aliases, `rotate.py:50`), key stored at
`~/.secrets/litellm-service-keys/env/lumen-agent-2.env`, consumed by a
`--user` unit `~/.config/systemd/user/lumen-agent.service`. `rotate_one`
correctly finds the unit (`units_restarted == ["lumen-agent.service"]`) but
issues:

```
['systemd-run', '--collect', '--unit=rotate-...', 'systemctl', 'restart', 'lumen-agent.service']
```

— system-manager scope, no `--user`. Repro script:
`reports/1973_verify_wq1968_repro_restart_scope.py` (this PR). Core
assertion: `"--user" not in restart_calls[0]` even though the only
`unit_dir` searched was the user one.

**Impact**: the key has already been regenerated on the proxy (step 3) and
rewritten on disk (step 4) by the time `restart_unit` runs. If the real unit
is `--user`-scoped, `systemctl restart <unit>` (system manager) either errors
("Unit not found", surfacing as `subprocess.CalledProcessError` — uncaught
inside `rotate_one`, propagates to `main`'s per-alias try/except, which marks
the alias `ERROR` and moves on) or, worse, silently no-ops/targets an
unrelated same-named system unit if one happens to exist. Either way the real
consumer keeps running with the just-invalidated old key. Because the
exception fires *before* `record_state` (rotate.py:344, after the restart
loop), the idempotency marker is never written — a retry does not just fix
the restart, it finds the *new* value already on disk, calls `/regenerate`
on it again, and repeats the same wrong-scope restart, thrashing the key on
every retry instead of converging.

This is not a hypothetical corner case: `agent5-dgm.env`'s sibling pattern
(same `~/.secrets/litellm-service-keys/env/` directory convention) is already
live on this fleet, and the default `unit_dirs` including
`~/.config/systemd/user` means the author intended `--user` units to be in
scope for this exact rotation — it's the standing pattern for at least
several of the 15 aliases per how Lumen's per-service key files are laid out,
not an exotic layout invented for this test.

## Fix shape (for the escalation ticket, not applied here — verify-only)

Track scope per-unit instead of per-alias: have `map_file_to_units` return
`(unit_name, is_user)` pairs (or two separate sets, one per `unit_dirs`
entry) instead of collapsing to `set[str]`, and pass each unit's own scope
into `restart_unit` — not a global heuristic over the env-file list.

## Verdict

CONTRADICTED (not a clean confirm). Credential hygiene, ordering/race safety,
idempotency, the never-restart-list/Thor note, and the bundled journald fix
all hold up under adversarial testing. But the restart step — the mechanism
that makes the rotation actually take effect for the consuming service,
which is the entire point of the ticket — has a real, reproduced-without-a-live-host
defect that can strand a `--user`-scoped consumer on an already-invalidated
key and thrash on retry. Per protocol, escalating to opus-5@high: wq#1977
(lane=build, pri=1, deps=1968).
