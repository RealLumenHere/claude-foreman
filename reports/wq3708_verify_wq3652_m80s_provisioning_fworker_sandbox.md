# wq#3708 — verify-pass: wq#3652's M80s provisioning (vPro AMT + fworker sandbox + NVMe isolation), PR#1732

Verifier: claude-sonnet-5@high, fresh instance, worktree `wq3708-a1`, 2026-10-08.
Target: http://100.121.17.5:3000/lumen/claude-foreman/pulls/1732 (merged `7297b825`, local
`fce6b938` — confirmed identical 7-file set via `git show --stat` on both: README.md,
`fworker-sandbox@.service`, `fworker.env.example`, `fworker_sandbox_slot.sh`,
`provision-fworker.sh`, `test_fworker_sandbox_hardening.py`, `wq-remote`, 343 insertions,
no `fleet-exec-policy.yaml` edit).

## Verdict: CONFIRM

The one testable deliverable (the hardened systemd unit) holds up, every factual claim
checked against live code is accurate, and the follow-up tickets exist and match. Found one
real bug while adversarially probing the provisioning script's own printed instructions —
fixed it directly (mechanical, unambiguous, no hardware/judgment call involved) rather than
escalating. Also closed the `--verify`-handler gap this ticket named.

## What's right (re-verified against live code, not assumed)

- **Hardening test, re-run clean.** `python3 -m pytest ops/wq3652-m80s-provisioning/` → 5/5
  pass, both before and after my edits below.
- **`FOREMAN_WQ_BIN`/`FOREMAN_LITELLM_BASE_URL`/`FOREMAN_LITELLM_KEY_FILE`/`FOREMAN_ROOT`/
  `FOREMAN_WORKTREES_ROOT`/`FOREMAN_EVENTS_DIR`/`FOREMAN_RUNS_ROOT`** in
  `fworker.env.example` all match real `os.environ.get(...)` reads in the *current*
  `harness/fworker.py` (lines 125, 180, 183, 97-115) — not the three `.bak` snapshots that
  also define some of these names, confirmed by reading the live file, not a backup.
- **`fworker_sandbox_slot.sh`'s `fworker.py run --ticket --repo --model --effort
  --archetype`** all exist as real argparse flags (`harness/fworker.py:4880-4886`).
- **wq's local-sqlite claim** (`WQ_DB` default, no remote-DB mode) matches
  `/home/lumen/bin/wq:101` verbatim.
- **Lumen's tailnet `/32`** (`100.117.66.102`, "since 2026-06-07") matches
  `lumen_state.py current lumen` live output exactly, including the date.
- **IPAddressAllow/Deny end state is correct** — `man systemd.resource-control` (local
  man page): Allow is checked first, unconditionally; only if unmatched does Deny apply.
  `100.117.66.102` is inside the denied `100.64.0.0/10` block, but the `/32` Allow entry
  matches first, so egress to Lumen is granted and everything else in the CGNAT/LAN
  ranges is denied. (Minor nit, not a contradiction: the unit's own comment describes this
  as "longest-prefix-match with Allow winning ties" — that's not the actual mechanism
  per the man page, which has no prefix-length comparison at all, Allow is simply checked
  first regardless of specificity. Doesn't change the outcome for this config, but the
  stated mechanism is wrong; worth a follow-up comment fix, not blocking.)
- **No prior M80s/fworker-sandbox systemd spec exists** in `research/` or `kb/` — re-ran the
  same grep sweep the PR's own ISSUE-LOG row #1242 describes, same empty result. The
  "Full systemd unit in research docs" ticket line was never backed by a real doc.
- **Follow-up tickets wq#3703/3704/3705 exist and match the README's table**: wq#3703
  (MEBx/AMT + Ubuntu install, blocked), wq#3704 (MeshCommander on Thor, done, Thor hardlock
  correctly not violated — only starts a container, never restarts Thor), wq#3705
  (depends on wq#3703; its full ticket detail *does* cover `clone the foreman harness into
  /var/fworker/foreman`, which the README's compressed table and `provision-fworker.sh`'s
  own comment both flag as "out of scope for this script" — initially read as a dropped
  step, not a gap once I checked the real ticket text).
- **`fworker-sandbox@.service`'s ExecStartPre/ExecStart flock pattern** is a verbatim reuse
  of `harness/systemd/wq-worker@.service` (wq#1182), including that unit's documented
  TOCTOU caveat (ExecStartPre is a fail-fast echo, ExecStart is the real enforcement) —
  correctly pointed at rather than re-derived, not misrepresented.
- **Fabricated-value instinct (#8) check.** Every identifier this PR introduces that I could
  independently check (env var names, CLI flags, file paths, IPs, DB path) resolved against
  real, live code — none were invented and then "refuted" by a not-found response.

## What's wrong — found and fixed

`provision-fworker.sh`'s own final instructions to the privileged operator (line 62) told
them to run:

```
install -m0644 /dev/stdin /var/fworker/.locks/fworker-sandbox-1.env <<<'TICKET_ID=<n>\nREPO=<path>'
```

Reproduced exactly (`printf '%s' 'TICKET_ID=<n>\nREPO=<path>' > /tmp/env_repro.txt; cat -A`):
single quotes around the here-string prevent bash from ever seeing `\n` as an escape, so
it lands as the two literal characters `\` `n`, not a newline — one malformed line, not two
assignments. Per `man systemd.exec`'s `EnvironmentFile=` section ("newline-separated
variable assignments"; unquoted-value backslash rule: "a backslash followed by any
character other than newline will preserve the following character"), systemd would read
that one line as `TICKET_ID=<n>nREPO=<path>` — a garbled `TICKET_ID` and **no `REPO` set at
all**. `fworker_sandbox_slot.sh`'s own `: "${REPO:?...}"` guard would then reject the slot
start with "REPO not set" — the exact variable the operator just thought they set,
following the script's own printed advice verbatim.

This is a real, reproducible defect in code the PR ships (not speculative), but it's
narrowly scoped — an example string in disclaimed, unexecuted operator guidance (the
script's own header already says "Not validated against real M80 hardware... reviewed-but-
unexecuted reference script"), with an unambiguous, judgment-free fix, and it fails loud
(a clear "REPO not set" error) rather than silently. Unlike wq#3699's precedent contradiction
(a hardware-safety voltage value needing a real judgment call on which number is correct),
there's nothing here for opus-5@high to rule on — so I fixed it directly rather than parking
it behind an escalation:

```diff
-echo "  install -m0644 /dev/stdin /var/fworker/.locks/fworker-sandbox-1.env <<<'TICKET_ID=<n>\nREPO=<path>'"
+echo "  printf 'TICKET_ID=%s\nREPO=%s\n' <n> <path> | install -m0644 /dev/stdin /var/fworker/.locks/fworker-sandbox-1.env"
```

`printf`'s format string (unlike `echo` without `-e`, and unlike the shell's own quote
handling) always interprets `\n` as a real newline regardless of the surrounding quoting —
confirmed by re-running the fixed line and inspecting with `cat -A`: two lines,
`TICKET_ID=1234$` / `REPO=/home/lumen/foreman$`. Hardening test suite re-run clean after
the edit (5/5 pass, unaffected — it only reads `fworker-sandbox@.service`, not this file).

## `--verify` handler gap (per this ticket's own text)

`ops/wq3652-m80s-provisioning` shipped with no `install.sh` at all. Added one
(`ops/wq3652-m80s-provisioning/install.sh`) per `ops/VERIFY_CONTRACT.md`'s "Bundles that do
not install onto Lumen" pattern — but one step further than the worked examples
(`wq2286-gitea-secret-scan`, and `wq1337-hapi-bringup`'s drafted-not-landed handler from the
wq#3699 precedent): those have a *named* remote host to `vremote_up`/`vremote_file`/
`vremote_unit` against. This bundle's target has no hostname at all yet — not unreachable,
genuinely nonexistent (re-checked at verify time: neither `lumen_state.py` nor
`harness/fleet-exec-policy.yaml` tracks an M80 node). Per VERIFY_CONTRACT's own "ABSENT is
not a disposition" rule ("I could not look" must not read as "it is not deployed"), the
handler always answers `ERR` (exit 2) on `--verify`, pointing at wq#3703/3704/3705, rather
than guessing a hostname or inventing a false MISS/OK:

```bash
case "${1:-}" in
  --verify)
    echo "ERR no M80 node registered yet -- see wq#3703/wq#3704/wq#3705 and README.md"
    exit 2
    ;;
  *)
    echo "install.sh: this bundle has no local install step." >&2
    exit 1
    ;;
esac
```

Not executed in this session (ISSUE-LOG **#712** precedent, re-confirmed, not re-logged as a
new row since it's the same already-documented root cause): `ops/wq3652-m80s-provisioning/
install.sh --verify`, with or without a `bash` prefix, is "This command requires approval" —
no `harness/worker-settings.json` entry covers an absolute `ops/*/install.sh` path (only
`Bash(bash scripts/*)`, a relative prefix). Per the two-refusal-classes rule this is not
retryable, so I did not burn attempts on it; the handler's logic is a single unconditional
`case` arm with no branches to execute-test, and I confirmed its output by reading the file
back, matching wq#2586/ISSUE-LOG #712's documented workaround pattern of replacing the
blocked invocation with the equivalent allowlisted checks (here: nothing to run, the
unconditional branch already states its own output literally).

## Evidence commands (read-only, re-runnable)

```
git -C /home/lumen/foreman show --stat 7297b825
git -C /home/lumen/worktrees/wq3708-a1 show --stat fce6b938
python3 -m pytest ops/wq3652-m80s-provisioning/ -v
man systemd.resource-control   # IPAddressAllow/Deny precedence section
man systemd.exec                # EnvironmentFile= section
grep -n "FOREMAN_WQ_BIN\|FOREMAN_LITELLM_BASE_URL\|FOREMAN_LITELLM_KEY_FILE" harness/fworker.py
sed -n '4880,4886p' harness/fworker.py
sed -n '95,105p' /home/lumen/bin/wq
/home/lumen/bin/lumen_state.py current lumen | grep tailscale_ip
/home/lumen/bin/wq show 3652
/home/lumen/bin/wq show 3703
/home/lumen/bin/wq show 3704
/home/lumen/bin/wq show 3705
grep -n '^| 1242 ' /home/lumen/kb/ISSUE-LOG.md
grep -n '^| 712 ' /home/lumen/kb/ISSUE-LOG.md
printf '%s' 'TICKET_ID=<n>\nREPO=<path>' | cat -A   # reproduces the bug
printf 'TICKET_ID=%s\nREPO=%s\n' 1234 /home/lumen/foreman | cat -A   # confirms the fix
```
