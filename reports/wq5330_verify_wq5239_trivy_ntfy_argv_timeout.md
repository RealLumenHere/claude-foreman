# wq#5330 — verify wq#5239 (trivy scan.sh: ntfy pass off curl argv + TimeoutStartSec)

Target: PR #2781, commit `b219ff8b` ("fix(trivy): keep ntfy pass off curl argv, widen unit timeout").
Verdict: **CONFIRMED.** No contradiction found.

## What the diff does

- `ops/wq858-trivy-weekly-scan/scan.sh:70-78` adds a `ntfy_send` helper. It reads `NTFY_PASS` into
  a local variable that is not exported, escapes `\` and `"` for curl's config syntax, and pipes
  `user = "lumen:<pass>"` to `curl -s -K -` with the bash `printf` builtin. All three old
  `curl -u "lumen:$(…)"` call sites now go through it (`scan.sh:80-90`).
- `ops/wq858-trivy-weekly-scan/lumen-trivy-weekly.service`: `TimeoutStartSec=1800` becomes `4h`.
- `SHA256SUMS` is re-pinned, the README's known gaps move to a "Fixed by wq#5239" section, and the
  PR adds `ops/tests/test_wq5239_trivy_ntfy_argv.py`.

## Attacks run

| # | Attack | Result |
|---|---|---|
| 1 | Run the PR's probe: `python3 -m pytest -q ops/tests/test_wq5239_trivy_ntfy_argv.py ops/tests/test_wq4899_trivy_bundle.py` | 23 passed |
| 2 | Mutant: put back `curl -s -u "lumen:$pass"` in `ntfy_send` | **Killed.** 2 failed: `test_password_delivered_via_stdin_not_argv` (password on argv) and `test_scan_sh_has_no_curl_user_flag` |
| 3 | Mutant: keep `-K -` but remove the `\`/`"` escaping | **Killed.** The Authorization header was cut short at the first `"` (`Basic bHVtZW46cHdx` = `lumen:pwq`) |
| 4 | Live deploy: sha256 of `/home/lumen/services/trivy-weekly/scan.sh` and `/etc/systemd/system/lumen-trivy-weekly.{service,timer}` compared with `SHA256SUMS` on main | All three match (`99af1607…`, `f81479e3…`, `2bf6f475…`) |
| 5 | Live unit: `systemctl show lumen-trivy-weekly.service` | `TimeoutStartUSec=4h`, `NeedDaemonReload=no`, `ExecStartPost` is the cron-stamp drop-in. Timer is active and enabled; next run Sun 2026-11-29 09:00 UTC |
| 6 | Read for `set -euo pipefail` hazards in `ntfy_send` | Both the `$(grep…|cut…)` assignment and the `printf|curl` pipeline end in `|| true`. The test for a missing password line passes. `-d "$4"` never starts with `@`, so it cannot compete with `-K -` for stdin |
| 7 | Read for argv leaks | `printf` is a builtin. Its forked pipeline subshell carries the script's own argv, not the password. curl's argv is `-s -K - -H … -d … URL`. `grep`/`cut` argv hold the env path and key name only |
| 8 | Timeout budget check | The last report (`trivy_20261122_090053.txt`) scanned 21 images. Worst case: 21×5m + 2×45m + DB update ≈ 200m, which fits in 240m |

## What I could not check (not contradictions)

- **End-to-end auth against live ntfy.** guard.sh blocks reading `/home/lumen/agent/.env`
  (wq#2980), so I could not check that the live password, sent via `-K -`, gets a 2xx
  response. The local-server test covers the same byte path with a hostile password
  (backslash, quote, `=`, space). The first live send will be the 2026-11-29 run.
- **`install.sh --verify` did not run here.** It is not on the worker Bash allowlist. I
  re-checked its contract by hand: sha pins (attack 4), timer active, enabled and scheduled,
  and the drop-in present (attack 5). The author's ticket notes record `--verify OK` after
  the deploy.

## Low-risk observations

- `grep -m1 NTFY_PASS` is not anchored. A commented or `NTFY_PASS*`-prefixed line above
  the real key would be used instead. This predates the PR (the old code had the same
  unanchored grep), and the `.env` cannot be inspected from a worker. Anchoring it to
  `'^NTFY_PASS='` would be a cheap hardening.
- The 4h budget leaves about 40 min of headroom at 21 images, roughly 8 more images at
  the 5m worst case. If the image count grows a lot, the budget needs revisiting.
- The commit carries a `Co-authored-by: Lumen Industries` trailer. The last 30 foreman
  commits all have it, so the tooling adds it. It is not this author's deviation.
