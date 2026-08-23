# Bes pre-install baseline (wq#857)

Captured 2026-08-23, this session, via `curl` only (read-only, no target changes).

```
$ curl -s -m 5 -o /dev/null -w "http_code=%{http_code}\n" http://100.110.156.91:9428/health
http_code=000   (connection failed — port not up, as expected pre-install)

$ curl -s http://100.110.156.91:3000/api/health
{"database": "ok", "version": "13.1.1", "commit": "a9cee6e1724a455676bb6c05eef7fc54aa4b19f4"}

$ curl -s http://100.110.156.91:3000/api/datasources
Infinity            yesoreyeram-infinity-datasource
VictoriaMetrics      prometheus   http://127.0.0.1:8428
```

Confirms: Bes reachable over tailscale, Grafana 13.1.1 healthy, no VictoriaLogs
datasource present yet, VictoriaLogs port not yet listening. Existing VM datasource
uses Grafana's built-in `prometheus` type against `127.0.0.1:8428` (loopback-only,
local vmagent scrapes pull from there) — different from VictoriaLogs, which needs the
dedicated plugin and a network-reachable bind address since it's a push target for the
whole fleet, not a pull target for one local agent.

Re-run `verify-victorialogs.sh 100.110.156.91` after `deploy-bes.sh` to compare.
