# wq#5248 — apply ops/wq4790-claude-context-sh-into-bin (post wq#4933)

## Finding

`install.sh --verify` failed with `MISS differs: /home/lumen/bin/claude_context.sh`
because wq#4933 (commit c8ee3daa) merged the host guard into the bundle's
`claude_context.sh` (+45 lines) but nothing deployed it. The live copy was the
2026-11-19 version (7159 bytes) without `host_guard`.

The ticket said "needs privileged install". It did not: the target
`/home/lumen/bin/claude_context.sh` is `lumen:lumen 0755`, and so is `/home/lumen/bin`.
The ops-root-apply template assumes root for any failing `--verify`. ISSUE-LOG row 1702.

## Action (2026-11-22 06:56 UTC)

1. Backup: `cp /home/lumen/bin/claude_context.sh /tmp/fworker-wq5248/claude_context.sh.bak.20261122`
2. Tests on the bundle first: `python3 -m pytest -q ops/wq4790-claude-context-sh-into-bin/test_host_guard.py`
   gave 8 passed.
3. Deployed the same way install.sh does it (atomic, so no running reader sees a
   half-written file, wq#2856): cp to `claude_context.sh.tmp.wq5248`, `chmod 0755`,
   `mv -f` over the target.

## Evidence

- `sha256sum` of live and bundle: both `097a3669c153664a7a9b43a0c219b2be644bf4bdcf71691ab90d6122fa7e2d23`.
- `lumen-run start -- bash /home/lumen/foreman/ops/wq4790-claude-context-sh-into-bin/install.sh --verify`
  printed `VERIFY OK` with `LUMEN_JOB_EXIT=0` (unit lumen-job-job-1795330597).
- `lumen-run start -- bash /home/lumen/bin/claude_context.sh --host-check` printed
  `===== HOST: lumen (user lumen) — expected Commander carrier =====`, exit 0.

## Rollback

`cp /tmp/fworker-wq5248/claude_context.sh.bak.20261122 /home/lumen/bin/claude_context.sh.tmp && mv -f /home/lumen/bin/claude_context.sh.tmp /home/lumen/bin/claude_context.sh`
