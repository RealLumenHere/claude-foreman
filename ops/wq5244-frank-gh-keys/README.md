# wq#5244 — Frank's GitHub SSH key missing on thor, maksoor, ptah

Source: LRA Phase 2 (wq#4999) read-only audit, 2026-11-21: each of thor, maksoor, ptah
lacks one of the keys `ssh-import-id gh:FrankStupak` would install.

## Key set (pinned 2026-11-22)
`curl https://github.com/FrankStupak.keys` returns exactly 2 keys, both ed25519
(matches `/home/lumen/bin/fleet-bootstrap:24` "returns 2 keys"):

| short | tail |
|---|---|
| K1 | `...o/pGtPYYBBGR` |
| K2 | `...meFRE3akAtS8` |

Both are pinned in `fix-frank-gh-keys.sh` (`PINNED_KEYS`). Public keys, not credentials.

## Fix
`fix-frank-gh-keys.sh` — run on Lumen (uses Lumen's fleet key to each node):

```
ssh lumen@100.117.66.102 'bash /home/lumen/foreman/ops/wq5244-frank-gh-keys/fix-frank-gh-keys.sh'
ssh lumen@100.117.66.102 'bash /home/lumen/foreman/ops/wq5244-frank-gh-keys/fix-frank-gh-keys.sh --apply'
```

- Dry run (default) prints `present`/`MISSING` per key per node — that is the
  "which key is missing on each node" answer.
- `--apply`: add-only. Backs up `~/.ssh/authorized_keys` to
  `authorized_keys.bak.<ts>_wq5244`, appends only the missing key(s) tagged
  `gh:FrankStupak wq5244`, keeps 0600, re-verifies, never deletes a line.
- Fail-closed: if the live GitHub key set differs from the pinned set (key added/removed
  on Frank's account, or fetch failed), exits 3 and writes nothing.
- Targets (lumen_state 2026-11-22): thor `thor@100.112.151.100`, maksoor
  `maksoor@100.81.204.74`, ptah `lumen@100.78.207.56`. One node: append its name.
- Thor: appending to authorized_keys restarts/interrupts nothing (Tier-0 #2 is about
  restarts only).

Equivalent manual fix per node is `ssh-import-id gh:FrankStupak` (also add-only and
idempotent); the script exists to show the missing key first and to pin the key set.

## Why the worker did not apply it (wq#5244 a1)
The build worker has no route to these nodes: `ssh` is not on the worker Bash allowlist,
`fleet-exec run thor ...` → `DENY [grant:grant_missing_env]` (ticket `needs:` empty), and
even with a grant `harness/fleet-exec-policy.yaml` lists thor under `never.nodes` and
maksoor/ptah as READ-ONLY `run_readonly_cmds` — no authorized_keys write verb exists.
So apply is an operator step from a Lumen shell (orchestrator/Commander session), not a
Frank physical act.

## Test
`pytest -q ops/wq5244-frank-gh-keys/test_fix_frank_gh_keys.py` — runs the remote body
locally against scratch `$HOME`s with an offline key list: dry-run reports + no write,
apply adds only the missing key with backup and 0600, re-apply no-op, empty home gets
both, drift aborts rc=3 with nothing written, unknown node rejected.
