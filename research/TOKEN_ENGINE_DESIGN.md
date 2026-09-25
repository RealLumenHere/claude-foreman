# Single token engine — agents request credentialed actions, never hold the credential

Status: DESIGN, review before code. Ticket: wq#2262 (Frank, 2026-09-20). Tree: claude-foreman @ `486691a`,
`/home/lumen/bin` and `/home/lumen/foreman/harness` as installed 2026-09-20. Triggering incident: wq#2243
(ISSUE-LOG row 537) — a GitHub PAT in a clone's remote URL, printed into an orchestrator transcript by an
ordinary `git remote` listing.

Every claim below carries `file:line` from the live tree or says how it was probed. Claims I could **not**
verify from this worktree are marked `[UNVERIFIED]` with the exact check that settles them; there is no
network egress from a build worker (WebFetch and WebSearch both returned "permissions … not granted"), so
the provider matrix in §4 is deliberately provenance-tagged rather than presented as fact.

---

## 0. What is actually true today

| claim | live state | verdict |
|---|---|---|
| there is already a broker to extend | `/home/lumen/bin/fleet-exec` (484 lines) + `fleet_exec_policy.py` (317 lines): verbs `run`/`copy`/`restart`/`grant`, static policy at `harness/fleet-exec-policy.yaml`, per-ticket grant, JSONL audit | TRUE — build on it, do not start a second broker |
| grants are already per-ticket and tamper-evident | `fworker.py:1044 resolve_exec_grant` turns a ticket's `needs` tags into `grants` via `capability.policy_grants` (`:1062`), writes `run_dir/grant.json` (`:1071`), exports `FOREMAN_GRANT_FILE` + `FOREMAN_GRANT_SHA` (`:2474-2475`); `fleet_exec_policy.py:76 load_grant` re-hashes the file and fail-closes on sha/ticket/worker/expiry mismatch, every branch a DENY not an exception (`:77-111`) | TRUE — the grant substrate needs extending, not replacing |
| the worker runs as the same OS user that owns the secrets | `id -un` → `lumen`; `lumen-pr:28,87` reads `/home/lumen/.secrets/github-frank.token` and `gitea-lumen.token` in-process as that user | TRUE — **this is the core defect.** The agent is stopped from reading secrets by a *tool-layer* hook, not by file permissions |
| the guard denies credential **paths** | `guard.sh:336` blocks `Read/Edit/Write/Grep/Glob` on `*.env`, `*/.claude/*`, `/home/lumen/.secrets*`; probed live — `ls /home/lumen/.secrets` was refused: `BLOCKED — denied path` | TRUE |
| the guard denies credential-emitting **command shapes** | `guard.sh` has no deny for `git remote -v`, `git config -l`, `env`, `printenv`. Line 310 denies mutating `curl`/`wget` only | FALSE — **the exact wq#2243 shape is still open at the guard** |
| `mask-secrets` is usable by a worker | `/home/lumen/bin/mask-secrets` exists (11 lines, 6 sed patterns). Probed live: `git remote -v \| /home/lumen/bin/mask-secrets` → `requires approval` — it is not on the worker allowlist | FALSE — instinct #4's mandated shape is unrunnable, so the compliant path is *harder* than the leaking one |
| a hook can redact a tool result before the model sees it | `research/SMARTCRUSHER_HOOK.md:22,40-54`: string-searched the shipped CLI 2.1.233 binary — `updatedOutput`/`updatedToolResult`/`replaceOutput`/`toolResultOverride` occur **0 times**. `PostToolUse` only appends (`decision`/`additionalContext`) | FALSE — §6 is built around this, not against it |
| the command-rewrite lever could pipe everything through the mask | `guard.sh:316-324`: the pipeline rewrite was **removed** by wq#954 and is documented as do-not-reintroduce — piping subshells the left side, so `cd`/`export`/`source` had to be exempted and "those exemptions were the entire residual leak (168 results, 725 KB, 20.1% of the corpus's over-cap bytes)" | TRUE, and it kills the obvious design |
| workers get secret env vars | `fworker.py:1222 build_env` pops `ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`, `CLAUDE_CODE_OAUTH_TOKEN` (`:1224-1227`), then re-adds exactly one model-credential per pool: cloud → `CLAUDE_CODE_OAUTH_TOKEN` (`:1235`), local → `ANTHROPIC_AUTH_TOKEN` = LiteLLM key (`:1274`). No GitHub/Gitea/Cloudflare token is ever put in worker env | PARTLY — one live model credential is in worker env by design; the forge credentials are not |
| `lumen-run` leaks a secret into workers | grep for `TOKEN\|KEY\|SECRET\|credential\|env[` over `/home/lumen/bin/lumen-run` → no matches | FALSE, but it runs jobs as **root** via `sudo systemd-run` (`lumen-run:10-13`), which is a path to every secret on the box |
| the wq#2243 fix landed | `ops/wq2243-remote-credential-sweep/` — `remote_credential_sweep.py`, tests, systemd `.service`/`.timer`, `install.sh` with `--verify` (commits `dae7e4e`, `1a8ed1d`). ISSUE-LOG row 540: the timer is **not installed** (`systemctl is-enabled` → `not-found`) and the burned token's rotation is still pending on the owner | PARTLY — detection code exists, is not running, and the leaked credential is still live |

**Reading of the evidence.** Every control we have today is a *tool-layer* control: a hook that refuses a
path, an allowlist that refuses a command. The secrets themselves are readable by the worker's own uid, so
every control is one un-hooked code path away from being bypassed — and §0 found two such paths already
(`git remote -v` at the guard, `sudo systemd-run` via lumen-run). Masking cannot be the backstop because the
CLI has no lever to rewrite a tool result, and the only lever that exists (`PreToolUse updatedInput`) was
already tried as a pipeline rewrite and retired for leaking through its own exemptions. That leaves exactly
one durable fix, which is the one Frank asked for: **take the secret out of the worker's reach entirely.**

---

## 1. Architecture

```
worker (uid lumen, Claude CLI, no secret on disk it can read)
  │  fleet-exec <verb> ...            argv only, never a secret
  ▼
fleet-exec CLI  ──── validates FOREMAN_GRANT_FILE/SHA, static policy  (existing, fleet_exec_policy.py)
  │  AF_UNIX SOCK_STREAM  /run/token-engine/broker.sock   (0660, group token-client)
  ▼
token-engine daemon (uid tokeneng, systemd unit, Restart=always)
  ├── secret store: /var/lib/token-engine/secrets/*  0600 tokeneng:tokeneng
  ├── minters: GitHub App JWT→installation token, LiteLLM /key/generate, …
  └── performs the ACTION, returns the RESULT
```

Four properties, each one a testable assertion:

1. **The socket is the only interface.** The worker's uid cannot `open()` any secret file — enforced by
   file mode, not by a hook. Test: `sudo -u lumen cat /var/lib/token-engine/secrets/<f>` → `EACCES`.
2. **Verbs, never secrets.** No response frame contains a credential. Test: a response-schema assertion in
   the daemon plus `mask-secrets` applied to every outbound frame as a tripwire that *logs an alert and
   fails the verb* if it ever fires (a mask that fires is a bug, not a save).
3. **Authorisation is the existing grant.** The worker passes `FOREMAN_GRANT_FILE`/`FOREMAN_GRANT_SHA`
   through; the daemon re-reads and re-hashes the grant file itself (it can — it runs as its own uid and
   the file is world-readable JSON containing no secret) rather than trusting anything the worker says.
   `SO_PEERCRED` on the socket gives the daemon the client's uid/pid independently.
4. **Every call is audited with the ticket id.** Reuse `fleet_exec_policy.py:277 audit_record` /
   `:313 append_jsonl` and the existing dual sink (`fleet-exec:112-114`: global `fleet-exec.jsonl` plus the
   per-worker audit path).

**Why a daemon and not `sudo -u tokeneng <script>`.** A sudo rule is re-evaluated per exec and its argv is
visible in `/proc`; a long-lived socket holds the minted token in the daemon's heap for the seconds it is
needed and never puts it in an argv, an env var, or a file. It also gives one place to revoke everything a
ticket holds when the ticket ends (§3.4).

---

## 2. Verbs

The grant schema already is `{kind, node, …}` (`fleet_exec_policy.py:114 find_grants` matches on
`kind` + `node`). Token-engine verbs reuse it with `node` = the *resource* (`repo:owner/name`,
`gateway:litellm`, `plug:desk-lamp`) so `find_grants` needs no change.

| verb | argv | grant kind | returns | never returns |
|---|---|---|---|---|
| `git-fetch` | `<repo> [refspec]` | `git_read` | exit code + stderr, masked | any credential |
| `git-push` | `<repo> <branch>` | `git_write` | exit code + stderr, masked | any credential |
| `pr-open` | `<repo> <head> <base> <title-file> <body-file>` | `pr_write` | PR number + url | token, API key |
| `pr-update` | `<repo> <number> <body-file>` | `pr_write` | PR number + url | ” |
| `pr-comment` | `<repo> <number> <body-file>` | `pr_comment` | comment id | ” |
| `api-call` | `<provider> <method> <path> [body-file]` | `api:<provider>` | response body, masked | `Authorization` header |
| `gateway-key` | `mint <name> <ttl>` / `revoke <name>` | `gateway_admin` | key **id** and expiry only | the key value |
| `plug` | `<plug> on\|off\|state` | `plug` | state | ” |

Rules that apply to every verb:

- **Body via file, never argv.** `title-file`/`body-file` are paths inside the worker's worktree; the daemon
  reads them. Keeps PR bodies out of `/proc/*/cmdline` and lets `check_argv_shape`'s metachar ban
  (`fleet_exec_policy.py:123-138`) stay strict.
- **Existing shape checks first.** `check_argv_shape` runs before any allowlist for these verbs too; the
  `python3 -c` exemption at `:157` stays confined to `check_run`.
- **Static policy AND grant.** A verb needs both a `never`-section pass in
  `harness/fleet-exec-policy.yaml` and a matching grant entry, same as `check_restart`/`check_copy` today.
- **Deny is logged with a rule tag.** Same `(ok, rule, reason)` triple the existing checks return
  (`fleet_exec_policy.py:146`), so the audit stays machine-readable.
- **Response masking is a tripwire, not a control** (§1.2).

New policy sections, mirroring `run_readonly_cmds` style: `git_repos_read`, `git_repos_write`,
`pr_repos`, `api_providers`, `gateway_admin_ops`, `plug_names`.

---

## 3. Git specifically — the recurring leak

### 3.1 Credential helper on the engine's socket

Git's credential protocol is a tiny line protocol on stdin/stdout (`get` → `protocol=`/`host=`/`path=`,
helper replies `username=`/`password=`). Ship `/usr/local/bin/git-credential-token-engine`: a ~40-line
client that forwards the `get` request to the broker socket and writes back the reply. Config, written by
the harness into each worktree (not into `~/.gitconfig`, so it is per-worktree and auditable):

```
[credential]
    helper = token-engine
    useHttpPath = true          # so the helper can scope per repo path, not per host
```

`erase`/`store` are no-ops. The helper never writes a file, so nothing lands in
`~/.git-credentials`, `git config`, env, or shell history. The password the helper returns is a
short-lived minted token (§4) that the daemon also records against the ticket for revocation.

**This is still a credential crossing into the worker's process for the duration of the push** — git holds
it in memory and puts it in an `Authorization` header. That is the residual risk of the helper approach and
it is bounded by: the token's TTL (≤1 h), its scope (one repo), and revocation at ticket end. If we want
*zero* crossing, `git-push` as a broker verb (§2) is strictly stronger, because git runs in the daemon's
process. **Recommendation: build both, in this order** — the helper first (it fixes the URL/config/history
leak surface immediately and works with every git invocation the fleet already has), the `git-push` verb
second (it removes the last crossing for the paths that matter). The helper then stays as the fallback for
ad-hoc read-only fetches.

### 3.2 Token-free remotes

- Rewrite every remote to `https://<host>/<owner>/<repo>.git` — no userinfo, no token.
  `ops/wq2243-remote-credential-sweep/remote_credential_sweep.py --fix` already strips userinfo via
  `git config --file` with path-only output; reuse it as the migration's sweep, and promote it from
  "detect hourly" to "detect + fix" once the helper is in place (a token-free URL is only usable *after*
  the helper exists, which is why the helper is the first child ticket).
- Install its systemd timer — ISSUE-LOG row 540 says it was never installed (`systemctl is-enabled` →
  `not-found`). That is a root step and belongs in the migration, not in this design.

### 3.3 Pre-push hook that refuses userinfo

`core.hooksPath` → a fleet-owned hooks dir (worker-unwritable, `guard.sh`'s primary-tree freeze at
`:341` already models the pattern). `pre-push` receives the remote URL as `$2`:

```
case "$2" in *://*@*) echo "pre-push: remote URL carries userinfo — refusing (wq#2262)" >&2; exit 1 ;; esac
```

Also refuse when `credential.helper` resolves to anything but `token-engine` on a push to a known forge
host, so a worktree cannot quietly fall back to a stored credential.

### 3.4 Revocation on ticket end

`guard.sh --lifecycle end` already runs at `SessionEnd` (`worker-settings.json:177-186`). Add a broker
`ticket-end <id>` call there: the daemon revokes every token it minted for that ticket
(`DELETE /installation/token` for GitHub App tokens, provider equivalent otherwise) and drops the
ticket's entry from its in-memory map. Ticket-end revocation must be idempotent and must also run from a
reaper on daemon start, because a killed worker never fires `SessionEnd`.

**The ticket-end grant is its own grant (wq#2349).** Ending a ticket is a lifecycle op every attempt is
entitled to, not an exec capability, so it must not ride on the `needs:ssh/copy/restart` grant
`fworker.resolve_exec_grant` mints. It did, implicitly: `guard.sh` passed no grant reference, `te_client`
defaulted to `FOREMAN_GRANT_FILE`/`FOREMAN_GRANT_SHA`, and almost no ticket carries an exec-shaped needs
tag — so the broker denied `grant:grant_missing_env` on **283 of 283** ticket-end calls
(`events/fleet-exec.jsonl`) and §3.4 revocation had never run once; the daemon-start reaper was the only
path that had ever revoked anything. `fworker.write_ticket_end_grant` now writes one grant per attempt
(`_runs/wq<id>/ticket-end-grant-a<n>.json`, 0444, lease = wall clock + 30 min because `SessionEnd` fires
*after* the child exits) holding exactly `{kind: token_engine, node: daemon}` and bound to this ticket and
worker id, and publishes it as `FOREMAN_TICKET_END_GRANT_FILE`/`_SHA`. `load_grant` then forces
`grant.ticket == req.ticket`, so the verb can only revoke what this attempt minted. With those vars unset
`guard.sh` **skips** rather than calling and being denied — one denial per session is what kept this
invisible for 283 of them.

---

## 4. Short-lived, narrow tokens per provider

**Verified 2026-09-24 (wq#2269 / T0).** Settled from a node with egress: live API probes for Gitea,
LiteLLM and Cloudflare; official docs for GitHub (live mint needs a GitHub App we do not have yet) and eBay
(verified from `bin/ebay-token` + docs, not a live call — refurb business out of scope). Each row carries its
source and the response field that proves it; no token value appears in this table by design.

| provider | what we use today | shortest-lived narrow option (claim) | provenance | how to verify |
|---|---|---|---|---|
| GitHub | classic PAT, account-wide (`.secrets/github-frank.token`, `github-lumen-classic.token` — ISSUE-LOG 537: a PAT of this class is what leaked; classic PATs never expire, account-wide, only ‘revoked’ by regeneration) | **GitHub App installation access token**: **1 h TTL** (401 after), scoped to chosen repos + permissions at mint, revocable early via `DELETE /installation/token` | `[VERIFIED 2026-09-24]` TTL **1 h**, response field `expires_at`, scope via `repositories`+`permissions` body — docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/authenticating-as-a-github-app-installation. **PREREQ: no GitHub App exists yet (only classic PATs) — create+install one before T1 can mint** | mint via `POST /app/installations/{id}/access_tokens`, read `expires_at` |
| Gitea (`.secrets/gitea-lumen.token`, `lumen-pr:87`) | personal access token | scoped token (1.27.3 scope list). **No expiry field at all** — mitigate by narrow scope + one-command rotation; engine holds it rather than minting | `[VERIFIED 2026-09-24]` Gitea PATs have **no expiry** (go-gitea #17864, never implemented); token-mgmt endpoints require **Basic auth** (token-auth errors); 1.27.3 supports scoped tokens — docs.gitea.com/api/operations/user-create-token | `GET /api/v1/users/{u}/tokens` **(Basic auth)** — no expiry field returned |
| LiteLLM gateway (`fworker.py:1274`, key file `FOREMAN_LITELLM_KEY_FILE`) | long-lived virtual key in worker env | `/key/generate` with a `duration` + budget, minted per ticket, `/key/delete` at ticket end | `[VERIFIED LIVE 2026-09-24]` `POST /key/generate {"duration":"1h"}` → response includes `expires` (probe: expiry ≈ mint+1 h); `/key/delete` revokes. Test key minted+deleted on localhost:4000 | `POST /key/generate {"duration":"1h"}`, confirm `expires` |
| Anthropic (`CLAUDE_CODE_OAUTH_TOKEN`, `fworker.py:1235`) | static OAuth token file | no short-lived option under our control. Keep it the one credential in worker env, and keep `build_env`'s pop-then-set-exactly-one discipline (`:1224-1235`) | tree-verified | n/a |
| Cloudflare / cloudflared (`cfut_` in `mask-secrets:6`) | tunnel credential | API tokens support scoped permissions + `expires_on`; tunnel creds themselves long-lived. Engine holds it; admin calls via `api-call cloudflare` | `[VERIFIED LIVE 2026-09-24]` `GET /client/v4/user/tokens/verify` → `status`+`expires_on` (field exists; **our `cf-dns-edit.token` returned `expires_on: null` = no expiry set — set one on rotation**) — Cloudflare API docs | `GET /client/v4/user/tokens/verify`, check `expires_on` |
| eBay (`bin/ebay-token`) | OAuth — **our script uses `client_credentials`** (application token), not the user/refresh flow | application access token **`expires_in` 7200s (2 h)**, re-minted on expiry — **no refresh token in the client_credentials grant** (nothing long-lived to lock beyond the client secret). (User `authorization_code` flow, unused here, would give a 2 h access token + ~18 mo non-rotating refresh token, `refresh_token_expires_in` 47304000.) | `[VERIFIED 2026-09-24]` from `bin/ebay-token` (grant_type=client_credentials, caches `expires_in` default 7200 −90s) + developer.ebay.com/api-docs/static/oauth-client-credentials-grant.html. **No live eBay call — refurb business out of scope** | read `bin/ebay-token`; `expires_in` 7200 |

**The rule regardless of provider:** mint per operation, keep it in the daemon's heap, never write it to
disk, revoke at ticket end. Where a provider has no short-lived option, the engine holds the long-lived
credential and the worker still only ever gets verbs — the blast radius collapses even without a TTL.

---

## 5. Worker isolation

Audited (§0): `build_env` is already disciplined — it pops all four Anthropic vars and re-adds exactly one
per pool, and no forge credential is ever placed in worker env. `lumen-run` passes no secret. So the env
surface is *not* where the leaks come from; the uid is.

Required changes:

1. **New OS user `tokeneng`**, own home, `/var/lib/token-engine` 0700, secrets 0600. Worker uid `lumen`
   gets **no** read path. Migration moves `/home/lumen/.secrets/*` there file by file (§7).
2. **Group `token-client`** owns the socket 0660; `lumen` joins it. Socket access is the whole grant the
   worker uid needs.
3. **`lumen-pr` stops reading token files** (`lumen-pr:28,87,106,259`) and calls broker verbs instead. This
   is the single largest code change in the migration and the reason `pr-open`/`pr-update` are first-class
   verbs rather than an `api-call github` special case.
4. **`lumen-run`'s root path is a hole** — a root job can read `tokeneng`'s files. Constrain the systemd-run
   units it creates with `User=`/`ProtectHome=`/`InaccessiblePaths=/var/lib/token-engine` unless the unit
   genuinely needs root, and deny `lumen-run` on the worker allowlist for archetypes that have no ops need.
5. **Daemon hardening**: `NoNewPrivileges=yes`, `ProtectSystem=strict`, `PrivateTmp=yes`,
   `RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6`, `MemoryDenyWriteExecute=yes`, no shell in its PATH.

---

## 6. Defence in depth, given what the harness can actually do

The honest constraint from §0: **nothing can redact a tool result between the tool and the model** in CLI
2.1.233, and the one lever that rewrites a command was retired for leaking through its exemptions. So the
layers are ordered by where they can actually stand:

| layer | where it stands | what it catches | status |
|---|---|---|---|
| engine holds the secret | file permissions, `tokeneng` uid | everything, structurally | this design |
| guard deny on credential-emitting **command shapes** | `guard.sh` Bash branch, next to `:310` | `git remote -v`, `git config -l`, `env`, `printenv`, `git config --get-urlmatch` — the wq#2243 shape, which is **open today** | new, child T5 |
| guard deny on credential **paths** | `guard.sh:336` | `Read`/`Grep` of `.secrets`, `.env`, `.claude` | exists, extend with `/var/lib/token-engine` |
| `mask-secrets` on the worker allowlist | `harness/worker-settings.json` | lets an agent *comply* with instinct #4 at all — today the compliant shape is refused | new, child T5 |
| mask the **transcript**, not the context | `fworker.py:743` reads the child's stdout stream-json; mask there before anything is written to disk/ntfy/episode store | a value that already reached the model still never reaches a file, a notification, or a PR body | new, child T6 |
| `remote_credential_sweep` timer | systemd, hourly | a token that lands in a remote URL anyway | code exists, **timer not installed** (ISSUE-LOG 540) |
| provider-side push protection | GitHub secret scanning + push protection on every fleet repo | a token in a commit | new, child T7 |
| one-command rotation with no doc drift | wq#1619 | the class defect: rotation moves a file and leaves every doc pointing at the old path | open ticket, fold in as T8 |

Two things this table refuses to pretend:

- **`mask-secrets` cannot protect the model's context.** Wiring it as a universal pipeline rewrite is the
  design guard.sh explicitly warns against (`:316-324`). It goes on the allowlist as a tool an agent can
  *choose*, and into fworker's transcript path where it works unconditionally. Those are the two places it
  is real.
- **Masking is not a control, it is an alarm.** Every `mask-secrets` hit anywhere should raise an ntfy and
  open a rotation ticket, because a masked token is still a burned token (instinct #4). One ticket per burned
  **value**, not per run that printed it (wq#2341): a repeat is a note on the row that is already open, and
  only a row that was closed — the credential rotated — lets the same value page a fresh one.

---

## 7. Migration inventory, ranked by blast radius

| rank | credential | who can read it today | blast radius | moves behind |
|---|---|---|---|---|
| 1 | `github-frank.token` — classic PAT, push + admin on frankstupak repos (ISSUE-LOG 537 says push+admin on the LPP repo) | uid `lumen` = every worker, via any un-hooked code path | account-wide on a personal account; **already burned once and rotation is still pending** | `git-push`, `pr-open`, `pr-update`, `pr-comment` |
| 2 | `github-lumen-classic.token` — classic PAT, push as RealLumenHere | uid `lumen` | all RealLumenHere repos | same verbs |
| 3 | worker `~/.ssh` key — the key `fleet-exec run_ssh` (`fleet-exec:174`) uses to reach every node | uid `lumen` | the whole fleet | already verb-mediated by fleet-exec; move the *key* to `tokeneng` and have the daemon run the ssh |
| 4 | `gitea-lumen.token` (`lumen-pr:87`) | uid `lumen` | self-hosted forge, all repos | `pr-*`, `git-*` on `gitea:` repos |
| 5 | LiteLLM gateway key (`fworker.py:1274`) | worker **env**, by design | gateway spend | `gateway-key mint` per ticket with a TTL + budget |
| 6 | `CLAUDE_CODE_OAUTH_TOKEN` (`fworker.py:1235`) | worker env, by design | Anthropic account | stays in env (no short-lived option); keep the pop-then-set discipline |
| 7 | cloudflared tunnel credential | root/systemd | fleet ingress | `api-call cloudflare`; tunnel cred stays root-only, instinct #5 unchanged |
| 8 | eBay OAuth refresh token (`bin/ebay-token`) | uid `lumen` | one marketplace account | `api-call ebay` |

Order of migration = this order. Rank 1–2 are one code change (`lumen-pr`) plus the helper, which is why
they are the first children.

---

## 8. Child tickets, dependency order

All filed. `wq#1619` already existed and is adopted as T8 rather than duplicated (noted on the ticket).

| id | ticket | depends on | gate |
|---|---|---|---|
| **T0** | **wq#2269** — verify the provider TTL matrix (§4) against live docs/APIs from a node with egress; correct this doc | — | §4 has zero `[UNVERIFIED]` rows |
| **T1** | **wq#2271 — git credential helper + token-free remotes.** `git-credential-token-engine`, broker `git-fetch`/`git-push` verbs, per-worktree `credential.helper` config, `remote_credential_sweep --fix` over every clone. **First build child** — the recurring leak | T0 (GitHub row only) | a push from a worktree succeeds with a userinfo-free remote, no token in `git config`, env, or history; sweep reports clean |
| **T2** | **wq#2270** — `token-engine` daemon + `tokeneng` user + socket + systemd hardening (§1, §5.1–5.2, §5.5) | — (T1 can land against a stub that reads today's file, then cut over) | a read of a secret file as uid `lumen` → EACCES; daemon unit active; audit line per call |
| **T3** | **wq#2272** — pre-push hook refusing userinfo + non-engine helper (§3.3), via fleet-owned `core.hooksPath` | T1 | hook refuses a crafted `https://x:y@host/…` push; normal push unaffected |
| **T4** | **wq#2273** — `lumen-pr` stops reading the secret store, calls `pr-open`/`pr-update`/`pr-comment` (§5.3) | T2 | a grep of `bin/lumen-pr` for the secret-store path → no matches; a PR opens end-to-end |
| **T5** | **wq#2274** — guard + allowlist: deny `git remote -v`/`git config -l`/`env`/`printenv` shapes; add `mask-secrets` to the worker allowlist; extend the path deny to `/var/lib/token-engine` (§6) | — | the wq#2243 command shape is refused; a remote listing piped through `mask-secrets` runs |
| **T6** | **wq#2275** — mask the transcript in `fworker` before anything is written to disk/ntfy/episode store; ntfy + auto-ticket on any mask hit (§6) | — | a synthetic `ghp_`-shaped string in child stdout never appears in the transcript file; alert fires |
| **T7** | **wq#2276** — provider-side push protection on every fleet repo; install the `remote-credential-sweep` timer (root step, ISSUE-LOG 540) | — | `systemctl is-enabled remote-credential-sweep.timer` → `enabled`; push protection recorded per repo |
| **T8** | **wq#1619** (pre-existing, adopted) — rotation postcheck that greps the instruction plane + `claude_memory.md` for the old path and fails while the rotator is still at the keyboard | T2 | rotating a token with a stale doc reference fails loudly |
| **T9** | **wq#2277** — ticket-end revocation from `guard.sh --lifecycle end` + daemon-start reaper (§3.4) | T2 | killing a worker mid-ticket leaves no live minted token |
| **T10** | **wq#2278** — `gateway-key`, `api-call`, `plug` verbs + migration ranks 5–8, incl. moving the worker ssh key into the engine (§7) | T2 | each credential in ranks 5–8 unreadable by uid `lumen` |

**Physical acts queued separately, never as gates** (instinct #3): rotate the burned `github-frank.token`
on the RealLumenHere/frankstupak accounts (outstanding from wq#2243); create the GitHub App and install it
on the fleet repos; enable push protection where it needs an account owner.

### 8.1 Landed

| id | landed as | gate evidence |
|---|---|---|
| **T4** (wq#2273) | lumen-bin `1cd720f` "feat(lumen-pr): PR verbs via token-engine broker" (lumen-bin PR #117, merged to `main` 2026-09-20). `load_token`/`resolve_github_token` and all three secret-store token files are gone from `bin/lumen-pr`; every forge call is a broker verb (`pr-open`/`pr-update`/`pr-comment`/`pr-view`/`pr-checks`/`pr-files`/`pr-list`/`pr-merge`) over `/run/token-engine/broker.sock`, title/body staged as worktree file paths under `events/te-pr.*` — never argv — so `check_argv_shape`'s metachar ban stays strict. Push stays on the T1 credential-helper path. | `grep -nE 'github-frank\.token|github-lumen-classic\.token|gitea-lumen\.token|\.secrets|resolve_github_token' bin/lumen-pr` → no matches (the remaining textual hits for `Authorization`/`load_token` are two comments recording that those paths are gone). End-to-end: the PR that landed this very row was opened and then updated through the daemon's `pr-open` (find-or-create-or-update, §5.3); one audit line per call with `"ticket": "wq2273"` in `foreman/events/fleet-exec.jsonl` (dual sink, daemon `AUDIT_PATH`). |
| **T10** (wq#2278) | foreman: `gateway-key`/`api-call`/`plug`/`ssh-run` verbs in `token-engine/token_engine_daemon.py`, `litellm-key` revoker + `revoke_one` in `token_ledger.py`, static config `providers.json`, per-ticket key mint in `fworker.build_env` (`mint_gateway_key`, broker-down fallback to the long-lived key), ranks 5-8 + rank-3 store migration in `install.sh` (COPY until each consumer is on verbs; `TE_FINALIZE_MOVE=1` shreds originals). CLAUDE_CODE_OAUTH_TOKEN stays in worker env by design (rank 6, no provider-side short-lived option); pop-then-set-exactly-one pinned by `harness/tests/test_token_injection.py`. Rank-3 completion (fleet-exec delegating `run_ssh` to `ssh-run`) is the bin-repo follow-up. | `python3 token-engine/tests/run_replay.py t10` → `T10 GATE: PASS`: mint against a loopback gateway returns id/expiry/handoff-path with the value absent from the frame (mask tripwire + schema assertion on every outbound frame), audit line per call in both sinks, `ticket-end` lands `/key/delete` at the gateway and unlinks the handoff file; unit suites `tests/test_daemon.py` (46) + `tests/test_ledger.py` (10) green. |

---

## 9. What this design does not fix

- **A token still crosses into git's process during a helper-mediated push** (§3.1). Bounded by TTL, scope
  and revocation; removed entirely only once `git-push` runs in the daemon.
- **One model credential remains in worker env** (`CLAUDE_CODE_OAUTH_TOKEN`) because no provider-side
  short-lived option exists for it.
- **Nothing protects the model's own context** if a secret is printed — the CLI has no lever (§0). The
  answer is that after migration there is no readable secret to print.
- **Root remains a bypass.** `lumen-run` and any root systemd unit can read `tokeneng`'s files; §5.4
  narrows it but does not close it. Closing it needs the root path itself brokered, which is out of scope
  here and a candidate follow-up.
