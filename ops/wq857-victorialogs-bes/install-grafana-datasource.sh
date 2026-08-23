#!/usr/bin/env bash
# wq#857 — install the official VictoriaLogs Grafana plugin and provision it as
# a datasource, on Bes's existing Grafana 13.1.1. Root required (grafana-cli
# writes to /var/lib/grafana/plugins, provisioning dir is root-owned, and the
# restart needs systemctl). Run this ON Bes as root, after install-victorialogs.sh.
#
# The plugin (victoriametrics-logs-datasource) is Grafana-catalog signed
# ("commercial" signatureType, confirmed via grafana.com/api/plugins/... this
# session) — no allow_loading_unsigned_plugins change needed, unlike some other
# community VictoriaMetrics plugins.
#
# Usage: install-grafana-datasource.sh <tailscale-ip-of-this-host>
set -euo pipefail

VL_LISTEN_ADDR="${1:?usage: install-grafana-datasource.sh <tailscale-ip-of-bes>}"

echo "== installing victoriametrics-logs-datasource plugin =="
grafana-cli plugins install victoriametrics-logs-datasource

echo "== provisioning datasource =="
sed "s/__VL_LISTEN_ADDR__/${VL_LISTEN_ADDR}/g" \
  "$(dirname "$0")/grafana-datasource-victorialogs.yaml.template" \
  > /etc/grafana/provisioning/datasources/victorialogs.yaml

echo "== restarting grafana-server =="
systemctl restart grafana-server
sleep 3
curl -sf http://127.0.0.1:3000/api/health && echo
echo "== done: VictoriaLogs datasource provisioned, LogsQL available in Explore =="
echo "Rollback: rm /etc/grafana/provisioning/datasources/victorialogs.yaml && grafana-cli plugins uninstall victoriametrics-logs-datasource && systemctl restart grafana-server"
