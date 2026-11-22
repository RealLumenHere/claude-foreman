# ops/wq5247-failed-units/lumen-audit.sh — Lumen audit-rules.service, run as root by
# install.sh as `bash lumen-audit.sh DRY TS` (lib.sh is sourced here).
# Exit 0 = unit not failed afterwards (DRY=1: diagnosis printed); 1 = unfixed.
#
# augenrules --load = concatenate /etc/audit/rules.d/*.rules into /etc/audit/audit.rules,
# then `auditctl -R` it (/usr/sbin/augenrules:41-46,147). Each rule the kernel refuses
# is named as "There was an error in line N of /etc/audit/audit.rules". Fix class this
# script acts on: a rule that depends on a path that does not exist (-w PATH, dir=,
# path=) — commented out in the rules.d file it came from, after a .bak copy. Any other
# refused rule stops with the evidence printed (needs a decision, not a guess).
. "$(dirname "$0")/lib.sh"
DRY="${1:-1}"
TS="${2:-$(date -u +%Y%m%d_%H%M%S)}"
U=audit-rules.service
RULES="${WQ5247_AUDIT_RULES:-/etc/audit/audit.rules}"
RULES_D="${WQ5247_RULES_D:-/etc/audit/rules.d}"
TAG="wq5247 disabled $TS (watched path absent; augenrules --load refused it)"

echo "== lumen: $U"
systemctl status "$U" --no-pager 2>&1 | sed -n '1,8p'
echo '-- auditctl -s'; auditctl -s 2>&1
echo "-- $RULES_D"; ls -l "$RULES_D" 2>&1
echo '-- augenrules --check'; augenrules --check 2>&1

if [ "$DRY" = 1 ]; then
  # auditctl has no dry-run (-R replaces the live rule set), so diagnose stays static:
  # list every rule of the class apply mode would disable.
  echo "-- static scan of $RULES for absent watched paths"
  n=0
  while IFS= read -r rule || [ -n "$rule" ]; do
    n=$((n + 1)); p="$(audit_watch_path "$rule")"
    if [ -n "$p" ] && [ ! -e "$p" ]; then echo "  line $n absent path $p: $rule"; fi
  done <"$RULES"
  echo '  (diagnose: no load attempted; apply mode runs augenrules --load and acts on its errors)'
  exit 0
fi

for pass in 1 2 3 4 5 6 7 8 9 10; do
  out="$(augenrules --load 2>&1)"; rc=$?
  echo "-- load pass $pass: rc=$rc"; printf '%s\n' "$out"
  if [ "$rc" -eq 0 ]; then
    if [ "$pass" -eq 1 ]; then
      echo 'VERDICT lumen: rules load cleanly now — the 11-12 failure was boot-time only (journal rotated, text lost); clearing the failed state'
    else
      echo "VERDICT lumen: loaded after disabling $((pass - 1)) pass(es) of absent-path rules"
    fi
    systemctl restart "$U"
    st="$(systemctl is-failed "$U" 2>/dev/null)"
    echo "  $U: $st"
    [ "$st" != failed ]; exit $?
  fi
  fixed=0
  for n in $(printf '%s\n' "$out" | audit_error_lines); do
    rule="$(audit_rule_at "$RULES" "$n")"
    p="$(audit_watch_path "$rule")"
    mapfile -t srcs < <(audit_rule_sources "$RULES_D" "$rule")
    echo "  refused line $n: $rule"
    echo "    from: ${srcs[*]:-<no rules.d file carries it verbatim>}"
    if [ -z "$p" ] || [ -e "$p" ] || [ "${#srcs[@]}" -eq 0 ]; then
      echo "UNFIXED lumen: line $n is not the absent-path class — needs a decision (evidence above)"
      exit 1
    fi
    echo "    cause: watched path $p does not exist"
    for f in "${srcs[@]}"; do
      cp -a "$f" "$f.bak.${TS}_wq5247" || exit 1
      echo "    disabled in $f (backup $f.bak.${TS}_wq5247)"
      audit_disable_rule "$f" "$rule" "$TAG" && fixed=1
    done
  done
  if [ "$fixed" -eq 0 ]; then
    echo "UNFIXED lumen: load failed (rc=$rc) with no 'error in line N' attribution — evidence above"
    exit 1
  fi
done
echo 'UNFIXED lumen: still failing after 10 passes'
exit 1
