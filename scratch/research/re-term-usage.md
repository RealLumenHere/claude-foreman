# re-term usage and its MCP equivalents — wq#4443

## What re-term is

`re-term` is the agent-side fast driver for the bench serial console. It currently
lives at `horus:~/bin/re-term` (sha `5440810c`), untracked in any repo. It holds a
persistent SSH ControlMaster to the RE box, so a warm call costs ~0.04s against a
~0.8s cold call — the same "no SSH round trip inside the loop" win that
`plugins/limpet-plugin-remote-cli` already banks for the shared-terminal/
serial-console CLIs (see that plugin's `mcp/server.py` docstring, wq#4419/wq#4433).

Known verb set (from the ticket, not independently re-derived — see "What this
worker could not verify" below): `drive / open / snap / tail / do / type / at / key /
catch / expect / status / list / kill / close`.

## Why part (1) — committing re-term into lumen-bin — is not done here

This ticket asked for two things: (1) commit the actual `re-term` script from
`horus:~/bin/re-term` into this repo, and (2) extend the shared-terminal MCP server
with its combined verbs. Part (2) is done (below). Part (1) is **not** — not skipped
by choice, but blocked by this worker's sandbox:

- `horus` is a different fleet node from `lumen`, where this build worker runs.
- Raw `ssh` is not in this worker's Bash allowlist
  (`/home/lumen/foreman/harness/worker-settings.json`).
- The sanctioned cross-node path, `/home/lumen/bin/fleet-exec run horus ...` / `copy`,
  denies with `DENY [grant:grant_missing_env]: grant file did not verify`. Per
  `guard.sh` (around its `revoke_minted_tokens` comment) and `fworker.py`, fworker only
  sets `FOREMAN_GRANT_FILE`/`FOREMAN_GRANT_SHA` for a ticket carrying a
  `needs:ssh|copy|restart` tag at dispatch. This ticket's ticket text has no such tag,
  so no grant was minted for this attempt — this is the documented
  `grant_missing_env` failure mode (fworker.py: "denied on grant:grant_missing_env for
  283 of 283 ticket-end" calls is the same class of gap, not specific to this ticket).
- No mounted/shared path, no existing copy of `re-term` anywhere under this worker's
  worktree or `--add-dir` roots (`/home/lumen/bin`, `/home/lumen/kb`,
  `/home/lumen/foreman`, `/home/lumen/agents/work-queue`, `/home/lumen/sentinel`,
  `/home/lumen/mailbot`, `/home/lumen/pokemon`) contains `re-term` or its source.

Logged as ISSUE-LOG row (see `/home/lumen/kb/ISSUE-LOG.md`, same class as row 1486 /
wq#4433's `projects/limpet` gap): a build worker cannot fetch or commit a file that
lives only on a different fleet node unless the ticket is dispatched with a
`needs:copy` (or `needs:ssh`) tag that mints the fleet-exec grant. **Resolution
options**: (a) redispatch this remainder with `needs:copy` so `fleet-exec copy horus
~/bin/re-term <lumen-drop>` succeeds, or (b) a worker/Frank session with a live
fleet-exec grant for `horus` runs the copy and opens a follow-up commit.

### What this worker could not verify

Because the actual script was unreachable, this doc does **not** invent re-term's
flag syntax, its argv shape, or its exit codes — doing so would be exactly the kind
of guess the house doctrine forbids ("never guess a path, model name, config value...
verify it"). The verb *names* below are the ticket's own list; the MCP mapping is
built from what IS verified in this repo (the shared-terminal and serial-console
CLIs at `/home/lumen/bin/shared-terminal` and `/home/lumen/bin/serial-console`, both
read directly for this ticket) — not from re-term's source.

## Part (2) — the MCP mapping (done)

`plugins/limpet-plugin-remote-cli/mcp/server.py` already exposes most of re-term's
verb set under the shared-terminal/serial-console naming, added by wq#4419 and
wq#4433:

| re-term verb | MCP tool | Notes |
|---|---|---|
| open | `serial_console_open` | spawns `sudo picocom`, sole port owner |
| snap | `snapshot` | current screen |
| tail | `scrollback` | history |
| type / key | `send` (text / keys) | literal text or tmux key names |
| catch | `serial_console_catch` | spam-until-match boot-window loop |
| expect | `serial_console_expect` / `wait` | poll for a regex, no typing |
| status | `status` | session metadata |
| list | `list` | session inventory |
| kill / close | `kill` (via `shared-terminal`) | — |
| drive | `start` | create a detached session |

Three verbs combine a read and a write in one round trip and had no MCP equivalent
before this ticket — those are the new tools this ticket adds:

- **`term_do`** (send + snapshot) — types `text` or `keys`, then immediately reads
  the screen back, so the agent sees the result of its own keystrokes without a
  second tool call. If the send fails, no snapshot is taken (a snapshot of
  whatever the screen already showed would look like a successful round trip when
  nothing was typed).
- **`term_at`** (guarded send) — waits for the screen to match `regex` first, then
  sends `text`/`keys`. A guard that never matches means nothing is ever typed — the
  point is to never send blind into the wrong prompt (a hung boot, a stale shell,
  output still scrolling).
- **`term_catch`** — identical to `serial_console_catch`, exposed under the
  `term_` prefix because the underlying `serial-console catch` subcommand only
  requires `shared-terminal status <name>` to succeed — it never checks for a
  picocom-owned port — so it already works unchanged on a plain shared-terminal
  session, not just a serial-console one.

All three are ENERGIZING (`[mcp.tools_energize]` in `limpet-plugin.toml`) since each
one always ends in a send. Implementation: `plugins/limpet-plugin-remote-cli/mcp/
server.py` (`tool_term_do`, `tool_term_at`, `tool_term_catch`, and their `TOOLS`
entries); tests: `plugins/limpet-plugin-remote-cli/tests/test_server.py`.

This gives agents the fast do/at/catch loop through MCP without hand-SSH to the RE
box. It does not, by itself, give back re-term's 0.04s-warm ControlMaster speedup —
each MCP call here is still one `subprocess.run` of the local CLI per tool call (fast,
since it's a local tmux round trip, not an SSH one). The actual SSH-latency win is
specific to re-term driving the *remote* RE box's shell over its own ControlMaster,
and only lands once part (1) is unblocked and re-term's own mechanism is evaluated
for reuse here.
