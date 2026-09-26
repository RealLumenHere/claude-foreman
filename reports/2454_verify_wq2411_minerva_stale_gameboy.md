# wq#2454 — verify of wq#2411 (PR 873, d8f404c / squash 9989917)

Verifier: claude-sonnet-5@high (fresh instance). Verdict: **CONFIRMED**.

## Per claim

| wq#2411 claim | Result | Evidence |
|---|---|---|
| `gameboy.yaml:9` correctly reads `tailscale_ip`, not `ts_ip` | **CONFIRMED (indirect)** | rename-vs-supersession fix `d2bc710` (wq#1782) already independently verified by `373e238`/`1313f9e` (wq#2194) |
| Live scanner binary was stale (pre-fix, installed 09-20 16:42) | **CONFIRMED** | snapshot `_runs/wq2411/snapshots/20260924T111351Z-home_lumen_bin_lumen-contradiction-scan` is 41488 bytes, mtime 09-20 16:42; repo copy at the time was 42242 bytes |
| `install.sh` aborts before the scanner copy reaches live because the rest needs sudo | **CONFIRMED** | `install.sh:31-34` (pre-fix) runs `sudo install …` unconditionally right after the copy; a build worker has no sudo grant, so `set -euo pipefail` kills the whole script there |
| `--script-only` exits right after the unprivileged copy, before any `sudo` line | **CONFIRMED** | `ops/wq1609-minerva-contradiction-scan/install.sh:27-29`, sits after line 20's `install -m 0755 … /home/lumen/bin/lumen-contradiction-scan` and before line 31's first `sudo` |
| Live scanner binary now byte-matches the repo copy | **CONFIRMED (reproduced independently)** | `md5sum /home/lumen/bin/lumen-contradiction-scan /home/lumen/foreman/ops/wq1609-minerva-contradiction-scan/lumen_contradiction_scan.py` → identical hash `3df59cdb…`, both 42242 bytes |
| Rescan confirms the `gameboy.yaml:9` hit is gone | **CONFIRMED (reproduced independently)** | ran `python3 lumen_contradiction_scan.py --json` fresh; `grep -c gameboy.yaml` on the output → 0 hits. One unrelated `gameboy-pc` archival-note hit remains (`power_plug`/E1, a different fact entirely) |
| Suite still passes | **CONFIRMED** | `python3 -m pytest -q test_lumen_contradiction_scan.py` → 54 passed |
| PR actually merged, diff matches what's on `main` | **CONFIRMED** | squash commit `9989917` is on `origin/main` (event log: `pr-merge … 873 squash`, exit 0); `git diff d8f404c 9989917 -- install.sh` is empty |

## Attempts to break the fix

- **Default (no-arg) invocation regressed?** No — the new branch only fires on `"${1:-}" = --script-only`; every other call path (bare, `--verify`) is byte-identical to before. `--verify` is handled in an earlier, separate `if` block (line 8) that exits via `vdone` before reaching the new code, so there's no ordering collision between the two flags.
- **Other callers accidentally passing `--script-only` and silently losing the systemd install?** Grepped the repo for other invocations of this `install.sh`; found none that pass a first positional arg, so no caller's behavior changes.
- **Scope creep / bypassing a real privilege boundary?** The exited-early path only ever touches a user-owned file (`/home/lumen/bin/lumen-contradiction-scan`, mode 0755, owned by `lumen`); it does not touch `/etc/systemd` or use `sudo` anywhere on the new path. No privilege escalation introduced.
- **Redeploy correctness**: compared the actual pre/post binary from the `_runs/wq2411/snapshots/` directory (mtimes 09-20 16:42 → 09-24 11:13) against the repo source at merge time — sizes and content converge exactly as claimed, not just asserted in the commit message.

No contradiction found. Confirming on the board.
