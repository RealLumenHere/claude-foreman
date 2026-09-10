# wq#1658 — dead-man LastTrigger-age lines for handoff's verify().
# Splice into /home/lumen/bin/handoff's verify() function, after the
# existing "## vault HA" block. wadjet-ha-monitor.timer and
# lumen-backup-critical.timer are the two highest-blast-radius units in the
# 107-timer dead-man gap this ticket closes (§ FLEET_SERVICE_ATLAS.md row
# F-13) — call them out explicitly rather than waiting on the fleet-wide
# cron-stamp rollout to reach them.
echo "## dead-man: critical timer LastTrigger age"
for t in wadjet-ha-monitor.timer lumen-backup-critical.timer; do
  lt=$(systemctl show "$t" -p LastTriggerUSec --value 2>/dev/null)
  if [ -z "$lt" ] || [ "$lt" = "n/a" ]; then
    printf "  %-28s never triggered (or unit missing)\n" "$t:"
    continue
  fi
  lt_s=$(date -d "$lt" +%s 2>/dev/null)
  if [ -n "$lt_s" ]; then
    printf "  %-28s %ds ago (%s)\n" "$t:" "$(( $(date +%s) - lt_s ))" "$lt"
  else
    printf "  %-28s %s\n" "$t:" "$lt"
  fi
done
