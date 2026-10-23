# wq#4513 — serial-mcp on Maksoor (UART over HTTP MCP)

Deploys [qarnet/serial-mcp](https://github.com/qarnet/serial-mcp) (Rust, MIT,
25-tool catalog, confirmed via direct README fetch 2026-10-23 — see
`kb/ISSUE-LOG.md`) on `maksoor` with HTTP transport bound to Maksoor's
Tailscale IP, so Lumen agents get direct UART access without SSH-wrapping or
`usbip`. This is a **different** upstream from the existing
`plugins/limpet-plugin-serial-mcp` (that one wraps `alxgmpr/serial-mcp`,
Python/PyPI `pyserial-mcp`, stdio transport, installed under
`/opt/limpet/plugins` for local Limpet-managed nodes) — the two don't
overlap; this ticket is Maksoor-specific, HTTP-only, Rust.

## Verified upstream facts (not guessed — see citations)

- Repo is real and matches the ticket's description: `cargo install
  serial-mcp` / crates.io `serial-mcp` / Rust 1.97.1 / MIT (README L1-6).
- Tool catalog: 25 tools, grouped Discovery/Connection lifecycle/I-O/Line
  control/Profiles/Logs/Utility (README "Tool catalog (25 tools)" section).
- Allowlist flag: `--allowlist <patterns>` (comma-separated globs), e.g.
  `--allowlist=/dev/ttyACM*,/dev/ttyUSB*` (README "Connect an agent" example
  block and "Transports and options" CLI table).
- HTTP transport: `--transport=http`, bind address via `--bind <addr>`
  (default `127.0.0.1:8000`) (README "Transports and options").
- Persistent JSONL event log: `--capture-dir <absolute-dir>` enables
  `export_log`, writes portable `.jsonl` filenames, never overwrites, no cwd
  fallback (README "Transports and options" / "Persistent capture").
- Latest tagged GitHub release at research time: `v0.9.3` (published
  2026-08-15T20:33:15Z, via `GET /repos/qarnet/serial-mcp/releases/latest`).
  This is the tag to pin for "vendor it, pin a tag" — `install.sh` below
  clones `--branch v0.9.3` rather than `main` or `cargo install` (which
  floats to whatever crates.io has newest).
- Maksoor facts (via `lumen_state.py current maksoor`, 2026-09-28/2026-10-20
  facts): `tailscale_ip 100.81.204.74`, `ssh_user maksoor`. Flagged
  `[UNVERIFIABLE — no probe]` in lumen_state — re-check before running
  install.sh in case it has drifted.

## Blocker: no execution path onto Maksoor from this worker (ticket blocked on this)

This worker's Bash allowlist (`harness/worker-settings.json`) has no `ssh`,
`scp`, or `rsync` entries at all. The mediated broker (`/home/lumen/bin
/fleet-exec`) is the only other path, and it doesn't cover what this ticket
needs either:

- `fleet-exec grant` returns `DENY [grant:grant_missing_env]` for this
  ticket — no `needs:` grant was issued for wq#4513, so even `fleet-exec run
  maksoor` is unavailable this session.
- Even with a grant, `maksoor`'s only allowed verb in
  `foreman/harness/fleet-exec-policy.yaml` is **read-only** `run` restricted
  to `run_readonly_cmds` (`true`, `uptime`, `hostname`, `cat`, `ls`, `stat`,
  `df`, `du`, `free`, `systemctl status|is-active|...`, `journalctl`, `ss`,
  `ip addr`, `tailscale status`, `docker ps|logs`) — no `cargo`, no package
  install, no `systemctl restart`/`start` (maksoor isn't in `allow.restart`
  either), and `copy_dirs` has no `maksoor` entry at all, so there is no
  broker path to install the Rust toolchain, build the binary, write
  `config`/the systemd unit, or start the service.
- This matches the exact gap class as wq#349/Thor (`kb/ISSUE-LOG.md` #114):
  a headless BUILD sandbox has no execution path onto a non-Lumen node and
  cannot self-grant one — not a pending Frank decision, just a missing
  broker capability.

Everything below is therefore a **ready-to-run checklist**, not something
this session executed. It needs either (a) a `fleet-exec` grant + policy
widening for maksoor (`copy` + a `sudo_cmds` entry for
`cargo build`/`apt-get install rustc`/`tee .../systemd/system/*`, reviewed by
PR per `fleet-exec-policy.yaml`'s own rules), or (b) Frank/physical hands on
Maksoor directly.

## Install (run ON Maksoor, as `maksoor`)

```bash
./install.sh
```

What it does — see the script for the exact commands:
1. Installs `rustup`/`cargo` via the official rustup script if `cargo` is
   not already on PATH.
2. Clones `qarnet/serial-mcp` at pinned tag `v0.9.3` into
   `/opt/serial-mcp/src` (not `main` — pin per ticket instructions).
3. `cargo build --release --locked`, installs the binary to
   `/opt/serial-mcp/bin/serial-mcp`.
4. Installs `serial-mcp.service` (this directory) to
   `/etc/systemd/system/`, `systemctl daemon-reload`,
   `systemctl enable --now serial-mcp`.
5. Adds the `maksoor` user to the `dialout` group if not already a member
   (upstream README "Install" section, prebuilt-binary row) — needed for
   non-root `/dev/ttyUSB*`/`/dev/ttyACM*` access.

The systemd unit (`serial-mcp.service`) binds HTTP to
`100.81.204.74:8000` (Maksoor's Tailscale IP — **re-verify this hasn't
drifted** with `tailscale ip -4` on Maksoor before enabling the unit) and
passes `--allowlist=/dev/ttyUSB*,/dev/ttyACM*` per the ticket's allowlist
requirement. Never binds to `0.0.0.0` or the LAN IP (`192.168.8.140`) — HTTP
transport has no auth of its own, so Tailscale-only bind is the access
control.

## Register with Claude Code on Lumen

Run on Lumen once the service is up on Maksoor (verify with `curl
http://100.81.204.74:8000/` first):

```bash
claude mcp add --transport http serial-maksoor http://100.81.204.74:8000
```

## Test with a USB-serial adapter (physical step — Frank/physical-access only)

Per Tier-0 instinct 3, plugging a USB-serial adapter into Maksoor's chassis
is a physical act nobody else can perform remotely — this is a **physical
task**, not a gate on the rest of the work. Once plugged in:

```bash
# from Lumen, through the registered MCP server, or directly on Maksoor:
curl -s http://100.81.204.74:8000/ # confirm server responds
# then via an MCP client call: list_ports() should show the new
# /dev/ttyUSB* or /dev/ttyACM* device; open()/transact() to confirm I/O.
```

## Note: Maksoor OS migration is planned

`lumen_state.py current maksoor` records a planned migration to
Xubuntu 26.10/26.04 (`scratch/research/maksoor-migration-plan.md`,
unscheduled as of 2026-10-20). A fresh OS install would wipe
`/opt/serial-mcp` and the systemd unit — re-run `install.sh` after any
migration.
