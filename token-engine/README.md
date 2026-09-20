# token-engine (T2 wq#2270, T10 wq#2278)

The one process allowed to hold a credential. Design:
`research/TOKEN_ENGINE_DESIGN.md` §1 + §5 (on the wq#2262 branch until merged).

| piece | where |
|---|---|
| daemon | `token_engine_daemon.py`, runs as uid `tokeneng` from root-owned `/usr/local/lib/token-engine/` |
| socket | `/run/token-engine/broker.sock`, 0660 `tokeneng:token-client`; `lumen` is in `token-client` |
| secret store | `/var/lib/token-engine/secrets/` 0700/0600 `tokeneng:tokeneng` — uid `lumen` gets EACCES |
| unit | `systemd/token-engine.service` (NoNewPrivileges, ProtectSystem=strict, PrivateTmp, RestrictAddressFamilies, MemoryDenyWriteExecute, Restart=always) |
| install | `install.sh` — root step, run detached: `lumen-run start --root -- bash .../install.sh .../token-engine` |
| client | `te_client.py` — one JSON frame per connection, grant refs only, never a secret |
| gate | `gate_replay.sh` — replays the T2 gate as uid lumen via `lumen-run start` |
| tests | `tests/test_daemon.py` — grant/deny/schema/mask logic, no root needed |

Per call the daemon: takes client identity from `SO_PEERCRED` (never the
request), re-reads + re-hashes the grant with `fleet_exec_policy.load_grant`
(every branch a DENY), requires a `{kind: token_engine, node: daemon}` grant
entry for the T2 verbs, audits to the fleet-exec dual sink with the ticket id,
and passes every outbound frame through a response-schema assertion plus the
mask-secrets patterns as a tripwire — a hit fails the verb and raises ntfy.

T2 verbs are `ping` and `mask-selftest`; git/PR verbs land via T1/T4 in the
`VERBS` registry.

T10 verbs (wq#2278, design §2 + §7 ranks 3/5–8) — each needs BOTH its policy
section in `harness/fleet-exec-policy.yaml` and a matching grant:

| verb | argv | grant kind/node | policy section | returns |
|---|---|---|---|---|
| `gateway-key` | `mint [ttl] [budget]` / `revoke` | `gateway_admin` / `gateway:litellm` | `gateway_admin_ops` | key **id**, expiry, 0640 handoff-file path — never the key value |
| `api-call` | `<provider> <method> <path> [body-file]` | `api:<provider>` / `api:<provider>` | `api_providers` | status + body only; Authorization never returned; body via file, never argv |
| `plug` | `<name> on\|off\|state` | `plug` / `plug:<name>` | `plug_names` | plug-ctl exit code + output |
| `ssh-exec` | `<node> <remote argv...>` | `ssh` / `<node>` | `run_readonly_cmds` (re-checked daemon-side) | exit code + stdout/stderr |

Migration (install.sh, design §7): rank 5 LiteLLM admin key, rank 8
`ebay.env`, rank 7 Cloudflare API token (tunnel cred stays root — instinct
#5), and the rank 3 fleet ssh key move into `/var/lib/token-engine/secrets/`.
Copy-only by default; re-run with `CUTOVER=1` to delete the lumen-readable
originals once consumers ride the broker. `fworker.py` prefers a per-ticket
`gateway-key mint` (duration + budget, revoked at attempt end) and falls back
to the static key file while the broker verb is not installed. The
`CLAUDE_CODE_OAUTH_TOKEN` stays in worker env (rank 6 — no provider-side
short-lived option); `build_env`'s pop-then-set-exactly-one discipline is
pinned by `harness/tests/test_gateway_key_per_ticket.py`.

Known limits: grants are still self-attested by uid lumen (ISSUE-LOG 557 —
follow-up will bind tickets to connections foreman-side); root remains a bypass
(design §9); fworker now writes grants 0444 so the daemon can read them
(ISSUE-LOG 560).
