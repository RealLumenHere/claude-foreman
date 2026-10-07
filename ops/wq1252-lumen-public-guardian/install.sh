#!/usr/bin/env bash
# wq#1252 — install lumen-public-guardian. Run as root via `lumen-run --root`
# (repo convention: ops/wq1184-gatus-health-checks, ops/wq911-auth-alerter).
# Idempotent: safe to re-run after every `git pull` in foreman.
#
# What it does NOT do: deploy lumen-mail-probe. That needs a Cloudflare token
# with Workers:Edit plus a one-click destination-address verification in
# Frank's account, neither of which exist on this host. Until then check E2
# reports UNCONFIGURED — visibly not-covering, never silently green.
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
DEST=/home/lumen/services/lumen-public-guardian
SECRET=/home/lumen/.secrets/lpg-probe-hmac

# wq#1696 deploy-verify contract (ops/_verify.sh). Read-only; before any install step.
# The sudoers grant is root-only 0440, unreadable to lumen, so it is not checked here.
if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  for f in guardian.py checks_email.py checks_stream.py schema_parse.py config.json; do
    vfile "$SRC/$f" "$DEST/$f"
  done
  vfile "$SRC/mail-probe/wrangler.toml" "$DEST/mail-probe/wrangler.toml"
  vfile "$SRC/mail-probe/src/worker.js" "$DEST/mail-probe/src/worker.js"
  vfile "$SRC/mail-probe/deploy.sh" "$DEST/mail-probe/deploy.sh"
  vunit_file "$SRC/lumen-public-guardian.service" /etc/systemd/system/lumen-public-guardian.service
  vunit_file "$SRC/lumen-public-guardian.timer" /etc/systemd/system/lumen-public-guardian.timer
  venabled lumen-public-guardian.timer
  vactive lumen-public-guardian.timer
  vdone
fi

echo "== code -> $DEST =="
install -d -o lumen -g lumen -m 755 "$DEST" "$DEST/mail-probe/src" \
        /home/lumen/logs /home/lumen/.secrets
for f in guardian.py checks_email.py checks_stream.py schema_parse.py config.json; do
  install -o lumen -g lumen -m 755 "$SRC/$f" "$DEST/$f"
done
install -o lumen -g lumen -m 644 "$SRC/mail-probe/wrangler.toml" "$DEST/mail-probe/wrangler.toml"
install -o lumen -g lumen -m 644 "$SRC/mail-probe/src/worker.js" "$DEST/mail-probe/src/worker.js"
install -o lumen -g lumen -m 755 "$SRC/mail-probe/deploy.sh" "$DEST/mail-probe/deploy.sh"

echo "== probe HMAC secret =="
if [ ! -f "$SECRET" ]; then
  # Generated here, never printed. The same bytes go into the worker with
  # `wrangler secret put PROBE_HMAC_SECRET < $SECRET` at probe-deploy time.
  openssl rand -hex 32 > "$SECRET"
  chown lumen:lumen "$SECRET"
  chmod 600 "$SECRET"
  echo "generated $SECRET (0600) — push the same value to the probe worker"
else
  echo "$SECRET already exists, leaving it alone"
fi

echo "== sudoers: exactly the one sudo heal in HEAL_ALLOWLIST =="
cat > /etc/sudoers.d/lumen-public-guardian <<'EOF'
# wq#1252 — lumen-public-guardian's stream heal ladder. Deliberately one exact
# command with fully-specified arguments: this grant must not become a general
# systemctl grant. It matches HEAL_ALLOWLIST in guardian.py verbatim; change
# one and you must change the other. No cloudflared line: the guardian files
# requests with cfd-actuator, which holds the only restart grant (wq#3575).
lumen ALL=(root) NOPASSWD: /usr/bin/systemctl restart lumen-pokemon-live.service
EOF
chmod 440 /etc/sudoers.d/lumen-public-guardian
visudo -cf /etc/sudoers.d/lumen-public-guardian

echo "== systemd units =="
install -m 644 "$SRC/lumen-public-guardian.service" /etc/systemd/system/lumen-public-guardian.service
install -m 644 "$SRC/lumen-public-guardian.timer" /etc/systemd/system/lumen-public-guardian.timer
systemctl daemon-reload

echo "== selftest before enabling =="
sudo -u lumen /usr/bin/python3 "$DEST/guardian.py" selftest

echo "== enable =="
systemctl enable --now lumen-public-guardian.timer
systemctl list-timers lumen-public-guardian.timer --no-pager || true

cat <<EOF

== installed ==
Next pass runs within 2 minutes. Watch it:
  journalctl -u lumen-public-guardian -f
  python3 $DEST/guardian.py status

Still to do (each is tracked in /home/lumen/foreman/LUMEN_PUBLIC_RUNBOOK.md):
  1. Verify lumen@lumennode.uk as a Cloudflare destination address, then
     deploy mail-probe with mail-probe/deploy.sh (not a bare wrangler
     deploy — it reads the cron trigger back and fails loudly if the
     deploy dropped it, wq#3209) -> unlocks E2 (the end-to-end round trip).
  2. Drop a Workers-read API token at /home/lumen/mailbot/.cf_deploy_token
     (0600) -> unlocks E1-version and the migration/rollback heals.
  3. Optional: /home/lumen/.secrets/lpg-status-push.json mapping check ids to
     status.lumennode.uk push URLs -> publishes the board.
Until each is done the matching check reports UNCONFIGURED and nags once a day.
EOF
