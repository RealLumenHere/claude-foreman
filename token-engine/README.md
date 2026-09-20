# token-engine (T2, wq#2270)

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

T2 verbs are `ping` and `mask-selftest`; provider verbs (git, PR, gateway-key)
land via T1/T4/T10 in the `VERBS` registry.

Known limits: grants are still self-attested by uid lumen (ISSUE-LOG 557 —
follow-up will bind tickets to connections foreman-side); root remains a bypass
(design §9); fworker now writes grants 0444 so the daemon can read them
(ISSUE-LOG 560).
