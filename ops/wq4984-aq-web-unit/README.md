# wq#4984 aq-web systemd unit

`/home/lumen/agents/alert-queue/aq_web.py` is the read-only aq alert page on 127.0.0.1:8766,
published tailnet-only by `tailscale serve` at `https://lumen-1.tail12fa3b.ts.net/alerts`.
It was started by hand with nohup on 2026-11-13 (no unit). By 2026-11-19 05:10 PST the
process was gone and nothing listened on 8766, so /alerts was down.

`lumen-aq-web.service` runs it as `lumen`, `Restart=always`, enabled at boot.

Deploy: `bash install.sh` (kills any hand-started copy, installs + enables the unit,
blesses it in unit-drift). Verify: `bash install.sh --verify` (unit file, enabled, active,
HTTP 200 on /open). Rollback line printed by install.sh.
