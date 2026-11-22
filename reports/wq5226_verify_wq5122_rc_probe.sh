#!/bin/bash
# wq#5226 probe: push-maksoor.sh:10 prints rc=$? after $(date -Is) in the same word.
# Bash expands left to right, so $? holds the exit status of the date substitution,
# not of the preceding rsync. Expected output: line 1 rc=0 (bug), line 2 rc=1 (correct).
# Run: python3 -c "import subprocess;print(subprocess.run(['bash','reports/wq5226_verify_wq5122_rc_probe.sh'],capture_output=True,text=True).stdout)"
false
echo "as-shipped: end $(date -Is) rc=$?"
false
echo "rc-first:   rc=$? end $(date -Is)"
