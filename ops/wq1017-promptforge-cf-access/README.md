# wq#1017 — script the prompts.lumennode.uk Access add

## Problem

`prompt-forge/INSTALL.md` step 4 asks a human to add
`prompts.lumennode.uk` to the Cloudflare Access application that already
fronts `home.lumennode.uk`, by hand, in the CF dashboard, before DNS is
routed. One-off manual Access edits are exactly the failure class already
logged once (`/home/lumen/kb/ISSUE-LOG.md` #37 — a stored Access-mgmt token
went dead and nothing noticed until Frank re-minted it by hand).

## What this adds

`add_access_hostname.py` — looks up the Access app that already covers
`--front-hostname` (default `home.lumennode.uk`) via
`GET .../access/apps`, then idempotently appends `--add-hostname` to that
app's `self_hosted_domains`. Never creates a new app, never removes an
existing domain, no-ops if the hostname is already present. Refuses (rather
than guesses) if zero or more than one app matches the front hostname.

Request shape (`_cf_get`, token-file convention) reused verbatim from
`foreman/ops/wq1373-cf-token-liveness/cf_token_liveness.py:64-72`.

## Token: not found in this worker's sandbox

Per instinct 7 (never fabricate a value you can't verify): this worker
searched every directory in its `--add-dir` scope
(`/home/lumen/mailbot`, `foreman`, `bin`, `kb`, `agents/work-queue`,
`projects/nest`, this worktree) for an Access-scoped CF token file and found
none — same gap `foreman/ops/wq1373-cf-token-liveness/README.md` already
documented ("the remaining fleet tokens named in the ticket (the #37
Access-mgmt token...) live in `claude_credentials.md` and/or on hosts
outside this worker's `--add-dir` scope"). The wq#1017 ticket note claiming
the fleet now holds a minted Access-mgmt token (2026-09-02) could not be
verified from here.

This ships as a config-only, ready-to-run artifact regardless: point
`--token-file`/`--account-id-file` at the real paths once confirmed and it
runs as-is, no code change required — same precedent as wq#1373's pending
tokens.

## Run

```bash
python3 add_access_hostname.py \
  --add-hostname prompts.lumennode.uk \
  --token-file /path/to/access_mgmt_token \
  --account-id-file /home/lumen/mailbot/.cf_account_id \
  --dry-run   # drop once verified
```

## Still out of BUILD-worker scope

Adding the hostname to Access is one piece of `prompt-forge/INSTALL.md`'s
step 4. Steps 3 (`sudo systemctl enable --now lumen-prompt-forge`), the
`/etc/cloudflared/config.yml` ingress edit + `cloudflared tunnel route dns`
+ `sudo systemctl restart cloudflared`, and step 5 (glance container
restart) all need root/docker, which this repo's guard hook denies BUILD
workers by design (same staged posture as wq#957's own INSTALL.md and the
wq#718 dashboard) — unaffected by whether an Access token is available.

## Tests

```
python3 -m unittest discover -s tests -p "test_*.py"
```

11 tests, all offline (CF calls mocked) — matching lookup, ambiguous-match
refusal, no-app-found, idempotent no-op, dry-run, live PUT payload shape,
and PUT-failure propagation.
