#!/usr/bin/env bash
# ops/wq3558-ollama-sched-wedge/install.sh — wq#3558 repro + root-cause bundle.
#
# This bundle ships no deploy step of its own: it is the repro (repro.py) and
# the root-cause writeup (README.md) for the ollama keep_alive scheduler wedge,
# not a payload that lands on Lumen. The actual fixes live elsewhere — the
# self-heal mitigation in ops/wq3354-fleet-selfheal (sh_ollama.py, CLASS 7) and
# the client-side fix on minerva's `fix/wq3558-embed-no-keepalive-pin` branch.
#
# --verify instead checks that the README's two load-bearing cross-references
# into wq3354-fleet-selfheal haven't drifted, per ops/VERIFY_CONTRACT.md's "a
# bundle that installs nothing at all still needs a handler" rule: without
# this, class 7 could be deleted, unregistered, or disabled by a later change
# and this bundle's README would keep claiming a fix that no longer runs.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
SH="$SRC/../wq3354-fleet-selfheal"

if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"

  # README's fix target still exists and is wired into the sweep's class table.
  [ -f "$SH/sh_ollama.py" ] || _vmiss "absent: $SH/sh_ollama.py"
  vcontains "$SH/selfheal_sweep.py" '"ollama":[[:space:]]*sh_ollama'

  # README's deploy command ("sudo bash ops/wq3354-fleet-selfheal/install.sh")
  # still ships sh_ollama.py when run.
  vcontains "$SH/install.sh" 'sh_ollama\.py'

  # README's "runs on built-in defaults" claim: class 7 is enabled in the
  # tracked config, not opted out.
  python3 -c "
import sys, yaml
cfg = yaml.safe_load(open('$SH/selfheal.yaml'))
sys.exit(0 if cfg.get('ollama', {}).get('enabled', True) else 1)
" || _vmiss "selfheal.yaml: ollama class 7 is disabled"

  vdone
fi

echo "ops/wq3558-ollama-sched-wedge installs nothing; it is evidence for the" >&2
echo "wq3354-fleet-selfheal class-7 fix. Run that bundle's install.sh to deploy." >&2
exit 1
