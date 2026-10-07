# wq#3522 — ser2net bench UART boot-log capture (maksoor)

KitPloit phase 6 (fix-6, 2026-10-06), rating 4: `apt ser2net` (mature; YAML config
`/etc/ser2net.yaml`, per-port `serialdev` mapping, `kickolduser`) turns a bench DUT's
UART into a timestamp-able, network-shared serial console, so boot logs survive a
closed terminal and several tools can attach to the same port instead of fighting
over `/dev/ttyUSBx`: `tio` (interactive), labgrid (wq#3492), baudowl (wq#3493).

## Security constraint (non-negotiable, per ticket)

A serial console is a root shell on the DUT. Every accepter this bundle ships binds
**127.0.0.1 or maksoor's own tailnet IP only — never `0.0.0.0`**:

- `ser2net.yaml.tmpl`'s accepter line is `tcp,__TAILNET_IP__,<port>` — address
  included, never a bare `tcp,<port>` (which defaults to all interfaces per
  `ser2net.yaml(5)`).
- `install.sh` resolves `__TAILNET_IP__` itself via `tailscale ip -4` at install
  time and **refuses to install** if tailscale isn't up, rather than ever falling
  back to a wildcard bind.
- `install.sh --verify` fails (`MISS`) if the live `/etc/ser2net.yaml` on maksoor
  contains the literal string `0.0.0.0`.
- The packaged `ser2net.service`/default config is disabled immediately after
  `apt install`, before our own config ever lands, per the ticket's "disable the
  packaged unit until a config exists" instruction.

No third-party serial-MCP wrapper (sermcp, embedded-debug-mcp, etc.) is used —
ser2net is the long-maintained upstream project (github.com/cminyard/ser2net),
installed from the distro's own `apt` package, per the ticket's supply-chain
instruction.

## What ships here

| File | Purpose |
|---|---|
| `ser2net.yaml.tmpl` | The real config, with `__TAILNET_IP__` as the only runtime-resolved placeholder. One worked example connection (`dut1`, `/dev/ttyUSB0`, port 7001) — duplicate the block per real DUT once maksoor's physical bench inventory is known; that inventory is outside this ticket's scope. |
| `install.sh` | Deploy-verify bundle per `ops/VERIFY_CONTRACT.md` (wq#1696): `apt install` → disable packaged unit → resolve tailnet IP → loopback-test → render+install our config → enable. Answers `--verify` read-only, both run locally on maksoor and remotely via ssh. |
| `test_loopback.sh` | The ticket's required pre-hardware check: builds a `socat` pty pair, runs `ser2net` against a test-rendered copy of the template pointed at the fake pty, and asserts the accepter comes up on `127.0.0.1:<port>` only (never a wildcard) before any real DUT is ever plugged in. `install.sh` runs this before enabling the real unit — not optional. |

## Deploy (next session with live ssh to maksoor)

```
scp -r ops/wq3522-ser2net-boot-log-maksoor maksoor:/tmp/
ssh maksoor 'sudo bash /tmp/wq3522-ser2net-boot-log-maksoor/install.sh'
```

## Status: NO-PROBE, shipped undeployed

maksoor is Frank's bench RE laptop, power-cycled by hand, and is currently offline
on tailscale (recurring condition — see
`reports/wq3544_verify_wq3524_maksoor_ignore_live_deploy_gap.md` and its
`nodes.ignore` entry in `ops/wq3354-fleet-selfheal/selfheal.yaml`). Per this
ticket's own `[NO-PROBE: Requires physical host Maksoor to be online]` annotation,
this bundle ships complete and ready to run but is not deployed by this session.
`install.sh --verify` run from elsewhere reports `ERR unreachable: maksoor` in that
state — per `ops/_verify.sh`'s contract, an unreachable node is `ERR`, never a
`MISS`: "I could not look" must not read as "it is not deployed."

## Config facts, verified not guessed

Per this session's doctrine (never guess a config value), every syntax claim in
`ser2net.yaml.tmpl` was checked against the ser2net 4.6.0 `ser2net.yaml(5)` man page
(Ubuntu 24.04 LTS / noble) and the upstream `ser2net.yaml` example
(github.com/cminyard/ser2net) before being written:

- Address-bound accepter syntax (`accepter: tcp,<addr>,<port>` / the rotator
  example `accepter: telnet,tcp,192.168.27.3,2020`, "bound to address only").
- `kickolduser: true|false`, `trace-both: <filename>`, `trace-timestamp: true|false`
  under CONNECTION SPECIFICATION OPTIONS.
- The `\N`/`\B`/`\Y`/`\m`/`\D` string-formatting escapes (port name, serial params,
  year, month-number, day — FILENAME, BANNER, AND STRING FORMATTING section), and
  that they must be single-quoted in YAML so the YAML parser doesn't eat the
  backslashes itself.
- `socat`'s canonical pty address form, `PTY,link=PATH,raw,echo=0` (socat(1)
  EXAMPLE section).
