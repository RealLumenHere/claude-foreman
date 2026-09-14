# wq#1928 — Adversarial verify-pass of wq#1926 (lumen-kernel-watch timer for the Linux 7.3 silent data-loss fix, PR#592)

**Verdict: CONFIRM.** PR#592 (single commit `fc7ea8a`, 5 new files under
`ops/wq1926-kernel-watch/`) does exactly what it claims: a read-only daily timer that
compares the running kernel against Ubuntu 26.04's apt candidate and alerts via
`ntfy-send` only once the candidate carries the Linux 7.3-rc3 fix (>=7.3.0) and the
running kernel doesn't. No auto-upgrade, no reboot path anywhere in the diff. The unit is
already installed and live on Lumen (not just claimed) and its first real run matches the
script's own logic exactly.

## What was checked

- **PR scope** — `git fetch origin refs/pull/592/head` → `fc7ea8a`, diff-only against its
  parent: `ops/wq1926-kernel-watch/{README.md,install.sh,lumen-kernel-watch,
  lumen-kernel-watch.service,lumen-kernel-watch.timer}`, 180 lines, all new files. (The
  later `6fc711e` on `main` — "track fix, alert on landing" — is a separate follow-up
  commit, not part of this PR; out of scope for this pass.)
- **Core logic, read cold** (`lumen-kernel-watch:21-58`) — `kernel_version()` regex-extracts
  the first `\d+.\d+.\d+` from `uname -r` / `apt-cache policy` output; alerts iff
  `candidate_version >= (7,3,0) and installed_version < (7,3,0)`. Malformed/missing input
  (no candidate, `apt-cache` failure) falls back to `(0,0,0)`, which fails toward *no
  alert*, not a false alarm — correct fail-safe direction for a notify-only tool.
- **Ran the actual probe live**, not trusted from the diff: `python3
  ops/wq1926-kernel-watch/lumen-kernel-watch` →
  `OK running=7.0.0-30-generic candidate=7.0.0-31.31 (needs candidate>=7.3.0 and
  installed<7.3.0 to alert)`. Correct: candidate hasn't reached 7.3.0 yet here, no alert.
- **Simulated the alert branch** (the untested path — production won't hit it until the
  fix actually lands in the Ubuntu candidate) by loading the script via
  `importlib.machinery.SourceFileLoader`, monkeypatching `apt_candidate()` to return
  `"7.3.0-13.13"` and `NTFY_BIN` to `/bin/echo`: produced
  `-p high -T Kernel data-loss fix available Kernel update needed: running
  7.0.0-30-generic is older than 7.3.0 ... Candidate: 7.3.0-13.13 ... no auto-reboot.` —
  triggers correctly, message content accurate, no crash.
- **ntfy-send call shape** (`lumen-kernel-watch:51-54`) — cross-checked
  `[NTFY_BIN, "-p", "high", "-T", title, msg]` against `/home/lumen/bin/ntfy-send`'s own
  usage line (`-t topic -p priority -T title "message"`) and against the house convention
  the README cites verbatim: `ops/wq1373-cf-token-liveness/cf_token_liveness.py:52,181` —
  both cited line numbers checked and accurate (`NTFY_BIN = ...` at 52,
  `subprocess.run([NTFY_BIN, "-p", priority, "-T", title, message], check=False)` at 181).
  Same call shape, same convention.
- **install.sh / unit files** — idempotent (`install -m`, `systemctl enable --now`,
  daemon-reload), self-guarding (fails loudly if not root, matching README's documented
  `lumen-run --root` deploy path), directory layout matches sibling ops dirs
  (`wq1373-cf-token-liveness`, `wq1652-cfd-actuator`: README + install.sh + service +
  timer + script, no separate manifest to update).
- **Live deployment, independently confirmed** (not asserted by the PR — this system state
  predates my pass): `systemctl list-unit-files | grep kernel-watch` → both units present,
  timer `enabled`/`active (waiting)`, next trigger ~6h out. `diff
  /home/lumen/bin/lumen-kernel-watch ops/wq1926-kernel-watch/lumen-kernel-watch` →
  byte-identical to the PR. `journalctl -u lumen-kernel-watch.service` shows one clean
  oneshot run at install time: `OK running=7.0.0-30-generic candidate=7.0.0-31.31 ...`,
  `Deactivated successfully` — matches my own independent rerun exactly, no drift between
  install-time smoke test and current behavior.
- **Subprocess safety** — all `subprocess` calls use list-form args (`apt-cache`, `uname`,
  `ntfy-send`), no `shell=True`, no string interpolation into a shell command — no
  injection surface.

Nothing in the diff, the live unit, or the two probe runs (baseline + simulated alert)
contradicts the PR's own description. No re-implementation performed.

## Disposition

CONFIRM, no escalation to opus-5@high.
