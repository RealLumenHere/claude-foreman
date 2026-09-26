# wq#2769 -- wq#2751 residuals: ghost-webhook, ollama :11434, unaudited container ports

## 1. messenger `/ghost-webhook` unauth POST -- FIXED (code landed, restart pending)
Route was in `_PUBLIC_EXACT` (no auth) and Popen'd the indexer + pushed attacker-controlled
`title` text to ntfy on every POST. Investigated whether to add a Ghost signature check (the
ticket's suggested fix) vs. remove outright: Ghost CMS itself has been fully decommissioned on
this fleet (no container, no config -- legal-liability removal, 2026-05-13 state notes) and the
route shows **zero hits in retained messenger logs**. No legitimate caller exists to
authenticate against, so removal is the correct fix, not a signature check protecting nothing.

Landed: `lumen-messenger` PR#4, commit `a1cbc56300` (`server_v2.py`: dropped from
`_PUBLIC_EXACT`, handler deleted, comment left for future Ghost redeploy). `py_compile` clean.

**NOT restarted.** Per wq#2751's own control-plane rule (8765 ingress = Frank-driving, never
unattended, since it fronts `chat.lumennode.uk` live), the `lumen-messenger` restart to pick this
up is left for Frank. One-line, no flags needed once he's ready:
`ssh lumen@100.117.66.102 'sudo systemctl restart lumen-messenger'`

## 2. ollama `*:11434` anon on tailnet -- FIXED, verified, persisted
Root cause (not previously documented this way): `ts-input`'s `ACCEPT all -- tailscale0`
(filter table) fires **before** any of the existing per-port "lockdown" rules are ever reached --
so the pre-existing `lumen-11434-lockdown-drop` rule has never actually blocked tailnet traffic,
only LAN/docker. Confirmed the same dead pattern exists for the port-3100 lockdown rule too (out
of scope here -- reserved for wq#2768, Frank-driven-only per the same control-plane rule).

Identified the one legitimate remote consumer via 14 days of ollama's own access log: Bes
(`100.110.156.91`), polling read-only `GET /api/tags` every ~72s (monitoring/dashboard). No other
tailnet peer has ever connected. A naive loopback-only rebind would have broken that.

Fix: `iptables -t raw -A PREROUTING -i tailscale0 -p tcp --dport 11434 ! -s 100.110.156.91 -j DROP`.
The `raw` table's `PREROUTING` chain runs before conntrack/nat and before the filter table
entirely, and is not one of the chains `tailscaled` manages (it only owns `ts-input`/`ts-forward`
in `filter`), so this survives `tailscaled` reconciling its own rules. `0.0.0.0` bind and
LAN/docker access left unchanged -- only the tailnet gap closed.

Verified live: Thor -> connection refused instantly (was open before). Bes's monitoring poll
succeeded again immediately after the rule went in (watched it in the journal). Local
(127.0.0.1) and docker-bridge (172.17.0.1) access both still `HTTP_200`. Persisted via
`netfilter-persistent save`, confirmed present in `/etc/iptables/rules.v4`. Recorded as a fleet
fact: `lumen_state.py current lumen ollama_firewall`.

**Follow-up filed:** wq#2808 -- the `ts-input`-ordering class bug generally (any future
"lockdown"-named filter-table rule on this box is dead against tailnet traffic by the same
mechanism; port 3100's existing lockdown rule is a second known instance, left untouched here).

## 3. Container-published `0.0.0.0` ports -- audited, 1 fixed, 5 no action needed

| Port | Service | Finding | Action |
|---|---|---|---|
| 3080 | LibreChat | `/api/models` -> 401 without auth; `registrationEnabled: false`. Properly gated. | None |
| 5006 | actual-budget (Frank's personal budget app) | **`bootstrapped: false` -- no password has ever been set.** Zero established connections or request-log evidence of any use since setup in June. Sibling `actual-nginx` sidecar (same compose file) was already correctly bound `127.0.0.1:5007` only -- the raw `5006:5006` publish to all interfaces was clearly an oversight, not a design choice. | **Fixed**: rebound to `127.0.0.1:5006:5006` in `docker-compose.yml` (backup: `docker-compose.yml.bak.20260926T060406Z_wq2769_loopback`), container recreated, verified: Thor now connection-refused, local `127.0.0.1` and the `actual-nginx` sidecar (`127.0.0.1:5007`) both still `HTTP_200`. **FRANK ACTION STILL OPEN: the app has no password at all -- visit it locally and complete the bootstrap/set a password.** Not something I'll pick for you. |
| 5959 | glance | `docker-containers` widget confirmed read-only (lists containers/status only -- Glance has no start/stop/exec control via that widget). Mounts `docker.sock` but nothing in this deployment's config exposes a write path through it. No auth, but info-disclosure only (container names, power-plug wattage, bookmarks, GitHub release radar) and a plausible legitimate use case (checked remotely, unlike actual-budget). | None -- same bucket as the read-only ports wq#2751 already left bound by design. |
| 8080 | ntfy | `NTFY_AUTH_DEFAULT_ACCESS=deny-all`. Properly access-controlled by default. | None |
| 8082/8443 | Dify (via docker-nginx-1) | Setup already completed 2026-05-23 (`"step":"finished"`); console API returns 405 without valid method/auth, not an open bypass. | None |
| 8418 | gatus | Read-only status API confirmed (`GET` returns health data, `POST` to the same route -> 405). No write endpoint found. | None |

## Summary
Fixed and verified: ollama tailnet exposure, actual-budget tailnet exposure, ghost-webhook
unauth POST (code landed, restart pending on Frank). Confirmed-adequate: LibreChat, ntfy, Dify,
glance, gatus. Open for Frank: (1) restart `lumen-messenger` when convenient, (2) set an actual
password on actual-budget -- it currently has none.
