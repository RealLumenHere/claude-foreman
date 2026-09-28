#!/usr/bin/env bash
# wq#1632 deploy — run from a session with access to /home/lumen/minerva.
# Order per DESIGN.md "Migration order". Backfill stays dry-run here on purpose;
# eyeball the report, then re-run step 3 with --apply.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REG=/home/lumen/minerva/verify/registry.yaml

# wq#1696 deploy-verify contract (wq#2985). Read-only, before any install step.
# registry.yaml is shared with wq#1613 and merged, never clobbered, so it is
# checked for the lifecycle predicates this bundle seeds rather than byte-compared.
if [ "${1:-}" = --verify ]; then
  . "$HERE/../_verify.sh"
  vfile     "$HERE/state_lifecycle.py" /home/lumen/bin/lumen-state-lifecycle
  vcontains "$REG" 'lifecycle'
  vdone
fi

install -m 0755 "$HERE/state_lifecycle.py" /home/lumen/bin/lumen-state-lifecycle

# 1. registry: seed file first (never clobber an existing one — shared with wq#1613),
#    then merge the live predicate vocabulary in.
mkdir -p /home/lumen/minerva/verify
[ -e "$REG" ] || install -m 0644 "$HERE/registry.seed.yaml" "$REG"
/home/lumen/bin/lumen-state-lifecycle seed-registry

# 2. dry-run report
/home/lumen/bin/lumen-state-lifecycle backfill

cat <<'EOF'
Next (manual, per DESIGN.md):
  lumen-state-lifecycle backfill --apply   # after eyeballing the dry-run
  state-regen                              # re-render projections
  # thoth: leave status=OFFLINE to the wq#1608 reconciler / an operator:
  #   lumen-state-lifecycle close-on-up thoth --source-ref 'reconcile:<probe>'
EOF
