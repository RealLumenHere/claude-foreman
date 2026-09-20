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
| ledger | `token_ledger.py` — minted-token ledger under `/var/lib/token-engine/minted/`, one 0600 file per ticket; revocation providers (`github-app`, `test-file`) |
| T9 gate | `gate_replay_t9.sh` — env-scoped daemon, any uid: mint → kill -9 worker → restart daemon → provider rejects the old token |
| tests | `tests/test_daemon.py`, `tests/test_ledger.py` — grant/deny/schema/mask + revocation/reaper logic, no root needed |

Per call the daemon: takes client identity from `SO_PEERCRED` (never the
request), re-reads + re-hashes the grant with `fleet_exec_policy.load_grant`
(every branch a DENY), requires a `{kind: token_engine, node: daemon}` grant
entry for the T2 verbs, audits to the fleet-exec dual sink with the ticket id,
and passes every outbound frame through a response-schema assertion plus the
mask-secrets patterns as a tripwire — a hit fails the verb and raises ntfy.

T2 verbs are `ping` and `mask-selftest`. T4 (wq#2273) added the PR family —
`pr-open` / `pr-update` / `pr-comment` / `pr-view` / `pr-checks` / `pr-files` /
`pr-list` / `pr-merge` — granted per resource (`kind` `pr_write`/`pr_comment`/
`pr_read`, `node` `repo:owner/name`, derived from `argv[0]`). Titles and bodies
travel as FILE PATHS under `/home/lumen` (never argv, never inside the state
dir, mask-tripwired before posting); results carry only scalars (PR number,
url, normalised check counts). The forge, base URL and token file per slug come
from root-installed `forges.json` + the identity map — never from the client.
Remaining provider verbs (git, gateway-key) land via T1/T10.

T9 (wq#2277, design §3.4) adds revocation: `ticket-end` revokes every token
ledgered for the calling ticket (grant scoping means only its own) and drops it
from the in-memory map; `guard.sh --lifecycle end` fires it at SessionEnd,
best-effort. A reaper in `main()` runs the same sweep over the whole ledger on
every daemon start — a kill -9'd worker never fires SessionEnd, so its tokens
die at the next restart instead. Revocation is idempotent (`DELETE
/installation/token` answering 401/404 counts as revoked; a missing ledger is a
clean no-op); entries whose provider call fails stay ledgered and alert.
`mint-test` exercises the pipeline against the file-backed test provider and is
refused unless the daemon env sets `TOKEN_ENGINE_ALLOW_TEST_MINT=1` (the unit
never does).

Known limits: grants are still self-attested by uid lumen (ISSUE-LOG 557 —
follow-up will bind tickets to connections foreman-side); root remains a bypass
(design §9); fworker now writes grants 0444 so the daemon can read them
(ISSUE-LOG 560).
