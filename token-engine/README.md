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

T1 (wq#2271, design §3.1-3.2) adds the git family — the recurring leak
(wq#2243 / ISSUE-LOG 537):

- `git-credential <slug> <protocol> <host>` (`kind` `git_read`, `node`
  `repo:owner/name`) backs `/usr/local/bin/git-credential-token-engine`, a
  helper that forwards git's credential `get` line protocol to the broker and
  never writes a file (`store`/`erase` are no-ops). The daemon pins the
  host/scheme to the forge it maps the slug to and answers with a one-shot
  handoff FILE (the gateway-key pattern) the helper reads and unlinks — the
  frame stays value-free, the mask tripwire stays intact. fworker writes the
  per-worktree config that selects it (`credential.helper` reset + `token-
  engine`, `credential.useHttpPath=true` — worktree config, never
  `~/.gitconfig`), so the token stops touching URLs, config, env, or history;
  it crosses only into git's memory for one fetch/push (§3.1 residual risk).
- `git-fetch <slug> [branch]` / `git-push <slug> <branch> [src]` (`git_read` /
  `git_write`) are the zero-crossing path: git runs in the daemon as uid
  tokeneng. Fetch lands in a group-readable bare mirror under the runtime dir
  the worker pulls from by path; push reads the branch out of the calling
  ticket's worktree (or `src`, bounded under `/home/lumen`) through a scratch
  bare repo and pushes with auth held in daemon env only. No `--force`
  spelling exists.
- GitHub auth prefers a per-call App installation token (1 h TTL, repo-scoped,
  ledgered `github-app` for ticket-end/reaper revocation) once forges.json
  carries a `github_app` block; until that App exists (T0 prereq) the
  engine-held PAT is served — the same in-memory crossing the fleet already
  had, minus every on-disk surface. install.sh also runs the wq#2243 sweep
  `--fix` and installs its hourly timer (ISSUE-LOG 540), and
  `TE_FINALIZE_MOVE=1` additionally shreds the `/home/lumen/.secrets` forge
  tokens, `~/.git-credentials`, and the global `credential.helper=store` line.
- T1 gate: `gate_replay_t1.sh` (via `python3 tests/run_replay.py t1`) — push
  through helper→broker→handoff against a Basic-auth loopback forge
  (`tests/fake_git_forge.py`), userinfo-free remote, token absent from
  config/env/sweep, handoff consumed.

T10 (wq#2278, design §2 + §7 ranks 5-8) adds the rest of the inventory:

- `gateway-key mint <name> <ttl> <budget-usd>` / `revoke <key-id>` (`kind`
  `gateway_admin`, `node` `gateway:litellm`): per-ticket LiteLLM virtual key
  with a duration and a budget, minted with the engine-held admin key. The
  frame carries the key id, expiry and a handoff PATH only — the value lands
  in a 0640 `tokeneng:token-client` file under `/run/token-engine/minted/`
  that `fworker.build_env` reads into the attempt env and unlinks. Ledgered
  as provider `litellm-key`, so `ticket-end`/the reaper `/key/delete` it.
  fworker falls back to the long-lived virtual key when the broker is down.
- `api-call <provider> <method> <path> [body-file]` (`kind` AND `node`
  `api:<provider>`, both derived from `argv[0]` so a cloudflare grant never
  reaches ebay): providers come from root-installed `providers.json`; body
  via file path under `/home/lumen` like the PR verbs; the response body is
  mask-REWRITTEN before framing (the one place masking is a rewrite — the
  outbound tripwire stays as backstop); the Authorization header is built
  in-daemon and never returned. `ebay` mints its ~2h access token from the
  engine-held `ebay.env` app credentials, cached 0600 under the state dir.
- `plug <name> on|off|state` (`kind` `plug`, `node` `plug:<name>`): Tapo
  creds from engine-held `tapo.env`, passed to `kasa` via
  `KASA_USERNAME`/`KASA_PASSWORD` env — never argv, nothing in /proc.
- `ssh-run <node> <cmd...>` (`kind` `ssh`, same grant shape as fleet-exec,
  `run` required in `verbs`): design §7 rank 3 — the fleet ssh key moves to
  the engine store and the ssh runs as uid `tokeneng`, behind the SAME
  `check_run` readonly allowlist fleet-exec applies, so the move changes who
  holds the key, not what a worker may execute. fleet-exec delegation is the
  bin-repo follow-up; until it lands, install.sh COPIES the key (T4 rule:
  never break a live consumer before its verb path exists).

`install.sh` copies ranks 5-8 sources (`ebay.env`, `tapo.env`, the Cloudflare
API token, the ssh key; `litellm-admin.token` arrives via `LITELLM_ADMIN_KEY`
env at install time since the master key is operator-held) into the store;
re-run with `TE_FINALIZE_MOVE=1` once every consumer is on verbs — that is
the step that makes them unreadable by uid lumen. T10 gate:
`gate_replay_t10.sh` (via `python3 tests/run_replay.py t10`) — mint against a
loopback fake gateway, value-free frames, dual-sink audit, revocation at
ticket-end, deny paths.

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
