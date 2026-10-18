# wq#4184 - Gitea fleet token 10-14 rotation, finish it

## Facts (from the ticket, fleet-night-ed04ec, read-only HTTP+sha12 probe)
- `~/.git-credentials` carries sha12 `4be570ad8964` (== the 10-14 04:53 pre-rotation
  backup) and is what every git op actually authenticates with. HTTP 200 on `/api/v1/user`.
- `.secrets/gitea-lumen.token` carries sha12 `3ab8c1d9555c` (minted 10-14 04:53) and is
  also live, but was never installed into the credential store.
- `gitea-admin.token` (`989db62adce4`) is live; both tokens auth as `lumen`, `is_admin=True`.
- Gitea's token create/list/revoke endpoints 401 under token auth; they need BasicAuth
  (username:password) or the on-host admin CLI.

## Research finding: the ticket's "need Frank's password" premise is stale
The ticket asks for "Frank supplies lumen Gitea password via Cord OR a research prompt
for an on-host route on anubis." Both already exist and are already wired, no Frank
action needed:

- `kb/ISSUE-LOG.md` #72/#110 (wq#1265/wq#1283, 2026-09-02): the on-host route is
  `gitea --config /etc/gitea/app.ini admin user generate-access-token`, run as the
  `gitea` user on Anubis — "needs shell on the gitea host, NOT the account password,
  so it is fleet-autonomous."
- `/home/lumen/bin/gitea-rotate-finish` (wq#2545, hardened after wq#2514's race) is the
  productionized version of exactly this ticket's "SAFE SEQUENCE": acquires the
  `gitea-lumen-token` slock, mints a fresh token on Anubis via that admin CLI over
  `ssh`, validates it before writing anything, writes all 4 copies
  (`.secrets/gitea-lumen.token`, `~/.git-credentials`, `agent/.env`, the token-engine
  store), verifies every consumer (API, `git ls-remote` on every tracked repo, the
  `lumen-pr` broker), deletes the retiring token row, then proves the old value now
  401s. It already reads `GITEA_PASSWORD` out of `agent/.env` (via BasicAuth, for the
  list/delete calls) — that password is already on the fleet, not something Frank needs
  to hand-paste.

So the correct invocation to finish wq#4184 is simply:

```
GITEA_ROTATE_RETIRE_ID=<id of the 4be570ad8964 row> gitea-rotate-finish
```

(the script auto-selects the one `lumen-fleet-*` token to retire if there's exactly one;
pass `GITEA_ROTATE_RETIRE_ID`/`_RETIRE_NAME` explicitly since this ticket's stray
`3ab8c1d9555c` row means there may be two `lumen-fleet-*` candidates — list first with
`curl -u lumen:$GITEA_PASSWORD http://100.121.17.5:3000/api/v1/users/lumen/tokens`).
After it succeeds, separately revoke the never-installed stray
(`DELETE /api/v1/users/lumen/tokens/<3ab8c1d9555c's id>`) — the script only retires the
one id it's told about. Run with no fworkers in flight (5+ hold cached creds; the
script's step 5 verifies consumers but does not itself restart fworker sessions holding
a stale env).

## Why this BUILD-lane session cannot run it
`gitea-rotate-finish` mints over `ssh anubis ...`. This worker's Bash tool is gated by a
per-command allowlist (`harness/worker-settings.json`), and that allowlist has zero `ssh`
entries of any shape — confirmed by direct test: `ssh anubis 'echo test'` is refused with
"This command requires approval" before it ever reaches a host. The same file also lacks
entries for `gitea-rotate-finish`, `rotate-gitea-cred`, or `sudo`, so even a hand-rolled
BasicAuth-over-curl substitute for the ssh mint step would still dead-end at the script's
later `sudo -n` token-engine write and would also be improvising around a vetted,
already-reviewed script — exactly what the ticket says not to do.

This is the same structural gap `kb/ISSUE-LOG.md` #256 (wq#1556) already logged: a
ticket's own needs named `ssh(anubis)`, no BUILD-lane profile grants it, and retrying the
same call doesn't change that (wq#1107's loop-breaker lesson). #256 was left OPEN with two
possible structural fixes — add `ssh anubis` to a BUILD-lane profile, or stop tagging
tickets with a need no lane can satisfy. wq#4184 is a second data point for the same open
gap, this time on a credential-rotation ticket rather than a monitoring one.

## What actually needs to happen
Not a Frank-gate (no password, no personal action) — a dispatch/capability gap. Either:
1. Run `gitea-rotate-finish` from a shell that already has ssh reach to Anubis (lumen's
   own login shell outside this sandboxed harness, or any ops lane whose allowlist
   includes it), or
2. Add a narrowly-scoped allowlist entry for the vetted wrapper itself
   (`Bash(/home/lumen/bin/gitea-rotate-finish*)`) to a BUILD-lane profile — this session's
   own `worker-settings.json` is frozen for the life of this run, so that edit has to
   land from outside this session, not from in here.

Blocked on dispatch, not on Frank. Filed as wq#4184 block; see `kb/ISSUE-LOG.md` for the
append.
