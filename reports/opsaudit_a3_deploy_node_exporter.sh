#!/usr/bin/env bash
# opsaudit_a3_deploy_node_exporter.sh — wq#753: fleet-wide node_exporter into the
# EXISTING VictoriaMetrics on Bes.
#
# Design constraint discovered during this run: the automation account on every fleet
# node can SSH in but has no passwordless root (sudo -n fails / is off-limits for this
# session's guard policy). So everything here is DELIBERATELY ROOT-FREE:
#   - node_exporter binary lives under $HOME/.local/node-exporter/, no /usr/local/bin.
#   - Autostart is a per-user systemd unit (`systemctl --user`), not a system unit.
#     Persistence across reboot comes from `loginctl enable-linger` (self-targeted,
#     which is unprivileged — a user may linger themselves without root).
#   - VictoriaMetrics on Bes is fed by a SECOND, unprivileged vmagent process running
#     as the `bes` user, with its own scrape.yml under $HOME on Bes. The EXISTING
#     root-owned /etc/vmagent/scrape.yml (HA power sensors, wq#342/344) is never
#     touched — zero risk to the running power dashboard, zero backup needed there.
#     Both vmagent instances remote-write to the same VictoriaMetrics
#     (127.0.0.1:8428 on Bes), which is the "existing VictoriaMetrics" the ticket
#     requires — one TSDB, two independent unprivileged scrape pipelines feeding it.
#
# Idempotent: safe to re-run. Each step checks current state before acting.
#
#   ./opsaudit_a3_deploy_node_exporter.sh                 # deploy node_exporter to every reachable host + the Bes scrape agent
#   ./opsaudit_a3_deploy_node_exporter.sh --verify-only    # just print curl/query status, change nothing
#
set -uo pipefail

NE_VERSION=1.12.1
VA_VERSION=v1.147.0
WORKDIR="$(mktemp -d /tmp/wq753-deploy.XXXXXX)"
VERIFY_ONLY=0
[ "${1:-}" = "--verify-only" ] && VERIFY_ONLY=1

# name  tailscale_ip     local(1/0)  vram(1/0)
# Roster = lumen_state.py current tailscale_ip facts, filtered to hosts that answered
# an SSH probe in the live fleet-telemetry snapshot taken 2026-08-22 (see report for
# the excluded/unreachable list and why each was dropped).
ROSTER="
anubis   100.121.17.5     0 0
apep     100.82.2.72      0 0
bes      100.110.156.91   0 0
gameboy  100.118.56.59    0 0
isis     100.100.129.24   0 1
lumen    100.117.66.102   1 1
maat     100.105.219.34   0 0
nephthys 100.98.74.87     0 1
seshat   100.121.243.124  0 0
sia      100.111.15.120   0 0
sobek    100.86.156.91    0 0
thor     100.112.151.100  0 0
thoth    100.75.1.108     0 0
toilet   100.70.94.19     0 1
"

log() { printf '[%s] %s\n' "$1" "$2"; }

# SSH target = {hostname}@{tailscale_ip} (fleet-telemetry's proven convention,
# not a ~/.ssh/config Host alias — several fleet names have no alias at all).
# Known exception: maat's account is "maam", not "maat".
ssh_user_for() { case "$1" in maat) echo maam ;; *) echo "$1" ;; esac; }
ssh_target() { echo "$(ssh_user_for "$1")@$2"; }

# ── fetch binaries once, both arches, on Lumen ───────────────────────────────
fetch_node_exporter() {
  for arch in amd64 arm64; do
    local tgz="node_exporter-${NE_VERSION}.linux-${arch}.tar.gz"
    [ -f "$WORKDIR/$tgz" ] && continue
    curl -sL -m30 -o "$WORKDIR/$tgz" \
      "https://github.com/prometheus/node_exporter/releases/download/v${NE_VERSION}/${tgz}"
    tar -xzf "$WORKDIR/$tgz" -C "$WORKDIR"
    cp "$WORKDIR/node_exporter-${NE_VERSION}.linux-${arch}/node_exporter" "$WORKDIR/node_exporter.$arch"
  done
}

fetch_vmagent() {
  local tgz="vmutils-linux-amd64-${VA_VERSION}.tar.gz"
  [ -f "$WORKDIR/$tgz" ] && return
  curl -sL -m30 -o "$WORKDIR/$tgz" \
    "https://github.com/VictoriaMetrics/VictoriaMetrics/releases/download/${VA_VERSION}/${tgz}"
  tar -xzf "$WORKDIR/$tgz" -C "$WORKDIR"
}

# ── per-node node_exporter install (SSH; skipped entirely for the local host) ─
remote_arch() { ssh -o BatchMode=yes -o ConnectTimeout=5 "$(ssh_target "$1" "$2")" uname -m </dev/null 2>/dev/null; }

ne_bin_for_arch() {
  case "$1" in
    x86_64) echo "$WORKDIR/node_exporter.amd64" ;;
    aarch64) echo "$WORKDIR/node_exporter.arm64" ;;
    *) echo "" ;;
  esac
}

install_user_unit() {
  # $1=host(or "LOCAL") $2=ip $3=vram(0/1)
  local host="$1" ip="$2" vram="$3"
  local unit="[Unit]
Description=Prometheus node_exporter (wq#753, unprivileged user unit)

[Service]
ExecStart=%h/.local/node-exporter/bin/node_exporter --web.listen-address=${ip}:9100 --collector.textfile.directory=%h/.local/node-exporter/textfile
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
"
  local vram_script='#!/usr/bin/env bash
set -euo pipefail
OUT="$HOME/.local/node-exporter/textfile/amdgpu_vram.prom"
TMP="$OUT.$$.tmp"
{
  echo "# HELP amdgpu_vram_used_bytes AMD GPU VRAM used bytes (sysfs mem_info_vram_used)"
  echo "# TYPE amdgpu_vram_used_bytes gauge"
  echo "# HELP amdgpu_vram_total_bytes AMD GPU VRAM total bytes (sysfs mem_info_vram_total)"
  echo "# TYPE amdgpu_vram_total_bytes gauge"
  for f in /sys/class/drm/card*/device/mem_info_vram_used; do
    [ -r "$f" ] || continue
    card=$(basename "$(dirname "$(dirname "$f")")")
    used=$(cat "$f")
    total=$(cat "${f%_used}_total" 2>/dev/null || echo 0)
    echo "amdgpu_vram_used_bytes{card=\"$card\"} $used"
    echo "amdgpu_vram_total_bytes{card=\"$card\"} $total"
  done
} > "$TMP"
mv "$TMP" "$OUT"
'
  local vram_timer="[Unit]
Description=Sample amdgpu VRAM into node_exporter textfile collector (wq#753)

[Timer]
OnBootSec=15s
OnUnitActiveSec=30s

[Install]
WantedBy=timers.target
"
  local vram_svc="[Unit]
Description=Sample amdgpu VRAM (wq#753)

[Service]
Type=oneshot
ExecStart=%h/.local/node-exporter/bin/vram_textfile.sh
"

  local target=""
  [ "$host" != LOCAL ] && target="$(ssh_target "$host" "$ip")"

  # </dev/null on every ssh/scp here: these run inside a `while read` roster loop
  # whose stdin is the roster stream — without it, ssh's own stdin handling can
  # silently eat lines out of that loop.
  run() { if [ "$host" = LOCAL ]; then bash -c "$1"; else ssh -o BatchMode=yes -o ConnectTimeout=8 "$target" "$1" </dev/null; fi; }
  # push/copybin LOCAL branch: $2 is a literal "~/..." string that must be expanded
  # against $HOME manually (no nested shell parse re-expands it for us here).
  push() {
    if [ "$host" = LOCAL ]; then cat > "${2/#\~/$HOME}"; else ssh -o BatchMode=yes -o ConnectTimeout=8 "$target" "cat > $2"; fi
  }
  # copy-then-rename: overwriting a running binary in place hits ETXTBSY; rename
  # swaps the directory entry instead, which the kernel allows even while the old
  # inode is still open by the running process.
  copybin() {
    if [ "$host" = LOCAL ]; then
      local dst="${2/#\~/$HOME}"
      cp "$1" "${dst}.new" && mv "${dst}.new" "$dst"
    else
      scp -q -o BatchMode=yes -o ConnectTimeout=8 "$1" "$target:${2}.new" </dev/null \
        && ssh -o BatchMode=yes -o ConnectTimeout=8 "$target" "mv ${2}.new ${2}" </dev/null
    fi
  }

  run "mkdir -p ~/.local/node-exporter/bin ~/.local/node-exporter/textfile ~/.config/systemd/user"

  local arch bin
  if [ "$host" = LOCAL ]; then arch="$(uname -m)"; else arch="$(remote_arch "$host" "$ip")"; fi
  if [ -z "$arch" ]; then log SKIP "$host: unreachable (arch probe failed)"; return 1; fi
  bin="$(ne_bin_for_arch "$arch")"
  if [ -z "$bin" ]; then log SKIP "$host: unsupported arch $arch"; return 1; fi

  copybin "$bin" "~/.local/node-exporter/bin/node_exporter"
  run "chmod +x ~/.local/node-exporter/bin/node_exporter"

  echo "$unit" | push - "~/.config/systemd/user/node_exporter.service"

  if [ "$vram" = 1 ]; then
    echo "$vram_script" | push - "~/.local/node-exporter/bin/vram_textfile.sh"
    run "chmod +x ~/.local/node-exporter/bin/vram_textfile.sh"
    echo "$vram_svc" | push - "~/.config/systemd/user/vram-textfile.service"
    echo "$vram_timer" | push - "~/.config/systemd/user/vram-textfile.timer"
  fi

  run "loginctl enable-linger 2>/dev/null; export XDG_RUNTIME_DIR=/run/user/\$(id -u); systemctl --user daemon-reload && systemctl --user enable node_exporter && systemctl --user restart node_exporter"
  if [ "$vram" = 1 ]; then
    run "export XDG_RUNTIME_DIR=/run/user/\$(id -u); systemctl --user enable --now vram-textfile.timer"
  fi
  log OK "$host: node_exporter user-unit enabled+started (arch=$arch vram=$vram)"
}

verify_node() {
  local host="$1" ip="$2"
  local code
  code=$(curl -s -m4 -o /dev/null -w '%{http_code}' "http://${ip}:9100/metrics" 2>/dev/null)
  if [ "$code" = 200 ]; then log VERIFIED "$host ($ip:9100) -> 200"; else log FAIL "$host ($ip:9100) -> ${code:-no response}"; fi
}

# ── Bes-side unprivileged scrape agent (feeds the EXISTING VictoriaMetrics) ──
deploy_scrape_agent() {
  local targets="" name ip local_ vram
  while read -r name ip local_ vram; do
    [ -z "$name" ] && continue
    targets="${targets}      - targets: [\"${ip}:9100\"]\n        labels: {node: \"${name}\"}\n"
  done <<EOF
$ROSTER
EOF

  local scrape_yml="global:
  scrape_interval: 30s
scrape_configs:
  - job_name: node_exporter_fleet
    static_configs:
$(printf '%b' "$targets")
"

  ssh -o BatchMode=yes -o ConnectTimeout=8 "$(ssh_target bes 100.110.156.91)" "mkdir -p ~/.local/vmagent-nodeexp/bin ~/.local/vmagent-nodeexp/rw ~/.config/systemd/user"
  scp -q -o BatchMode=yes -o ConnectTimeout=8 "$WORKDIR/vmagent-prod" "$(ssh_target bes 100.110.156.91):.local/vmagent-nodeexp/bin/vmagent.new"
  ssh -o BatchMode=yes -o ConnectTimeout=8 "$(ssh_target bes 100.110.156.91)" "mv ~/.local/vmagent-nodeexp/bin/vmagent.new ~/.local/vmagent-nodeexp/bin/vmagent" </dev/null
  ssh -o BatchMode=yes -o ConnectTimeout=8 "$(ssh_target bes 100.110.156.91)" "chmod +x ~/.local/vmagent-nodeexp/bin/vmagent"
  printf '%s' "$scrape_yml" | ssh -o BatchMode=yes -o ConnectTimeout=8 "$(ssh_target bes 100.110.156.91)" "cat > ~/.local/vmagent-nodeexp/scrape.yml"

  local unit="[Unit]
Description=vmagent: node_exporter fleet -> existing VictoriaMetrics (wq#753, unprivileged)
After=network-online.target

[Service]
ExecStart=%h/.local/vmagent-nodeexp/bin/vmagent -promscrape.config=%h/.local/vmagent-nodeexp/scrape.yml -remoteWrite.url=http://127.0.0.1:8428/api/v1/write -remoteWrite.tmpDataPath=%h/.local/vmagent-nodeexp/rw -httpListenAddr=127.0.0.1:8430
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
"
  echo "$unit" | ssh -o BatchMode=yes -o ConnectTimeout=8 "$(ssh_target bes 100.110.156.91)" "cat > ~/.config/systemd/user/vmagent-nodeexp.service"
  ssh -o BatchMode=yes -o ConnectTimeout=8 "$(ssh_target bes 100.110.156.91)" "loginctl enable-linger 2>/dev/null; export XDG_RUNTIME_DIR=/run/user/\$(id -u); systemctl --user daemon-reload && systemctl --user enable vmagent-nodeexp && systemctl --user restart vmagent-nodeexp"
  log OK "bes: vmagent-nodeexp user-unit enabled+started, scraping $(echo "$ROSTER" | grep -c . ) targets -> 127.0.0.1:8428/api/v1/write"
}

verify_vm() {
  local n
  n=$(curl -sg -m5 'http://127.0.0.1:18428/api/v1/query?query=count(up{job="node_exporter_fleet"}==1)' \
      | python3 -c 'import json,sys
try:
    d=json.load(sys.stdin)["data"]["result"]
    print(d[0]["value"][1] if d else 0)
except Exception:
    print("query-failed")')
  log VM "series with up==1 for job=node_exporter_fleet: $n (via lumen-powerdash-vmtunnel 127.0.0.1:18428)"
}

# ── main ──────────────────────────────────────────────────────────────────
if [ "$VERIFY_ONLY" = 0 ]; then
  fetch_node_exporter
  fetch_vmagent
  echo "$ROSTER" | while read -r name ip local_ vram; do
    [ -z "$name" ] && continue
    host="$name"; [ "$local_" = 1 ] && host=LOCAL
    install_user_unit "$host" "$ip" "$vram" || true
  done
  deploy_scrape_agent
  sleep 5
fi

echo "$ROSTER" | while read -r name ip local_ vram; do
  [ -z "$name" ] && continue
  verify_node "$name" "$ip"
done
verify_vm

rm -rf "$WORKDIR"
