# wq#3700 — judgment: wq#3653 OPNsense + Tailscale + Pi-hole + exit-node runbook

Verifier: claude-sonnet-5@high, worktree `wq3700-a1`, 2026-10-08. Judge (escalated on
contradiction): claude-opus-5@high, same session. Target: PR
http://100.121.17.5:3000/lumen/claude-foreman/pulls/1724, merged to `main` as `9caa3b64`,
bundle `ops/wq3653-opnsense-tailscale-pihole-a1/`. Verify/judgment only — no code in the
bundle's scripts was changed, nothing was applied to any live box, no ssh to Topton/Pi-hole/
Flint 3 was attempted (they are unreachable from this lane, same wall the author hit).

## Verdict: CONTRADICTION CONFIRMED

The bundle is well-researched and the RUNBOOK/README/TEST_PLAN are sound, but one of the two
shipped scripts — `pihole_fleet_bypass.py` — does not implement what it claims to against the
real Pi-hole v6 API. This was not caught before merge because nothing is live yet, so the
author never ran it against a real (or schema-faithful) server. wq#3653 remains correctly
`done` per its own ticket (staging a runbook for boxes nobody can reach is exactly what was
asked, and `wq done`'s own override path already handles "a sandboxed worker cannot do the
root install itself" — see `ops/VERIFY_CONTRACT.md` "What consumes it"), but its deliverable
is not yet correct, and this verify ticket does not confirm it as-is. A follow-up fix ticket,
**wq#3707**, is filed and carries the fix scope below; it is a prerequisite before anyone runs
this script against the real Pi-hole.

## What I (sonnet) found first

`setup_bypass_group()` (`pihole_fleet_bypass.py:63-79`) POSTs `/api/groups` to create
`Fleet-Bypass` and discards the response — no `id` is ever read out of it. It then POSTs
`/api/clients` with `{"client": client, "groups": ["Fleet-Bypass"]}` for every fleet LAN host
and tailnet CIDR (`:75-76`) — the literal group **name** string, not an id.

Primary-source check (`fleet-search github --code "repo:pi-hole/FTL filename:clients.yaml"`,
2026-10-08): `src/api/docs/content/specs/clients.yaml` — `groups: description: Array of group
IDs`. That is pi-hole/FTL's own OpenAPI schema for the exact endpoint this script calls.

To make that concrete I wrote a schema-faithful local mock
(`/tmp/fworker-wq3700/mock_pihole.py` — not committed, scratch probe only) that accepts
`/api/auth`, returns a created group with an integer `id` from `/api/groups`, and rejects any
non-integer `groups` entry on `/api/clients`. Running the **unmodified, shipped**
`pihole_fleet_bypass.py --base-url http://127.0.0.1:8953 --password x --bypass-group
--confirmed` against it raised an uncaught `urllib.error.HTTPError: HTTP Error 400: Bad
Request` from inside `setup_bypass_group`'s first `/api/clients` call. That reproduction is
real (the traceback is pasted in this session's tool output), but — per the judge's
correction below — it models the wrong failure mode for the real server.

## Escalation: opus-5@high judgment (independent re-derivation)

Spawned fresh, given only the file paths and my claim, told explicitly not to trust my
snippet or mock and to re-derive everything from pi-hole/FTL's actual source. Its findings,
verbatim except for trimming:

> **Verdict: CONFIRMED, with a correction.** The bug is real: the wrong value type goes into
> `groups`, and Fleet-Bypass membership never happens. But your failure mode is wrong. Real
> FTL does not return HTTP 400 for string group entries. It skips them, deletes the client's
> existing group links, and returns 201. Your mock does not match the real server, so the
> uncaught-traceback repro is not valid evidence.
>
> **Primary sources** (raw.githubusercontent.com/pi-hole/FTL/master, copies in
> `/tmp/fworker-wq3700/`):
> - `src/api/docs/content/specs/clients.yaml:397-402`: `groups: ... type: array, default:
>   [0], items: type: integer` — independently confirms the sonnet snippet.
> - `groups.yaml`: POST 201 response is `groups/get` (carries the read-only integer `id`); a
>   correct client reads `groups[0].id`.
> - `src/api/list.c:~570-577`: `gravityDB_addToTable()` inserts the client first, then
>   `gravityDB_edit_groups()` runs.
> - `src/database/gravity-db.c` `edit_groups()`: deletes all existing group associations
>   (including the Default (0) link the insert trigger just added), then loops `if(group ==
>   NULL || !cJSON_IsNumber(group)) continue;` — strings are silently dropped, no error. The
>   call still returns `okay=true` and the API answers 201.
> - `gravity-db.c:1702/1778`, `:1843-1847`: a client in zero groups gets `NOT_FOUND` on
>   gravity/denylist lookups — "Client %s is in no group, no regex applies".
>
> **Severity**: fails silently, not loudly. Each listed client ends up in **no group at all**,
> not Default and not Fleet-Bypass. By luck, in current FTL that happens to mean unfiltered
> (no adlists, no deny-list match applies) — roughly the P1/P2 outcome wanted — but it depends
> on undocumented zero-group behavior, the Fleet-Bypass group itself is created and never
> used, and the state will not survive someone re-adding the client in the Pi-hole UI later
> (which re-links Default). DNS does not fail closed; the deliverable "implements the
> Fleet-Bypass mechanism" is false regardless.
>
> **Other defects found independently, all primary-sourced:**
> - **Item 9 (`setup_conditional_forwarding`, :82-97) is broken twice**: `/api/config` only
>   defines `patch`/`put` (`config.yaml:34,111`), the script POSTs; and `dns.revServers` is a
>   `CONF_JSON_STRING_ARRAY` of `"<enabled>,<cidr>,<server>[#port][,<domain>]"` strings
>   (`config.c:606-611`), not the object the script sends. This one fails loudly.
> - **The `/api/docs` pre-flight can never pass**: FTL 301-redirects `/api/docs` to
>   `/api/docs/`, which serves `index.html` (`src/api/docs/docs.c:15-37`), not JSON.
>   `json.loads` throws, the broad `except` prints "refusing to write blind", and the script
>   exits 1 every run — training the operator to skip straight to `--confirmed` without ever
>   seeing a real shape check.
> - **Not idempotent**: a re-run's `POST /api/groups` or `POST /api/clients` against an
>   existing name/client returns `400 database_error "already present"` (`groups.yaml`
>   duplicate example) as an uncaught `HTTPError`. The correct call for an existing client is
>   `PUT /api/clients/{client}`.
> - Root cause stated in the script's own docstring: only `/api/auth` was pinned to upstream
>   docs; groups/clients/config were pattern-matched off "the Pi-hole v6 web UI's own page
>   names", not verified against the schema.
> - Minor: no `DELETE /api/auth` (sessions never logged out); a wrong `--password` gives an
>   uncaught 401 traceback; **TEST_PLAN.md 8a/8b would still pass** with the buggy script
>   (client in zero groups still gets clean, unblocked answers under current FTL behavior), so
>   the shipped acceptance probe cannot catch this bug either.

I re-checked the judge's two load-bearing citations myself against the same repo
(`pi-hole/FTL`, `src/api/docs/content/specs/clients.yaml` and `groups.yaml`) via
`fleet-search github --code` and they match what's quoted above.

## Why this is a judgment trigger, not a simple confirm

The contradiction is specific and falsifiable (file:line on both sides, primary-source
schema, a reproduced run), it is load-bearing (RUNBOOK item 8 / README call Fleet-Bypass the
central mechanism keeping Pi-hole from blocking fleet DNS), and my own first-pass reproduction
modeled the wrong failure mode — which the escalation caught and corrected with better
evidence than I had. That is exactly the case this archetype's opus-5@high escalation step
exists for.

## Bundle verify-contract compliance (separate from the above)

wq#3700 named this bundle as missing a `--verify` handler
(`ops/VERIFY_CONTRACT.md`/wq#1696). Added `ops/wq3653-opnsense-tailscale-pihole-a1/install.sh`
in this worktree: a literal `--verify` case arm, read-only, checking only the two RUNBOOK
items with a real author-assigned remote identifier to anchor on (OPNsense's tailscale
hostname `opnsense-topton`, set by RUNBOOK.md item 2) — plugin installed (item 1),
authenticated (item 2), and the accept-dns=false loop-prevention invariant (item 8d,
`NETWORK_PREMORTEM.md` §0.4). Pi-hole and the Flint 3 were never assigned a hostname by this
ticket, so there is nothing non-fabricated to ssh to for them yet; that gap is noted in the
script's own header for whoever names them later. Could not execute `install.sh --verify` in
this sandbox to confirm its own behavior end-to-end: neither `ssh` nor `bash <path>`/direct
execution of an `ops/*/install.sh` is on this lane's Bash allowlist
(`harness/worker-settings.json` has `Bash(bash scripts/*)` but nothing for `ops/`, and no
`ssh` entry at all) — the same reachability wall the README already documents, plus a second,
narrower gap (no in-repo script execution permission) worth widening if a future worker needs
to self-test an `install.sh` it just wrote. Verified by code reading instead: `vremote_up`
returns `ERR unreachable` (exit 2) on an unresolvable host per `ops/_verify.sh:114-119`, which
`ops-deploy-drift` (`/home/lumen/bin/ops-deploy-drift:104-106,160`) never alerts on — only
`VERIFY_FAIL` (exit 1) past 24h opens an aq row — so this handler is safe to land now even
though the host cannot be reached yet; it will start reporting real `MISS`/`OK` the day
OPNsense actually joins the tailnet.

## What I checked myself

- `lumen_busy check` at session start: stale key from an unrelated prior task (`wq3658`,
  pid 3534581, dead) — cleared, then set my own.
- `git -C /home/lumen/foreman log --oneline --grep wq3653`, `git show --stat` on both
  `9caa3b64` (on `main`) and `75bf6b2e` (superseded duplicate, same diff) to confirm what
  actually landed.
- Read README.md, RUNBOOK.md (all 10 numbered steps — the ticket's "9-item" count in the
  commit message groups RUNBOOK items 8+9 as one ticket item; not a real discrepancy), and
  TEST_PLAN.md in full.
- Read both shipped scripts in full; `python3 -m py_compile pihole_fleet_bypass.py` (clean).
- `grep -n -- "--verify"` across `ops/wq2808-tailnet-raw-lockdown` and
  `ops/wq3527-dc-gateway-loopback-a1` (two other staged/unreachable-host bundles): neither
  has a handler either — this is a pre-existing pattern across similarly-blocked bundles, not
  unique to wq#3653 (worth a sweep ticket if the contract is meant to be universal).
- `ops/DEPLOY_TRIAGE.md` and `ops/_verify.sh` in full for the contract's actual rules
  (`vremote_up`/ERR-not-MISS, the wq2500 install.sh as the closest prior-art shape for a
  remote-operator script with a `--verify`).
- fleet-search (web/github --code) for the Pi-hole v6 API schema; GitHub code-search rate
  limit was down to 6 by the end — stopped there rather than spending the rest confirming an
  already-clear citation.

Not checked / explicitly out of scope: any live state on Topton, the Pi-hole RPi, or the
Flint 3 (none reachable, none of this ticket's business per the ongoing instinct-3 Frank
dependency already recorded in wq#3653's own README). The IDS/IPS and VLAN-regression
checklists in TEST_PLAN.md (only relevant post-cutover).

## Disposition

- **wq#3653**: left `done` (status unchanged — staging the runbook for unreachable boxes was
  the ask, and it was met); this verify ticket is the record that its Pi-hole script needs a
  fix before anyone runs it.
- **wq#3707** (new, lane `build`, pri 2): fix the four `pihole_fleet_bypass.py` defects above.
  Carries a liveness probe (schema-faithful mock run, all-integer `groups` payload) so it is
  falsifiable rather than a prose-only acceptance.
- `ops/wq3653-opnsense-tailscale-pihole-a1/install.sh` added in this worktree, landing via
  `lumen-pr` alongside this report.
