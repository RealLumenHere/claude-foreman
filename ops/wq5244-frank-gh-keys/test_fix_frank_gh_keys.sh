#!/usr/bin/env bash
# Offline test for fix-frank-gh-keys.sh: runs the remote body locally against scratch $HOMEs
# (WQ5244_LOCAL_HOME) with a pinned stand-in for github.com/FrankStupak.keys.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
FIX="$HERE/fix-frank-gh-keys.sh"
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
K1="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIH+6pUc8Jtgp42jVU8Y2lgcz3EUU3XYPo/pGtPYYBBGR"
K2="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIH53I9o3FsArc0TqMVs82sBrN8kXxsYAmeFRE3akAtS8"
OTHER="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI$(printf 'Q%.0s' $(seq 43))"
printf '%s comment-a\n%s comment-b\n' "$K1" "$K2" > "$T/live.keys"
fail=0
ok() { echo "PASS $1"; }
bad() { echo "FAIL $1"; fail=1; }

# Case 1: node has K1 + an unrelated key, K2 missing. Dry run reports, writes nothing.
mkdir -p "$T/h1/.ssh"; printf '%s other\n%s frank-laptop\n' "$OTHER" "$K1" > "$T/h1/.ssh/authorized_keys"
before="$(cat "$T/h1/.ssh/authorized_keys")"
out="$(WQ5244_LIVE_KEYS_FILE="$T/live.keys" WQ5244_LOCAL_HOME="$T/h1" bash "$FIX" maksoor 2>&1)"; rc=$?
grep -qF "present  ssh-ed25519 ...${K1: -12}" <<<"$out" && grep -qF "MISSING  ssh-ed25519 ...${K2: -12}" <<<"$out" \
  && [ "$before" = "$(cat "$T/h1/.ssh/authorized_keys")" ] && [ $rc -eq 0 ] && ok "dry-run reports K2 missing, file untouched" || bad "dry-run: $out"

# Case 2: --apply appends only K2, keeps existing lines, makes a backup, idempotent on re-run.
out="$(WQ5244_LIVE_KEYS_FILE="$T/live.keys" WQ5244_LOCAL_HOME="$T/h1" bash "$FIX" --apply maksoor 2>&1)"; rc=$?
n_k1="$(grep -cF "${K1#* }" "$T/h1/.ssh/authorized_keys")"; n_k2="$(grep -cF "${K2#* }" "$T/h1/.ssh/authorized_keys")"
[ $rc -eq 0 ] && [ "$n_k1" = 1 ] && [ "$n_k2" = 1 ] && grep -qF "$OTHER other" "$T/h1/.ssh/authorized_keys" \
  && ls "$T"/h1/.ssh/authorized_keys.bak.*_wq5244 >/dev/null 2>&1 && grep -q "added + re-verified" <<<"$out" \
  && [ "$(stat -c %a "$T/h1/.ssh/authorized_keys")" = 600 ] && ok "apply adds K2 only, backup made, 0600" || bad "apply: $out"
out="$(WQ5244_LIVE_KEYS_FILE="$T/live.keys" WQ5244_LOCAL_HOME="$T/h1" bash "$FIX" --apply maksoor 2>&1)"; rc=$?
[ $rc -eq 0 ] && grep -q "ok: nothing to add" <<<"$out" && [ "$(grep -cF "${K2#* }" "$T/h1/.ssh/authorized_keys")" = 1 ] \
  && ok "re-apply is a no-op" || bad "idempotence: $out"

# Case 3: no ~/.ssh at all -> created 0700, both keys added.
mkdir -p "$T/h2"
out="$(WQ5244_LIVE_KEYS_FILE="$T/live.keys" WQ5244_LOCAL_HOME="$T/h2" bash "$FIX" --apply ptah 2>&1)"; rc=$?
[ $rc -eq 0 ] && [ "$(grep -c gh:FrankStupak "$T/h2/.ssh/authorized_keys")" = 2 ] && [ "$(stat -c %a "$T/h2/.ssh")" = 700 ] \
  && ok "empty home gets both keys" || bad "empty home: $out"

# Case 4: GitHub set drifted (extra key) -> abort rc=3, nothing written.
mkdir -p "$T/h3/.ssh"; : > "$T/h3/.ssh/authorized_keys"
printf '%s\n' "$OTHER" >> "$T/live.keys"
out="$(WQ5244_LIVE_KEYS_FILE="$T/live.keys" WQ5244_LOCAL_HOME="$T/h3" bash "$FIX" --apply thor 2>&1)"; rc=$?
[ $rc -eq 3 ] && [ ! -s "$T/h3/.ssh/authorized_keys" ] && ok "drift aborts fail-closed" || bad "drift: rc=$rc $out"

# Case 5: unknown node rejected.
head -2 "$T/live.keys" > "$T/live2.keys"
out="$(WQ5244_LIVE_KEYS_FILE="$T/live2.keys" WQ5244_LOCAL_HOME="$T/h3" bash "$FIX" seshat 2>&1)"; rc=$?
[ $rc -eq 2 ] && ok "unknown node rejected" || bad "unknown node: rc=$rc $out"

exit $fail
