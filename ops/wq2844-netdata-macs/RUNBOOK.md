# wq#2844 — netdata on the MacPro6,1 nodes

Why: `ops/fan_floor_alarm.py` (wq#2510) reads `sensors.*` charts from netdata on
`<tailscale-ip>:19999`. hathor, nephthys and khepri had no netdata, so the alarm was blind
on the node that kept dropping. This is NOT the wq#394 canary installer (that one pipes
kickstart.sh into root and adds a 48h sampler).

Apply (per node, as root on the node; needrestart must be list-only — the installer checks):

    scp -r /home/lumen/foreman/ops/wq2844-netdata-macs <node>:/tmp/
    ssh <node> 'sudo bash /tmp/wq2844-netdata-macs/install.sh <node-tailscale-ip>'

Verify (read-only, from Lumen): `bash ops/wq2844-netdata-macs/install.sh --verify`

Facts worth knowing:
- netdata.conf is a packaged conffile; the original is kept once as `netdata.conf.orig-wq2844`.
- `netdata` hard-depends on the ebpf/perf/nfacct/slabinfo/otel plugins (`--no-install-recommends`
  changes nothing). isis runs the same set at ~430 MB / ~3% of one core, so it is left as-is.
- The alarm needs a fully covered 60-min window: a fresh install returns no verdict for ~1 h.
  During warm-up `/api/v1/charts` can also return partial JSON; the alarm treats that as no verdict.
- Never upgrades an installed netdata (fleet-update flow) and never reboots.
- Rollback: `apt-get remove --purge netdata*` and delete /etc/apt/sources.list.d/netdata.sources.
