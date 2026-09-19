# wq#2095 — register the RE bench inventory in fleet_tools

Frank (2026-09-19, wq#160): already owns SOIC clips, a CH341A, and Pi Picos usable as
serprog SPI programmers. They were absent from the registry, so tickets kept proposing
to buy a programmer.

`/home/lumen/minerva/context/fleet_tools.yaml` gained a `re_bench_hardware` group
(`updated: 2026-09-19`), re-rendered with `fleet-tools-render` (6 groups, 23 tools, no
drift) so it reaches `FLEET_TOOLS.md` and `claude_context.sh --core`. Like wq#1720
(`../wq1720-session-boilerplate/`), `context/` is outside version control, so the live edit has
no PR; the exact change is recorded in `wq2095_fleet_tools.diff`.

Entries (hardware entries carry no `path`, so the renderer's path-drift check skips them):

| entry | key fact |
|---|---|
| pi-pico-serprog | preferred; natively 3.3V; `flashrom -p serprog:dev=/dev/ttyACM0:115200` |
| ch341a-programmer | stock is 5V I/O — voltage mod or 3.3V clone for 3.3V chips |
| soic-clips | SOIC-8/16 only; WSON/QFN needs another approach; read the chip part number first |
| sigrok / logic analyzer | sigrok-cli + PulseView on maksoor; physical analyzer model unconfirmed |
| maksoor RE workstation toolset | from `lumen_state.py current maksoor` (node_provisioning, 2026-09-17) |

Not verified: the Pico pin map (left to the pico-serprog README rather than guessed), which
logic analyzer is physically attached, and whether the Picos already carry serprog firmware.
