#!/usr/bin/env bash
# wq#4514-wq#4518, wq#4552: install gitea-mcp, postgres-mcp, mcp-server-docker, playwright-mcp,
# mcp-grafana, and a second dbhub registration (fleet-state) on Lumen as user-scope stdio MCP
# servers for Claude Code.
# No root, no systemd units, no listening ports (freeze rule: no service without naming what
# it replaces), every version pinned, every download hash-checked.
#
#   install.sh [TOOL...]   install or refresh (default: all). TOOL: gitea postgres docker playwright grafana fleet_state
#   install.sh --verify    read-only deploy check (wq#1696 contract); `--verify TOOL...` for a subset
#
# Design, from wq#4290 (stealth-browser execute_script SSRF):
#   * Secrets are read from /home/lumen/.secrets by the launcher at start, never written to
#     ~/.claude.json (Claude Code keeps MCP env there in plaintext).
#   * A third-party server whose tools can mutate or reach the network runs behind
#     lumen_mcp_guard.py: fail-closed tool allowlist, argument-value allowlists, http(s)-only
#     URL arguments, resources/prompts hidden, and for browsers a forced public-only egress proxy.
#   * Every guarded launcher passes --mask-output: file contents, page snapshots and SQL rows can
#     carry secrets as easily as container logs (wq#4943).
#   * Launchers are GENERATED here and --verify compares them byte for byte.
#   * Operator-side steps that need another node (minting the Gitea token on Anubis, the
#     read-only DB role) are in RUNBOOK.md, not here: this script only touches Lumen.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"

ROOT=/home/lumen/.local/share/mcp
GDIR=$ROOT/_guard
GPY=$GDIR/venv/bin/python
GUARD=$GDIR/lumen_mcp_guard.py
CLAUDE=/home/lumen/.npm-global/bin/claude
UV=/home/lumen/.local/bin/uv
PY312=/home/lumen/.local/bin/python3.12
SECRETS=/home/lumen/.secrets
ALL_TOOLS="gitea postgres docker grafana playwright fleet_state"
NODE=/usr/bin/node

# ---------------------------------------------------------------- pins: gitea
GITEA_VER=1.8.1
GITEA_TGZ_URL=https://gitea.com/gitea/gitea-mcp/releases/download/v$GITEA_VER/gitea-mcp_Linux_x86_64.tar.gz
GITEA_TGZ_SHA=5ed5005e116a5c6e570893a2c6bb8fd034633b67bffd4145e2de1daa7b9fd761
GITEA_BIN_SHA=fe6f89cb78646ab08595835a8118266475dba334c10fcac2a6fe7f3e7950f1c5
GITEA_HOST=http://100.121.17.5:3000
GITEA_TOKEN=$SECRETS/gitea-mcp-token
# Reads, issues, PR create/review. No create_repo/fork/delete_*/rename, no direct file commits
# (create_or_update_file), no releases/tags, no Actions secrets or dispatch, no packages.
GITEA_ALLOW=get_me,get_gitea_mcp_server_version,list_my_repos,search_repos,search_issues,search_users,list_org_repos,get_user_orgs,get_dir_contents,get_file_contents,get_repository_tree,list_branches,list_commits,get_commit,list_tags,get_tag,list_releases,get_release,get_latest_release,list_issues,list_pull_requests,issue_read,pull_request_read,label_read,milestone_read,wiki_read,actions_run_read,issue_write,pull_request_write,pull_request_review_write
# Landing stays with `lumen-pr land` (worktree discipline + review gate): no merge, no review
# delete/dismiss.
GITEA_ARG_ALLOW=(
  'issue_write:method=create|update|add_comment|edit_comment|add_labels|remove_label|replace_labels|clear_labels|add_dependency|remove_dependency'
  'pull_request_write:method=create|update|close|reopen|update_branch|add_reviewers|remove_reviewers'
  'pull_request_review_write:method=create|submit|reply_comment|resolve_thread|unresolve_thread'
)

# ---------------------------------------------------------------- pins: postgres (wq#4515)
# crystaldba/postgres-mcp 0.3.0 is not installable: it hard-pins pglast==7.2.0, which PyPI no longer
# serves (verified 2026-10-23). The ticket's alternative, bytebase/dbhub, is used. NOT the PyPI
# package "dbhub" (a 2022 package by someone else). Only the fleet_memory Postgres, through a
# SELECT-only role; never minerva.db (fleet-wide lock incidents 2026-09-28).
DBHUB_VER=1.5.0
DB_PW=$SECRETS/fleet-memory-mcp-ro.pw
DB_PORT=5433
DBHUB_ALLOW=execute_sql,search_objects

# ---------------------------------------------------------------- pins: fleet-state (wq#4552)
# Second dbhub registration, same $DBHUB_VER install, over the LIVE fleet memory (fleet-memory/
# mcp_ro above is a frozen 2026-10-08 Postgres snapshot pending collapse per wq#4539). sqlite has
# no role system, so the env-var DSN+READONLY=true scheme the postgres launcher uses is NOT a
# real guardrail here: verified empirically (wq#4552 research) that `DSN=sqlite://...?mode=ro
# READONLY=true` still lets a DELETE through on dbhub 1.5.0 — the env-var path never populates
# the config value the sqlite connector's readOnly-open and keyword classifier read. A TOML
# [[tools]] readonly=true block DOES work: DELETE comes back READONLY_VIOLATION ("Only select,
# with, explain, pragma allowed"), the fix landed in dbhub 0.22.6 (CVE-2026-61788; our 1.5.0 is
# after it). So fleet-state ships its own dbhub.toml instead of env vars. Never point a
# write-capable connection at minerva.db — lumen_state.py is the sole writer (WAL mode, verified
# via `sqlite3 "file:...?mode=ro" "PRAGMA journal_mode"` -> wal, so this read-only reader cannot
# block that writer).
FLEET_STATE_DB=/home/lumen/minerva/minerva.db
FLEET_STATE_ALLOW=execute_sql,search_objects

# ---------------------------------------------------------------- pins: docker (wq#4516)
# The Docker socket is root. Read-only surface only: no run/create/start/stop/recreate/remove/
# pull/push/build, no volume or network create/remove. Mutation stays with `ssh ... docker` under
# lumen-change-safety until a verify/critic-gated profile exists (RUNBOOK.md, follow-up).
# Container logs can carry credentials, so every result passes through the fleet's mask-secrets
# (fail closed: if masking fails the result is withheld).
DOCKER_MCP_VER=0.3.0
DOCKER_ALLOW=list_containers,fetch_container_logs,list_images,list_networks,list_volumes
MASKER=/home/lumen/bin/mask-secrets

# ---------------------------------------------------------------- pins: grafana (wq#4518)
# Grafana lives on Bes (tailnet only), not on Lumen. It serves anonymous Viewer, which already covers
# everything exposed here, so no service account is created and Bes is not touched. If anonymous
# access is ever turned off, drop a Viewer service-account token in $GRAFANA_TOKEN and the launcher
# picks it up. 79 upstream tools; 19 read-only ones are exposed (no grafana_api_request, no
# create/update/delete, no plugins, no incident/oncall/loki/tempo).
GRAFANA_VER=2.0.2
GRAFANA_TGZ_URL=https://github.com/grafana/mcp-grafana/releases/download/v$GRAFANA_VER/mcp-grafana_Linux_x86_64.tar.gz
GRAFANA_TGZ_SHA=624e997b78bcadf236f82f8086827890ba77b6dfdc6fd6446f7c48dfd9e586f0
GRAFANA_BIN_SHA=71661c6c96ab1dbd7c84668c4115f7979cb2bc3ee62d474c7c718fd11081a527
GRAFANA_URL=http://100.110.156.91:3000
GRAFANA_TOKEN=$SECRETS/grafana-mcp.token
GRAFANA_ALLOW=search_dashboards,get_dashboard_summary,get_dashboard_by_uid,get_dashboard_panel_queries,get_dashboard_property,list_datasources,get_datasource,check_datasources_health,alerting_rules_read,alerting_silences_read,query_prometheus,query_prometheus_histogram,list_prometheus_metric_names,list_prometheus_label_names,list_prometheus_label_values,list_prometheus_metric_metadata,search_folders,get_annotations,generate_deeplink

# ---------------------------------------------------------------- pins: playwright (wq#4517)
# NOT "@anthropic/mcp-playwright" (404). The browser can reach anything its network can, so (wq#4290):
#   * Chrome egress is forced through lumen_egress_proxy (public hosts only, resolved and IP-pinned per
#     connection, loopback NOT bypassed), WebRTC UDP is closed, service workers are blocked;
#   * the allowlist drops browser_run_code_unsafe, browser_evaluate, browser_file_upload, browser_drop;
#   * navigate/tab URLs must be http(s).
# Browser: the pinned Chrome for Testing from the wq#4206 install. Its AppArmor userns profile already
# exists (Lumen sets apparmor_restrict_unprivileged_userns=1), so the sandbox stays ON, no --no-sandbox.
# Playwright wants r1247; this is r1243 (153.0): CDP-compatible in practice, proven by smoke_playwright.py.
PW_VER=0.0.83
PW_CHROME=/home/lumen/.local/share/stealth-browser-mcp/ba95cca933c8/browsers/chromium-1243/chrome-linux64/chrome
PW_ALLOW=browser_navigate,browser_navigate_back,browser_snapshot,browser_find,browser_click,browser_hover,browser_type,browser_press_key,browser_select_option,browser_fill_form,browser_wait_for,browser_tabs,browser_close,browser_resize,browser_handle_dialog,browser_take_screenshot,browser_console_messages,browser_network_requests,browser_network_request

# ---------------------------------------------------------------- launchers (generated)
emit_launcher_gitea() {
cat <<EOF
#!/usr/bin/env bash
# GENERATED by ops/wq4514-mcp-install-batch/install.sh: do not hand-edit (install.sh --verify compares it).
# gitea-mcp $GITEA_VER behind lumen_mcp_guard (wq#4514). The token is read from a 0600 file by
# gitea-mcp itself and never lands in ~/.claude.json.
set -euo pipefail
export GITEA_HOST=$GITEA_HOST
export GITEA_ACCESS_TOKEN_FILE=$GITEA_TOKEN
export PYTHONWARNINGS=ignore
exec $GPY -I $GUARD --name gitea \\
  --allow $GITEA_ALLOW \\
  --mask-output $MASKER \\
$(for a in "${GITEA_ARG_ALLOW[@]}"; do printf "  --arg-allow '%s' \\\\\n" "$a"; done; printf '  --') $ROOT/gitea-mcp/$GITEA_VER/gitea-mcp -t stdio
EOF
}

emit_launcher_postgres() {
cat <<EOF
#!/usr/bin/env bash
# GENERATED by ops/wq4514-mcp-install-batch/install.sh: do not hand-edit (install.sh --verify compares it).
# dbhub $DBHUB_VER -> lumen-pgvector fleet_memory as the SELECT-only role mcp_ro (wq#4515). The DSN is
# built here from a 0600 file, passed by environment (never argv, never ~/.claude.json). dbhub's own
# READONLY and MAX_ROWS are a second layer on top of the role's read-only transactions.
set -euo pipefail
export DSN="postgres://mcp_ro:\$(cat $DB_PW)@127.0.0.1:$DB_PORT/fleet_memory?sslmode=disable"
export READONLY=true MAX_ROWS=500 TRANSPORT=stdio
export PYTHONWARNINGS=ignore
exec $GPY -I $GUARD --name fleet-memory \\
  --allow $DBHUB_ALLOW \\
  --mask-output $MASKER \\
  -- $NODE $ROOT/dbhub/$DBHUB_VER/node_modules/@bytebase/dbhub/dist/index.js
EOF
}

emit_launcher_fleet_state() {
cat <<EOF
#!/usr/bin/env bash
# GENERATED by ops/wq4514-mcp-install-batch/install.sh: do not hand-edit (install.sh --verify compares it).
# dbhub $DBHUB_VER -> $FLEET_STATE_DB (sqlite, LIVE fleet state) behind lumen_mcp_guard (wq#4552).
# Read-only comes from fleet-state.toml's [[tools]] readonly=true, NOT an env var: see the pin
# comment above for why the postgres launcher's READONLY=true env var does not transfer to sqlite.
set -euo pipefail
export TRANSPORT=stdio
export PYTHONWARNINGS=ignore
exec $GPY -I $GUARD --name fleet-state \\
  --allow $FLEET_STATE_ALLOW \\
  --mask-output $MASKER \\
  -- $NODE $ROOT/dbhub/$DBHUB_VER/node_modules/@bytebase/dbhub/dist/index.js --config=$ROOT/dbhub/$DBHUB_VER/fleet-state.toml
EOF
}

emit_launcher_docker() {
cat <<EOF
#!/usr/bin/env bash
# GENERATED by ops/wq4514-mcp-install-batch/install.sh: do not hand-edit (install.sh --verify compares it).
# mcp-server-docker $DOCKER_MCP_VER behind lumen_mcp_guard, READ-ONLY (wq#4516). The Docker socket is
# root-equivalent: see RUNBOOK.md before adding any mutating tool to the allowlist.
set -euo pipefail
export PYTHONWARNINGS=ignore
exec $GPY -I $GUARD --name docker \\
  --allow $DOCKER_ALLOW \\
  --mask-output $MASKER \\
  -- $ROOT/docker-mcp/$DOCKER_MCP_VER/venv/bin/mcp-server-docker
EOF
}

emit_launcher_grafana() {
cat <<EOF
#!/usr/bin/env bash
# GENERATED by ops/wq4514-mcp-install-batch/install.sh: do not hand-edit (install.sh --verify compares it).
# mcp-grafana $GRAFANA_VER behind lumen_mcp_guard, READ-ONLY (wq#4518): server-side --disable-write and
# a trimmed tool category list, plus the guard allowlist and output masking.
set -euo pipefail
export GRAFANA_URL=$GRAFANA_URL
# mcp-grafana reports usage statistics to Grafana Labs unless told not to; this fleet does not phone home.
export GRAFANA_USAGE_STATS=disabled DO_NOT_TRACK=1
[ -s $GRAFANA_TOKEN ] && export GRAFANA_SERVICE_ACCOUNT_TOKEN="\$(cat $GRAFANA_TOKEN)"
export PYTHONWARNINGS=ignore
exec $GPY -I $GUARD --name grafana \\
  --allow $GRAFANA_ALLOW \\
  --mask-output $MASKER \\
  -- $ROOT/mcp-grafana/$GRAFANA_VER/mcp-grafana -t stdio --disable-write --enabled-tools search,datasource,prometheus,alerting,dashboard,folder,annotations,navigation
EOF
}

emit_launcher_playwright() {
cat <<EOF
#!/usr/bin/env bash
# GENERATED by ops/wq4514-mcp-install-batch/install.sh: do not hand-edit (install.sh --verify compares it).
# @playwright/mcp $PW_VER behind lumen_mcp_guard (wq#4517): fail-closed tool allowlist, http(s)-only URL
# arguments, and every connection Chrome opens forced through the public-only egress proxy.
set -euo pipefail
export PYTHONWARNINGS=ignore
exec $GPY -I $GUARD --name playwright --egress-proxy \\
  --allow $PW_ALLOW \\
  --http-url-arg browser_navigate:url --http-url-arg browser_tabs:url \\
  --mask-output $MASKER \\
  -- $NODE $ROOT/playwright-mcp/$PW_VER/node_modules/@playwright/mcp/cli.js \\
  --headless --isolated --browser chromium --executable-path $PW_CHROME \\
  --proxy-server '{EGRESS_PROXY}' --proxy-bypass '<-loopback>' --block-service-workers \\
  --config $ROOT/playwright-mcp/$PW_VER/browser-config.json --output-dir $ROOT/playwright-mcp/$PW_VER/output
EOF
}

# ---------------------------------------------------------------- verify helpers (read-only)
vgen() { # DST GENERATOR...: DST is byte-identical to what the generator prints
  local dst="$1"; shift
  [ -e "$dst" ] || { _vmiss "absent: $dst"; return; }
  cmp -s <("$@") "$dst" || _vmiss "differs from generator ($1): $dst"
}
v_registered() { # NAME LAUNCHER: registered to this launcher AND Claude Code itself can connect
  local reg; reg="$("$CLAUDE" mcp get "$1" 2>/dev/null || true)"
  case "$reg" in *"$2"*) ;; *) _vmiss "claude user-scope MCP '$1' not registered to $2"; return;; esac
  case "$reg" in *Connected*) ;; *) _vmiss "claude reports MCP '$1' is not connected (launcher $2)";; esac
}
n_items() { printf '%s\n' "$1" | tr ',' '\n' | grep -c .; }
v_launcher_lists() { # LAUNCHER EXPECTED_COUNT READONLY_TOOL ARGS_JSON
  # launcher -> guard -> backend answers tools/list with exactly that many tools AND a real
  # read-only tools/call succeeds. tools/list alone is not enough: a second `initialize` on the
  # backend (per-request proxy sessions) breaks the first call but never the listing.
  local out n
  out="$(timeout 120 "$GPY" -I "$GDIR/mcp_smoke.py" --call "$3" --args "$4" -- "$1" 2>/dev/null || true)"
  n="$(printf '%s\n' "$out" | sed -n 's/^tools\/list: \([0-9]*\) tools.*/\1/p')"
  [ "$n" = "$2" ] || _vmiss "launcher $1 lists '${n:-nothing}' tools, expected $2"
  printf '%s\n' "$out" | grep -qF "call $3($4): ok:" || _vmiss "launcher $1: read-only call $3 did not succeed"
}
v_binary() { # PATH SHA256
  [ -x "$1" ] && [ "$(sha256sum "$1" | cut -d' ' -f1)" = "$2" ] || _vmiss "missing or hash differs: $1"
}
v_secret_file() { # PATH
  [ -s "$1" ] && [ "$(stat -c %a "$1")" = 600 ] || _vmiss "secret file missing, empty or not 0600: $1"
}

verify_common() {
  vfile "$SRC/lumen_mcp_guard.py" "$GUARD"
  vfile "$SRC/mcp_smoke.py" "$GDIR/mcp_smoke.py"
  vfile "$SRC/../wq4206-stealth-browser-mcp/lumen_egress_proxy.py" "$GDIR/lumen_egress_proxy.py"
  vfile "$SRC/../wq4206-stealth-browser-mcp/lumen_stealth_guard.py" "$GDIR/lumen_stealth_guard.py"
  "$GPY" -I -c 'import sys, fastmcp, mcp; sys.exit(fastmcp.__version__ != "2.11.2")' 2>/dev/null \
    || _vmiss "guard venv cannot import fastmcp==2.11.2 + mcp: $GDIR/venv"
}

verify_gitea() {
  local d=$ROOT/gitea-mcp/$GITEA_VER
  [ -x "$MASKER" ] || _vmiss "masker missing or not executable: $MASKER"
  v_binary "$d/gitea-mcp" "$GITEA_BIN_SHA"
  vgen "$d/launch.sh" emit_launcher_gitea
  v_secret_file "$GITEA_TOKEN"
  v_registered gitea "$d/launch.sh"
  v_launcher_lists "$d/launch.sh" "$(n_items "$GITEA_ALLOW")" get_gitea_mcp_server_version '{}'
}

verify_postgres() {
  local d=$ROOT/dbhub/$DBHUB_VER
  [ -x "$MASKER" ] || _vmiss "masker missing or not executable: $MASKER"
  vfile "$SRC/dbhub-package.json" "$d/package.json"
  vfile "$SRC/dbhub-package-lock.json" "$d/package-lock.json"
  [ "$(sed -n 's/.*"version": *"\([^"]*\)".*/\1/p' "$d/node_modules/@bytebase/dbhub/package.json" 2>/dev/null | head -1)" = "$DBHUB_VER" ] \
    || _vmiss "dbhub $DBHUB_VER not installed in $d"
  vgen "$d/launch.sh" emit_launcher_postgres
  v_secret_file "$DB_PW"
  v_registered fleet-memory "$d/launch.sh"
  v_launcher_lists "$d/launch.sh" "$(n_items "$DBHUB_ALLOW")" execute_sql '{"sql":"SELECT count(*) FROM memory_events"}'
}

verify_fleet_state() {
  local d=$ROOT/dbhub/$DBHUB_VER
  [ -x "$MASKER" ] || _vmiss "masker missing or not executable: $MASKER"
  [ "$(sed -n 's/.*"version": *"\([^"]*\)".*/\1/p' "$d/node_modules/@bytebase/dbhub/package.json" 2>/dev/null | head -1)" = "$DBHUB_VER" ] \
    || _vmiss "dbhub $DBHUB_VER not installed in $d (shared with fleet-memory)"
  vfile "$SRC/fleet-state.toml" "$d/fleet-state.toml"
  vgen "$d/launch-fleet-state.sh" emit_launcher_fleet_state
  [ -s "$FLEET_STATE_DB" ] || _vmiss "fleet-state source db missing: $FLEET_STATE_DB"
  v_registered fleet-state "$d/launch-fleet-state.sh"
  v_launcher_lists "$d/launch-fleet-state.sh" "$(n_items "$FLEET_STATE_ALLOW")" execute_sql '{"sql":"SELECT count(*) FROM state_facts WHERE valid_to IS NULL"}'
}

verify_docker() {
  local d=$ROOT/docker-mcp/$DOCKER_MCP_VER
  [ -x "$MASKER" ] || _vmiss "masker missing or not executable: $MASKER"
  [ -x "$d/venv/bin/mcp-server-docker" ] || _vmiss "mcp-server-docker not installed in $d/venv"
  "$d/venv/bin/python" -c 'import importlib.metadata as m, sys; sys.exit(m.version("mcp-server-docker") != sys.argv[1])' "$DOCKER_MCP_VER" 2>/dev/null \
    || _vmiss "mcp-server-docker version in $d/venv is not $DOCKER_MCP_VER"
  vgen "$d/launch.sh" emit_launcher_docker
  v_registered docker "$d/launch.sh"
  v_launcher_lists "$d/launch.sh" "$(n_items "$DOCKER_ALLOW")" list_containers '{}'
}

verify_grafana() {
  local d=$ROOT/mcp-grafana/$GRAFANA_VER
  [ -x "$MASKER" ] || _vmiss "masker missing or not executable: $MASKER"
  v_binary "$d/mcp-grafana" "$GRAFANA_BIN_SHA"
  vgen "$d/launch.sh" emit_launcher_grafana
  v_registered grafana "$d/launch.sh"
  v_launcher_lists "$d/launch.sh" "$(n_items "$GRAFANA_ALLOW")" list_datasources '{}'
}

verify_playwright() {
  local d=$ROOT/playwright-mcp/$PW_VER
  [ -x "$MASKER" ] || _vmiss "masker missing or not executable: $MASKER"
  vfile "$SRC/playwright-package.json" "$d/package.json"
  vfile "$SRC/playwright-package-lock.json" "$d/package-lock.json"
  vfile "$SRC/playwright-browser-config.json" "$d/browser-config.json"
  [ "$(sed -n 's/.*"version": *"\([^"]*\)".*/\1/p' "$d/node_modules/@playwright/mcp/package.json" 2>/dev/null | head -1)" = "$PW_VER" ] \
    || _vmiss "@playwright/mcp $PW_VER not installed in $d"
  if [ ! -x "$PW_CHROME" ] || ! timeout 30 "$PW_CHROME" --headless=new --dump-dom about:blank >/dev/null 2>&1; then
    _vmiss "pinned Chrome missing or aborts with its sandbox on (AppArmor userns profile from wq#4206 not loaded?): $PW_CHROME"
  fi
  vgen "$d/launch.sh" emit_launcher_playwright
  v_registered playwright "$d/launch.sh"
  v_launcher_lists "$d/launch.sh" "$(n_items "$PW_ALLOW")" browser_tabs '{"action":"list"}'
}

if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  shift
  tools=("$@"); [ ${#tools[@]} -gt 0 ] || tools=($ALL_TOOLS)
  verify_common
  for t in "${tools[@]}"; do "verify_$t"; done
  vdone
fi

# ---------------------------------------------------------------- install
CLAUDE_JSON_BACKED_UP=0
claude_register() { # NAME LAUNCHER
  if [ "$CLAUDE_JSON_BACKED_UP" = 0 ] && [ -e /home/lumen/.claude.json ]; then
    mkdir -p /home/lumen/backups
    cp -a /home/lumen/.claude.json "/home/lumen/backups/claude.json.pre-wq4514.$(date +%Y%m%d_%H%M%S)"
    CLAUDE_JSON_BACKED_UP=1
  fi
  "$CLAUDE" mcp remove -s user "$1" >/dev/null 2>&1 || true
  "$CLAUDE" mcp add-json -s user "$1" "{\"type\":\"stdio\",\"command\":\"$2\",\"args\":[],\"env\":{}}" >/dev/null
}

write_provenance() { # DIR JSON-BODY
  printf '%s\n' "$2" > "$1/PROVENANCE.json"
}

install_common() {
  mkdir -p "$GDIR"; chmod 700 "$ROOT"
  [ -x "$GPY" ] || "$UV" venv --quiet --python "$PY312" "$GDIR/venv"
  VIRTUAL_ENV="$GDIR/venv" "$UV" pip install --quiet --python "$GPY" \
    -c "$SRC/guard-requirements.txt" "fastmcp==2.11.2" "mcp==1.30.0"
  install -m 0644 "$SRC/lumen_mcp_guard.py" "$GUARD"
  install -m 0755 "$SRC/mcp_smoke.py" "$GDIR/mcp_smoke.py"
  # the reviewed wq#4206 egress filter, byte for byte (single source of truth)
  install -m 0644 "$SRC/../wq4206-stealth-browser-mcp/lumen_egress_proxy.py" "$GDIR/lumen_egress_proxy.py"
  install -m 0644 "$SRC/../wq4206-stealth-browser-mcp/lumen_stealth_guard.py" "$GDIR/lumen_stealth_guard.py"
}

install_gitea() {
  local d=$ROOT/gitea-mcp/$GITEA_VER tmp
  [ -s "$GITEA_TOKEN" ] || { echo "ERR missing $GITEA_TOKEN: mint the mcp-agent token first (RUNBOOK.md, gitea)" >&2; exit 1; }
  mkdir -p "$d"
  if [ ! -x "$d/gitea-mcp" ] || [ "$(sha256sum "$d/gitea-mcp" | cut -d' ' -f1)" != "$GITEA_BIN_SHA" ]; then
    tmp="$(mktemp -d)"
    curl -fsSL -m 120 -o "$tmp/t.tgz" "$GITEA_TGZ_URL"
    [ "$(sha256sum "$tmp/t.tgz" | cut -d' ' -f1)" = "$GITEA_TGZ_SHA" ] || { rm -rf "$tmp"; echo "ERR tarball sha256 mismatch" >&2; exit 1; }
    tar -xzf "$tmp/t.tgz" -C "$d" gitea-mcp
    chmod 0755 "$d/gitea-mcp"; rm -rf "$tmp"
    [ "$(sha256sum "$d/gitea-mcp" | cut -d' ' -f1)" = "$GITEA_BIN_SHA" ] || { echo "ERR binary sha256 mismatch" >&2; exit 1; }
  fi
  emit_launcher_gitea > "$d/launch.sh"; chmod 0755 "$d/launch.sh"
  write_provenance "$d" "{\"tool\":\"gitea-mcp\",\"version\":\"$GITEA_VER\",\"source\":\"$GITEA_TGZ_URL\",\"tarball_sha256\":\"$GITEA_TGZ_SHA\",\"binary_sha256\":\"$GITEA_BIN_SHA\",\"installed\":\"$(date -u +%FT%TZ)\"}"
  claude_register gitea "$d/launch.sh"
}

install_postgres() {
  local d=$ROOT/dbhub/$DBHUB_VER
  [ -s "$DB_PW" ] || { echo "ERR missing $DB_PW: create the mcp_ro role first (RUNBOOK.md, postgres)" >&2; exit 1; }
  mkdir -p "$d"
  install -m 0644 "$SRC/dbhub-package.json" "$d/package.json"
  install -m 0644 "$SRC/dbhub-package-lock.json" "$d/package-lock.json"
  # lockfile integrity hashes; install scripts off (dbhub has none, its dependencies must not run any)
  (cd "$d" && npm ci --ignore-scripts --no-audit --no-fund --silent)
  emit_launcher_postgres > "$d/launch.sh"; chmod 0755 "$d/launch.sh"
  write_provenance "$d" "{\"tool\":\"@bytebase/dbhub\",\"version\":\"$DBHUB_VER\",\"lock_sha256\":\"$(sha256sum "$SRC/dbhub-package-lock.json" | cut -d' ' -f1)\",\"installed\":\"$(date -u +%FT%TZ)\"}"
  claude_register fleet-memory "$d/launch.sh"
}

install_fleet_state() {
  local d=$ROOT/dbhub/$DBHUB_VER
  [ -s "$FLEET_STATE_DB" ] || { echo "ERR fleet-state source db missing: $FLEET_STATE_DB" >&2; exit 1; }
  [ -d "$d/node_modules/@bytebase/dbhub" ] || { echo "ERR dbhub $DBHUB_VER not installed in $d: run 'install.sh postgres' first (shared install)" >&2; exit 1; }
  install -m 0644 "$SRC/fleet-state.toml" "$d/fleet-state.toml"
  emit_launcher_fleet_state > "$d/launch-fleet-state.sh"; chmod 0755 "$d/launch-fleet-state.sh"
  claude_register fleet-state "$d/launch-fleet-state.sh"
}

install_docker() {
  local d=$ROOT/docker-mcp/$DOCKER_MCP_VER
  [ -x "$MASKER" ] || { echo "ERR $MASKER missing: refusing to expose container logs unmasked" >&2; exit 1; }
  mkdir -p "$d"
  [ -x "$d/venv/bin/python" ] || "$UV" venv --quiet --python "$PY312" "$d/venv"
  VIRTUAL_ENV="$d/venv" "$UV" pip install --quiet --python "$d/venv/bin/python" --require-hashes -r "$SRC/docker-mcp.lock"
  emit_launcher_docker > "$d/launch.sh"; chmod 0755 "$d/launch.sh"
  write_provenance "$d" "{\"tool\":\"mcp-server-docker\",\"version\":\"$DOCKER_MCP_VER\",\"lock_sha256\":\"$(sha256sum "$SRC/docker-mcp.lock" | cut -d' ' -f1)\",\"installed\":\"$(date -u +%FT%TZ)\"}"
  claude_register docker "$d/launch.sh"
}

install_grafana() {
  local d=$ROOT/mcp-grafana/$GRAFANA_VER tmp i
  [ -x "$MASKER" ] || { echo "ERR $MASKER missing: refusing to expose Grafana output unmasked" >&2; exit 1; }
  mkdir -p "$d"
  if [ ! -x "$d/mcp-grafana" ] || [ "$(sha256sum "$d/mcp-grafana" | cut -d' ' -f1)" != "$GRAFANA_BIN_SHA" ]; then
    tmp="$(mktemp -d)"
    for i in 1 2 3 4 5; do curl -fsSL -m 120 -o "$tmp/t.tgz" "$GRAFANA_TGZ_URL" && break; sleep $((i * 4)); done  # GitHub 502s now and then
    [ "$(sha256sum "$tmp/t.tgz" | cut -d' ' -f1)" = "$GRAFANA_TGZ_SHA" ] || { rm -rf "$tmp"; echo "ERR tarball sha256 mismatch or download failed" >&2; exit 1; }
    tar -xzf "$tmp/t.tgz" -C "$d" mcp-grafana
    chmod 0755 "$d/mcp-grafana"; rm -rf "$tmp"
    [ "$(sha256sum "$d/mcp-grafana" | cut -d' ' -f1)" = "$GRAFANA_BIN_SHA" ] || { echo "ERR binary sha256 mismatch" >&2; exit 1; }
  fi
  emit_launcher_grafana > "$d/launch.sh"; chmod 0755 "$d/launch.sh"
  write_provenance "$d" "{\"tool\":\"mcp-grafana\",\"version\":\"$GRAFANA_VER\",\"source\":\"$GRAFANA_TGZ_URL\",\"tarball_sha256\":\"$GRAFANA_TGZ_SHA\",\"binary_sha256\":\"$GRAFANA_BIN_SHA\",\"installed\":\"$(date -u +%FT%TZ)\"}"
  claude_register grafana "$d/launch.sh"
}

install_playwright() {
  local d=$ROOT/playwright-mcp/$PW_VER
  [ -x "$PW_CHROME" ] || { echo "ERR pinned Chrome missing: $PW_CHROME (install the wq4206 stealth-browser bundle first)" >&2; exit 1; }
  mkdir -p "$d/output"
  install -m 0644 "$SRC/playwright-package.json" "$d/package.json"
  install -m 0644 "$SRC/playwright-package-lock.json" "$d/package-lock.json"
  install -m 0644 "$SRC/playwright-browser-config.json" "$d/browser-config.json"
  (cd "$d" && npm ci --ignore-scripts --no-audit --no-fund --silent)
  emit_launcher_playwright > "$d/launch.sh"; chmod 0755 "$d/launch.sh"
  write_provenance "$d" "{\"tool\":\"@playwright/mcp\",\"version\":\"$PW_VER\",\"lock_sha256\":\"$(sha256sum "$SRC/playwright-package-lock.json" | cut -d' ' -f1)\",\"chrome\":\"$PW_CHROME\",\"installed\":\"$(date -u +%FT%TZ)\"}"
  claude_register playwright "$d/launch.sh"
}

main() {
  local tools=("$@") t
  [ ${#tools[@]} -gt 0 ] || tools=($ALL_TOOLS)
  for t in "${tools[@]}"; do
    case " $ALL_TOOLS " in *" $t "*) ;; *) echo "unknown tool: $t (have: $ALL_TOOLS)" >&2; exit 2;; esac
  done
  install_common
  for t in "${tools[@]}"; do "install_$t"; done
  exec "$0" --verify "${tools[@]}"
}
main "$@"
