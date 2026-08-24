# wq#948 — litellm-budget-alert stopped putting service keys in URLs

`lumen-budget-alert.timer` ran `/home/lumen/bin/litellm-budget-alert` every 6h. For each
`*.key` file under `/home/lumen/.secrets/litellm-service-keys` the script called
`GET /key/info?key=sk-...`. Uvicorn logs the request line with its query string, so
`docker logs litellm` holds every service key in cleartext — the same disclosure that
burned `ops-emergency` in wq#914, through a different channel. wq#920 rotated that key to
`ops-emergency-2`; the 00:15Z sweep re-logged the replacement about two hours later.

| file | what it is |
|---|---|
| `litellm-budget-alert.diff` | The patch applied live to `/home/lumen/bin/litellm-budget-alert` (that repo is not commit-safe — see `ops/wq841-rework-durability`). |
| `ensure_no_key_in_url.py` | Keeps the patch alive. Exit 0 clean, 1 leaking. Run it if anything rewrites the script. |

## The fix

`check_key(key_str)` is gone. `key_index()` fetches
`GET /key/list?return_full_object=true&include_team_keys=true&size=100&page=N` once,
paginating, with the master key in the `Authorization` header — headers are not logged —
and indexes the result by each record's `token`, which is `sha256(key)`. `main()` then
hashes each local `*.key` file and looks it up. The secret never reaches a URL, and no
extra call is made per key.

Behaviour that changed beyond the transport:

- A key file matching no gateway record is now a named failure ("rotated or revoked?")
  rather than an HTTP error string. It still alerts urgent and still exits 1.
- A gateway or master-key failure now short-circuits with one urgent alert instead of one
  per key. Same alert, same exit code, 1 request instead of 14.
- Aliases come from the gateway, so `ops-emergency.key` correctly reports as
  `ops-emergency-2` (wq#920 deliberately kept the old file path).

## Verified 2026-08-24

```
python3 /home/lumen/bin/litellm-budget-alert     # 14 keys, same table, "All keys within budget."
docker logs litellm --since 3m | grep -c 'GET /key/info?key=sk-'   # 0
docker logs litellm --since 3m | grep -c 'GET /key/list'           # 3
python3 ensure_no_key_in_url.py --json                             # ok: true
python3 ensure_no_key_in_url.py --file <pre-patch copy>            # ok: false, flags line 40
```

## Not done here

- **The keys already in the container log stay exposed.** This patch stops new leaks; it
  does not un-log the 2026-08-23 18:15Z and 2026-08-24 00:15Z sweeps, which printed all 14
  service keys including `ops-emergency-2`. Under the printed=burned rule those keys need
  rotating and the retained log needs dropping — filed separately, since rotation is the
  same class of action wq#920 took.
- `/home/lumen/agent/master_key_guard.py:20` still names `/key/info` as the budget-alert
  route in a comment. Its actual allowlist is the `/key/` prefix, so `/key/list` passes and
  nothing is broken; only the comment is stale. Left alone — different owner.
