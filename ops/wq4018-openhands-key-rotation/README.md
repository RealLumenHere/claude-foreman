# wq#4018: OpenHands LiteLLM key burned by wq#4007

## What burned

`mask_replay.py 4007` reports one unaccounted value: sk-key, body sha256 `edce1aefdbdd`, seen in
`read:/home/lumen/containers/openhands/docker-compose.yml@0#live`. wq#4007 attempt 1 Read the compose
whole, and its `LLM_API_KEY=sk-...` line is a live LiteLLM virtual key.

`probe_key.py` checks this by hash only, as of 2026-10-15. The live compose still carries `edce1aefdbdd`
(compose mtime 2026-10-13T15:38Z) and `GET :4000/key/info` with that key returns 200, so the key is
still live.

## Leak path, closed here

`harness/guard.sh` now refuses Read/Grep of that compose file and its copies, the same rule
wq#3678 added for the LibreChat compose. Bash reads were already refused, because the
container-runtime word rule matches the path. To check the key without printing it, run
`python3 probe_key.py`, or `python3 probe_key.py --copies` to also scan backups.

## Rotation (privileged lane, not a BUILD worker)

A BUILD worker can't run this rotation. `rotate.py` reads `/home/lumen/.secrets/litellm-master.env`
and runs `docker compose up`, and guard.sh denies both to workers (the same wall as ISSUE-LOG rows
1218/1239). Run it from a container-capable operator lane on Lumen:

    ssh lumen@<lumen ip from lumen_state.py current lumen> \
      'python3 /home/lumen/foreman/ops/wq4018-openhands-key-rotation/rotate.py --dry-run'
    ssh lumen@<...> 'python3 /home/lumen/foreman/ops/wq4018-openhands-key-rotation/rotate.py'

The script never prints a key. Each step refuses to continue when the previous one failed:

1. Confirm by hash that the compose key is `edce1aefdbdd`. It exits 0 when the key is already gone.
2. Mint a replacement that mirrors the old key's alias, models, budget and limits from `/key/info`.
   The new alias is `<alias>-wq4018`.
3. Write the replacement to `/home/lumen/containers/openhands/.env` (0600), then change the compose
   line to `LLM_API_KEY=${OPENHANDS_LLM_API_KEY}`. The original goes to
   `docker-compose.yml.bak.wq4018` (0600).
4. Run `docker compose up -d --force-recreate`.
5. Check the new key returns 200, delete the old key, then check the old key returns 401.
6. Run `mask_replay.py record-rotated --sha256-12 edce1aefdbdd --ticket 4018`. After that,
   `mask_replay.py 4007` exits 0.

If a run stops after step 3, rerun the script. It finds the burned key in the `.bak.wq4018` copy
and the replacement in `.env`, then resumes at step 4. It doesn't mint a second key.

After the rotation, the compose holds no secret, so the wq#4018 guard rule can be lifted.
