# wq#4493 — burned Anthropic API key (sk-key `f29c7685753e`)

## What burned

`mask_replay.py 4484` (exit 1): one unaccounted value, sk-key, prefix `sk-ant-`, body len 99,
sha256-12 `f29c7685753e`, seen in wq#4484 attempt 1's transcript at 2026-10-23T04:50:56Z. The
context window is the `lumen/claude-sonnet` and `lumen/claude-haiku` entries of
`agent/litellm_config.yaml` (workspace `wrkspc_017zvy386miL7cs9TKomFovm`). It is the live key
LiteLLM uses for both `lumen/claude-*` routes.

Since 04:55Z on 10-23 the config reads it as `os.environ/ANTHROPIC_API_KEY` (lines 409, 417)
and holds no `sk-ant-` literal (hash sweep, zero hits). The key is still valid.

## Leak path

The tool call that put the value in context, traced by hash through the wq#4484 transcript:

    Bash: lumen-run wait lumen-job-wq4484-show-…; lumen-run log lumen-job-wq4484-show-…

The job dumped the config section inside a systemd unit, where guard.sh's path denies never
run, and `lumen-run log` printed the unit's journal unmasked. wq#3606 hit the same leak path
and fixed it in `ops/wq3606-lumen-run-log-mask/`, where `log` pipes through `mask-secrets`
(4/4 tests pass on this branch). That bundle was never installed: `/home/lumen/bin/lumen-run`
line 129 is still the raw `journalctl` line. A BUILD worker can't install it, because
`/home/lumen/bin` is a primary tree and guard.sh denies Edit there (wq#1719). The install is
filed as its own ticket with `needs: repo(/home/lumen/bin)`.

## Rotation (Frank, Console only)

The Admin API can list keys and set `status: inactive`/`archived` on
`/v1/organizations/api_keys/{id}`. It has no create endpoint, and no Admin key
(`sk-ant-admin01-`) is referenced anywhere in the fleet. Minting the replacement is a
Console act on Frank's account:

1. platform.claude.com → workspace `wrkspc_017zvy386miL7cs9TKomFovm` → API keys → create a
   new key.
2. Put it in `agent/.env` as `ANTHROPIC_API_KEY=` (0600, back up first). Run
   `cred-consumers ANTHROPIC_API_KEY` for any other holder.
3. Recreate the litellm container so it picks up the env. Probe `lumen/claude-haiku` once
   through the gateway and expect 200.
4. In the Console, disable the old key (the one whose hint matches; its last-used time stops
   moving after step 3). Probe the old value directly against `/v1/models` and expect 401.
5. Settle the row:
   `python3 /home/lumen/foreman/harness/mask_replay.py record-rotated --sha256-12 f29c7685753e --ticket wq4493 --note "Anthropic API key (litellm lumen/claude-*), old key disabled in Console, old 401 new 200"`.
   After that, `mask_replay.py 4484` exits 0, which is wq#4493 AC1.
