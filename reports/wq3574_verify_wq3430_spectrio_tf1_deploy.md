# wq#3574 verify: wq#3430 [spectrio] Deploy 5 TF1 single-purpose appliances

Archetype 8 adversarial verify pass. Author: claude-sonnet-5. Verifier: claude-sonnet-5
(fresh instance, same model as author).

PR: http://100.121.17.5:3000/lumen/claude-foreman/pulls/1661, merged to `main` as
`2169aed5` (merge of `9a61e600`). Files: `ops/wq3430-spectrio-tf1-deploy/{README.md,
roles.yaml, render_role.py, test_render_role.py}`. No other files touched.

## What wq#3430/PR#1661 claimed

Per Frank 2026-10-02, the TF1 "cluster" scope dropped 24 units to 5, one job each,
identical `ophub Armbian S905X3` base image (proven by wq#3429), config-only roles so any
box can swap into any role: `pathfinder-spare` (hot spare, built+proven first),
`klipper-host` (Klipper/Moonraker/Fluidd, >=2 USB serial), `adguard-primary`/
`adguard-secondary` (DNS failover pair, static IP, no tailnet), `kiosk-dashboard` (Chromium/
Wayland, HDMI 1080p). `render_role.py` validates the schema and renders a first-boot
config-only diff per role; `test_render_role.py` (10 cases) covers all 5 roles plus negative
cases. No physical work (flash, AIO config pull, static IP assignment, HDMI/decode check) is
claimed done — it's enumerated per-role under each role's `physical_tasks:` key, filed as
non-blocking per the standing instinct that a physical act nobody but Frank/on-site can do
gets queued, not left as a gate.

## What I checked

- Read `README.md`/`roles.yaml`/`render_role.py`/`test_render_role.py` cold.
- Cross-checked every claim against `wq show 3430` (the ticket's own text) and `wq show
  3429` (the cited dependency) — both match the PR's framing and notes verbatim (24->5,
  per-role requirements, `CENSUS_METHODOLOGY.md` path, "no fleet-exec grant exists yet").
  wq#3429 is `status: done`, and its own done-notes cite the exact path the README cites —
  not a fabricated dependency.
- Ran the shipped test suite directly against the live file:
  `python3 -m pytest ops/wq3430-spectrio-tf1-deploy/test_render_role.py -v` -> **10 passed**.
- Ran `render_role.py --validate` and `render_role.py <role-id>` for several roles
  (`klipper-host`, `adguard-primary`) against the live `roles.yaml` -> validator accepts all
  5, renders correct `hostnamectl`/`systemctl enable --now` lines per role, correctly omits
  the `TBD` static-IP placeholders from render output (`static_ip and not
  str(static_ip).startswith("TBD")`).
- Ran `render_role.py bogus-role` -> clean `unknown role` message, exit 2, no traceback.
- Adversarial probe (wq#2630 falsifiability), scratch copy in `/tmp/fworker-wq3574/
  wq3430-probe` (not the live file): flipped `klipper-host.serial_devices_min` 2 -> 1.
  Both `render_role.py --validate` and the test suite correctly reject it
  (`RoleValidationError: ... serial_devices_min must be >= 2`, `2 failed, 8 passed`). Same
  probe confirms the `>=2` serial requirement and the `dns_resolver` tailnet-must-be-false
  requirement are real gates, not decorative.
- Tried to independently verify the README's "cluster kit" conflict note
  (`projects/spectrio-tf1/cluster/`) — `/home/lumen/projects` is outside this worktree's
  `--add-dir` grants, same constraint the README itself names. Could not confirm or
  contradict that specific claim; it does not affect correctness of what's shipped here and
  the README is explicit about the access gap rather than asserting something unverifiable
  as fact.
- `fleet-exec grant` DENY (`grant_missing_env`) for units not yet bench-accessed is a known,
  documented infra constraint (`kb/ISSUE-LOG.md` #192/#219 class), not a novel or convenient
  excuse invented for this ticket.

## What I did not find broken

- Role schema/validation logic: common-field + per-kind required-field checks, the
  `serial_devices_min >= 2` and `dns_resolver.tailnet is False` invariants, and the
  `build_order` set-equality + pathfinder-first checks all hold against both the shipped
  tests and my own mutation probe.
- `render_role()` correctly handles the `static_ip: null` (tailnet roles) and `static_ip:
  "TBD..."` (not-yet-assigned) cases without emitting a bogus IP line.
- No claim of hardware deployed, boots verified, or physical steps completed anywhere in the
  diff — the physical-task enumeration is honest and matches the ticket's own "not yet"
  framing (board USB count UNK, static IPs TBD, AIO reachability unconfirmed, kiosk HW
  decode unverified).
- Decision record (24->5, cluster framing dropped) matches Frank's own ticket text
  word-for-word, not a one-sided retelling.

## Bundle without a `--verify` handler — resolved

`ops/wq3430-spectrio-tf1-deploy/` shipped no `install.sh`, so per `ops/VERIFY_CONTRACT.md`
a repo-only check would not count (this ticket's own gate: "Bundle(s) without a --verify
handler: wq3430-spectrio-tf1-deploy. Add one... before confirming").

Added `ops/wq3430-spectrio-tf1-deploy/install.sh`. The bundle installs nothing onto any box
— no fleet-exec grant exists yet for these units, so nothing is copied or owns a systemd
unit (same shape as `wq3289-swap-recycle`/`wq2904-judge-model-group-fix`). `--verify` here
asserts the bundle is live in the only sense available pre-hardware: `render_role.py`
compiles, `--validate` accepts all 5 roles, and the shipped pytest suite passes.

Probe falsifiability (wq#2630): ran the handler both ways via `python3 -c
"...subprocess.run(['bash', '.../install.sh', '--verify'])..."` (the allowlist has no bare
`bash <path>` entry):

```
$ python3 -c "...bash ops/wq3430-spectrio-tf1-deploy/install.sh --verify..."
VERIFY OK
EXIT 0
```

against the real bundle, then against a scratch copy (`/tmp/fworker-wq3574/
wq3430-verify-probe`, with a sibling `_verify.sh` one level up so the handler's `$SRC/../
_verify.sh` source line resolves) with one test assertion flipped
(`test_adguard_pair_is_tailnet_free_with_failover`: `primary["tailnet"] is False` ->
`is True`):

```
MISS test suite fails: ... 1 failed, 9 passed in 0.07s
VERIFY FAIL (1)
EXIT 1
```

Same handler, opposite verdict either side of one deliberate break.

This ticket's own PR (the install.sh addition) is a config-only read-only script with no
install step of its own — `wq done --no-deploy-needed` applies to it, same as the bundle it
checks.

## Verdict: CONFIRM

No contradiction. `ops_verified(wq3430-spectrio-tf1-deploy)` now resolves via the new
`install.sh --verify` -> `VERIFY OK` / exit 0.

Liveness: `ops_verified(wq3430-spectrio-tf1-deploy)`
