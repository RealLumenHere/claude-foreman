# wq#2844 verify-pass: hathor/khepri netdata :19999 reachable, fan-floor alarm gets a verdict

**Verdict: FIXED, confirmed live.** The reachability gap wq#2510 hit (only isis had a
verdict; hathor/khepri/nephthys netdata `:19999` timed out from Lumen, kb ISSUE-LOG #868)
was root-caused as "netdata absent on those Macs," not a bind/firewall issue on an
existing install — none of the three MacPro6,1 nodes ran netdata at all. The fix, already
on `main` as `ba599b2f` ("feat(ops): add production netdata bundle for the Mac Pros",
`Refs: wq#2844`), installs the same netdata 2.11.x package isis runs and binds it to the
node's own tailnet IP:

- `ops/wq2844-netdata-macs/netdata.conf.template:8` — `bind to = TAILSCALE_IP:19999`
- `ops/wq2844-netdata-macs/netdata.conf.template:9-14` — `allow ... from = localhost 100.*`
  (tailnet-only, same allow-list shape as isis's canary config)
- `ops/wq2844-netdata-macs/install.sh` — idempotent per-node installer + `--verify`
  read-only check, run over SSH per `RUNBOOK.md`

## What was verified live (this session, from Lumen, 2026-09-28)

Direct `curl` from Lumen to each node's netdata on the tailnet, no proxy/mirror hop
(`mirrored_hosts` in each response names only that node itself):

- `curl -s -m 5 http://100.80.71.13:19999/api/v1/info` (hathor) — 200, `mirrored_hosts:
  ["hathor"]`, `reachable: true`
- `curl -s -m 5 http://100.120.16.78:19999/api/v1/info` (khepri) — 200, `mirrored_hosts:
  ["khepri"]`, `reachable: true`
- `curl -s -m 5 http://100.100.129.24:19999/api/v1/info` (isis, control) — 200, unchanged

`python3 ops/fan_floor_alarm.py check` (live, this session):

```
isis      raise=False clear=True te0t=72.2 fan=1100   action=hold
hathor    raise=False clear=True te0t=76.9 fan=1100   action=hold
nephthys  raise=False clear=True te0t=34.3 fan=1100.0 action=hold
khepri    raise=False clear=True te0t=73.7 fan=1099.5 action=hold
thor      raise=None  clear=None te0t=None fan=None   action=notify no netdata source
```

hathor and khepri (this ticket's scope) both return a real `raise`/`clear` verdict, not
null — the acceptance bar the ticket set. nephthys, scoped in by a prior note on this
ticket (off-by-policy concern superseded once it was confirmed running and blind), verifies
the same way. thor stays `None` by design (instinct 2 — notify-only, no netdata source).

`python3 -m pytest ops/tests/test_wq2844_netdata_macs.py -q` — 7 passed.

Matches the ticket's own `wq#2844 detached verify 15:20Z` note (nephthys/hathor/khepri
PASS with the same te0t_mean/fan_max shape), reproduced independently in this session
rather than taken on the note's word.
