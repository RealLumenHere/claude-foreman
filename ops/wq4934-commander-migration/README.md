# wq#4934: Commander migration to Seneca and dual-tunnel load balancer

State as of 2026-11-18 (Pacific). **The migration is not finished.** This directory holds
the tested tooling and the attended runbook for what is left. A headless worker cannot do
the rest: it needs a purchase, two credentials and Frank driving (see "Frank only").

| File | Purpose |
|---|---|
| `verify_seneca.py` | Phase A. Read-only check of the Seneca node from Lumen. |
| `lb_plan.py` | Phase B. Plan, apply, check, enable and disable the Cloudflare load balancer. |
| `lb_config.example.json` | Config template for `lb_plan.py`. Rejected until the ids are filled in. |
| `cloudflared-seneca.example.yml` | Tunnel config template for Seneca's own tunnel. |
| `test_*.py` | 64 tests, no network: `python3 -m pytest -q ops/wq4934-commander-migration/` |

## Where each phase stands

| Phase | Item | State | Evidence |
|---|---|---|---|
| A | Horus NVMe moved into the Seneca USFS-05-V2 | Recorded done 2026-11-06, not re-checked | lumen_state `seneca.role`, `seneca.hostname` (both "UNVERIFIABLE, no probe") |
| A | unminimize | Recorded NOT done | lumen_state `seneca.status`: "Still minimized", confidence 0.30 |
| A | verify | Not run | `fleet-exec run horus` returns `DENY [grant:grant_missing_env]` for this ticket |
| A | Tailscale on Seneca | Reported logged out | wq#4930 (`NeedsLogin`, seen 2026-11-05), open; lumen_state says "Tailscale up" at confidence 0.30 |
| B | Load balancer, two tunnel pools | Not started | Needs the add-on, a new tunnel and an API token |
| C | Retire Horus | Not started | Old chassis is Frank's; record cleanup in wq#4932, wq#4986 |

Lumen stays the sole Commander carrier until step B4 (`ops/wq4921-commander-lumen/`).

## The approved design could not be read

The ticket points at `/home/lumen/docs/commander-migration/`. That directory is outside
the worker's readable set, and the sanctioned `read_dir_sweep.py` reported `files=0`
for it, which means empty or unlistable. No copy exists in the repo or in any granted
directory. Possible cause, not confirmed: wq#4933 (Commander writes on 2026-11-05/06
were split across two hosts).

So the Phase B design below is **reconstructed** from the ticket text, Cloudflare's
documentation and the repo's earlier Commander work. Frank: compare it with the approved
document before step B3. If they differ, the approved document wins and `lb_config.json`
is the only thing that should need to change.

## Design (reconstructed)

All Cloudflare pages below were fetched 2026-11-18.

- **Load balancer hostname: `mcp-backend.lumennode.uk`.** It is the hostname the mcpw
  worker already calls, so mcpw, its `MCP_BACKENDS` and the Access application stay as
  they are. A load balancer record takes precedence over an equally specific CNAME, and
  when the load balancer is disabled the existing record is served again
  (https://developers.cloudflare.com/load-balancing/load-balancers/dns-records/).
  Enabling is the cutover. Disabling is the rollback.
- **Two pools, one tunnel each.** Pool 1 is Lumen's existing tunnel. Pool 2 is a new
  tunnel that exists only on Seneca. They cannot be two connectors of one tunnel: the
  load balancer "does not distinguish between replicas of the same tunnel", and one pool
  cannot reference a tunnel UUID twice
  (https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/routing-to-tunnel/public-load-balancers/).
  The same page gives the endpoint form `<UUID>.cfargotunnel.com` with the tunnel's
  hostname as the Host header, and says TCP monitors do not work for tunnels.
- **Steering off.** Traffic goes to the first healthy pool in order, so Lumen carries
  everything while it is healthy, Seneca takes over when it is not, and traffic returns
  to Lumen by itself
  (https://developers.cloudflare.com/load-balancing/understand-basics/traffic-steering/steering-policies/standard-options/).
- **Seneca runs its own dc-gateway.** Its tunnel points at `http://localhost:3100` on
  Seneca, not at Lumen. This is what covers Lumen being down. The cost: during a
  failover Commander's shell and files are Seneca's, not Lumen's.
- **Monitor:** HTTPS, port 443, `GET /health`, expect 200, Host header set. On Lumen
  `GET http://localhost:3100/health` returned 200 and a bare `GET /mcp` returned 406
  (checked 2026-11-18).

Not known. Do not assume either way:

- Price of the Load Balancing add-on (searched: "Cloudflare Load Balancing add-on
  pricing per month 2 endpoints 60 second health checks").
- Whether monitor probes are subject to the Access policy on the hostname (searched:
  "cloudflare load balancing monitor health check cloudflare access protected origin
  service token header 403"). If they are, both pools report unhealthy after B3 and
  `check` prints the failure reason. Stop there.
- Whether Cloudflare health-checks pools while the load balancer is disabled. If
  `check` keeps printing "no health data yet", read pool health in the dashboard and
  use `--no-health-gate` for the cutover.
- Whether Lumen's ingress forwards `/health`. Step B0 checks it.
- Whether list calls paginate. `lb_plan.py` assumes each list fits in one response,
  which holds for an account with a handful of monitors, pools and load balancers.

## Frank only

1. Enable the Load Balancing add-on for the Cloudflare account. It needs a billing
   profile (https://developers.cloudflare.com/load-balancing/get-started/enable-load-balancing/).
2. Create an API token with "Load Balancing: Monitors and Pools Write" on the account
   and "Load Balancers Write" on the `lumennode.uk` zone, then store it on Lumen without
   it touching a command line:

       ssh -t lumen@100.117.66.102 'umask 077 && mkdir -p /home/lumen/.config/cloudflare && read -rs -p "paste token: " T && printf %s "$T" > /home/lumen/.config/cloudflare/lb-token && echo saved'

   If `check` later fails with an authentication error, add the two matching Read
   permissions. Whether Write covers the GET calls was not checked.
3. `cloudflared tunnel login` on Seneca (browser sign-in) in step B2.
4. `sudo tailscale up` on Seneca and open the URL it prints (wq#4930).
5. Drive phases A and B over SSH, not through Commander. Nothing here may run
   unattended (lumen_state `commander.rule_no_unattended`).

Seneca commands go through Lumen's `horus-lan` ssh alias because Tailscale on Seneca is
reported logged out. Seneca's recorded LAN address is 192.168.8.209 (lumen_state
`seneca.lan_ip`). Older records for the Horus chassis say .229 and .231 (wq#4932), and
this worker could not see which address the alias uses. `verify_seneca.py` fails on the
wrong node (hostname and CPU checks). If it does, add `--target horus@192.168.8.209` and
use that destination in place of `horus-lan` below.

## Phase A: unminimize and verify

A0. Read-only check. It needs no change on the node.

    ssh lumen@100.117.66.102 'python3 /home/lumen/foreman/ops/wq4934-commander-migration/verify_seneca.py'

All `OK`: Phase A is done, skip to A4. `FAIL os:unminimized: still minimized`: continue.
`FAIL node:hostname` or `FAIL hw:cpu`: stop, the node is not the one this runbook is for.

A1. Make sure the tool is there and read what it will do. The script on the node may
differ from the copy this runbook was written against
(https://gist.github.com/Ch00k/8f7ad2a31a61c8b937e30e265b2f9e54, fetched 2026-11-18),
which runs `apt-get upgrade` and reinstalls every package that ships man pages.

    ssh -t lumen@100.117.66.102 'ssh -t horus-lan "command -v unminimize || sudo apt-get install -y unminimize"'
    ssh lumen@100.117.66.102 'ssh horus-lan "cat \$(command -v unminimize)"'

A2. Back up the dpkg config (rule 6), then run it detached so a dropped SSH session
cannot interrupt dpkg. `yes` answers its prompts, including the upgrade prompt. The
script does not reboot the node. A new kernel, if one is installed, waits for the next
reboot.

    ssh -t lumen@100.117.66.102 'ssh -t horus-lan "sudo cp -a /etc/dpkg/dpkg.cfg.d /etc/dpkg/dpkg.cfg.d.bak.wq4934"'
    ssh -t lumen@100.117.66.102 'ssh -t horus-lan "sudo systemd-run --unit=wq4934-unminimize --collect --setenv=DEBIAN_FRONTEND=noninteractive /bin/sh -c \"yes | unminimize\""'

A3. Watch it. It is finished when the last line is `Success`.

    ssh lumen@100.117.66.102 'ssh horus-lan "journalctl -u wq4934-unminimize -n 15 --no-pager"'

A4. Repeat A0. Expect every line `OK` once Tailscale is logged in again. Then record it:

    ssh lumen@100.117.66.102 'python3 /home/lumen/minerva/lumen_state.py ingest seneca status "ONLINE, unminimized, standby (cloudflared and dc-gateway masked)" --valid-from $(date +%F) --source wq#4934 && /home/lumen/bin/state-regen'

## Phase B: load balancer

B0. Pre-flight, read-only. All three must pass before anything changes.

    ssh lumen@100.117.66.102 'python3 /home/lumen/foreman/ops/wq4921-commander-lumen/verify_topology.py'
    ssh lumen@100.117.66.102 'python3 /home/lumen/foreman/ops/wq4934-commander-migration/verify_seneca.py'
    ssh lumen@100.117.66.102 'grep -n -E "tunnel:|hostname|path|service" /etc/cloudflared-mcp/config.yml'

The `tunnel:` line is Lumen's tunnel UUID for the config in B3. If the ingress has a
`path:` rule that would not match `/health`, do not edit Lumen's live ingress. Set
`"health_path": "/mcp", "expected_codes": "406"` in the config instead.

B1. Start Seneca's own gateway and confirm it answers locally. Expect `200`. Anything
else: stop and bring Seneca's gateway to Lumen's version first.

    ssh -t lumen@100.117.66.102 'ssh -t horus-lan "sudo systemctl unmask dc-gateway.service && sudo systemctl enable --now dc-gateway.service"'
    ssh lumen@100.117.66.102 'ssh horus-lan "curl -s -o /dev/null -w \"%{http_code}\n\" -m 5 http://localhost:3100/health"'

B2. Give Seneca its own tunnel. Same procedure as earlier endpoints
(`ops/wq2607-dc-endpoint-anubis/README.md`, `ops/p1-commander/CHECKLIST.md`).

    ssh lumen@100.117.66.102 'ssh horus-lan "systemctl cat cloudflared.service; ls -l /etc/cloudflared/"'
    ssh -t lumen@100.117.66.102 'ssh -t horus-lan "cloudflared tunnel login"'
    ssh -t lumen@100.117.66.102 'ssh -t horus-lan "cloudflared tunnel create mcp-seneca"'

The first command shows which config file the unit reads. Back that file up with a
timestamped copy, then replace it with `cloudflared-seneca.example.yml`, filling in the
UUID and credentials path that `tunnel create` printed (Frank supplies both; they do
not exist before this step). Do not run `cloudflared tunnel route dns`. Do not start
cloudflared on Seneca with the old mcp-horus credentials. Then:

    ssh -t lumen@100.117.66.102 'ssh -t horus-lan "sudo systemd-run --unit=wq4934-cf-seneca --collect /bin/sh -c \"systemctl unmask cloudflared.service && systemctl enable --now cloudflared.service\""'
    ssh lumen@100.117.66.102 'python3 /home/lumen/foreman/ops/wq4934-commander-migration/verify_seneca.py --carrier live'

B3. Build the load balancer, disabled. Copy `lb_config.example.json` to
`/home/lumen/.config/cloudflare/lb_config.json` and fill in the account id, zone id and
both tunnel UUIDs (Frank supplies the two ids from the Cloudflare dashboard).

    ssh lumen@100.117.66.102 'python3 /home/lumen/foreman/ops/wq4934-commander-migration/lb_plan.py plan --config /home/lumen/.config/cloudflare/lb_config.json'
    ssh lumen@100.117.66.102 'python3 /home/lumen/foreman/ops/wq4934-commander-migration/lb_plan.py apply --attended --config /home/lumen/.config/cloudflare/lb_config.json --token-file /home/lumen/.config/cloudflare/lb-token'
    ssh lumen@100.117.66.102 'python3 /home/lumen/foreman/ops/wq4934-commander-migration/lb_plan.py check --expect disabled --config /home/lumen/.config/cloudflare/lb_config.json --token-file /home/lumen/.config/cloudflare/lb-token'

Nothing is routed through the load balancer yet. Go on only when `check` prints `OK`.
Health data can take a few monitor intervals to appear.

B4. Cutover. `enable` refuses unless the live objects match the plan and both pools
are healthy.

    ssh lumen@100.117.66.102 'python3 /home/lumen/foreman/ops/wq4934-commander-migration/lb_plan.py enable --attended --config /home/lumen/.config/cloudflare/lb_config.json --token-file /home/lumen/.config/cloudflare/lb-token'
    ssh lumen@100.117.66.102 'python3 /home/lumen/foreman/ops/wq4921-commander-lumen/verify_topology.py'

Then make one Commander call from claude.ai. It must land on Lumen (`hostname`).

B5. Failover test. This stops Lumen's connector for five minutes and starts it again by
itself. With a 60 s monitor interval, expect roughly two to three minutes before
Cloudflare marks the Lumen pool down (estimate from the monitor settings, not measured).

    ssh lumen@100.117.66.102 'sudo systemd-run --unit=wq4934-failover-test --collect /bin/sh -c "systemctl stop cloudflared-mcp.service; sleep 300; systemctl start cloudflared-mcp.service"'

Inside the window, a Commander `hostname` call from claude.ai must answer `seneca`.
Afterwards (rule 7) confirm the connector is back and both pools are healthy:

    ssh lumen@100.117.66.102 'systemctl is-active cloudflared-mcp.service'
    ssh lumen@100.117.66.102 'python3 /home/lumen/foreman/ops/wq4934-commander-migration/lb_plan.py check --expect enabled --config /home/lumen/.config/cloudflare/lb_config.json --token-file /home/lumen/.config/cloudflare/lb-token'

A later Commander `hostname` call must answer `lumen` again.

B6. Rollback, at any point after B4. The CNAME serves again and Lumen is the sole
carrier, as before. Allow for the DNS TTL.

    ssh lumen@100.117.66.102 'python3 /home/lumen/foreman/ops/wq4934-commander-migration/lb_plan.py disable --attended --config /home/lumen/.config/cloudflare/lb_config.json --token-file /home/lumen/.config/cloudflare/lb-token'

B7. Record the new topology in lumen_state (`commander.*`, `seneca.status`,
`horus.role`), run `/home/lumen/bin/state-regen`, and mark section A of
`ops/wq4921-commander-lumen/README.md` (the relay standby) as superseded.

## Phase C: retire Horus

After B5 has passed and the load balancer has run without a rollback for a soak period
(suggested: one week):

1. Remove the old mcp-horus replica credentials from Seneca. Lumen keeps that tunnel.
2. Fix the stale Horus records: roster addresses (wq#4932) and the "Commander runs on
   Horus" text (wq#4986). The node's ssh user and roster subject are still `horus`;
   renaming them is a separate change.
3. Frank: the old Horus chassis no longer holds the NVMe. Disposing of it is a physical task.
