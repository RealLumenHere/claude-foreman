#!/usr/bin/env bash
# wq#1632 deploy — run from a session with access to /home/lumen/minerva.
# Order per DESIGN.md "Migration order". Backfill stays dry-run here on purpose;
# eyeball the report, then re-run step 3 with --apply.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REG=/home/lumen/minerva/verify/registry.yaml
BIN=/home/lumen/bin/lumen-state-lifecycle

# wq#1696 deploy-verify contract (wq#2985). Read-only, before any install step.
# registry.yaml is shared with wq#1613 and merged, never clobbered, so it is
# checked for the lifecycle predicates rather than byte-compared.
if [ "${1:-}" = --verify ]; then
  . "$HERE/../_verify.sh"
  vfile     "$HERE/state_lifecycle.py" "$BIN"
  vcontains "$REG" "closes_on_decommission: true"
  vdone
fi

install -m 0755 "$HERE/state_lifecycle.py" "$BIN"

# 1. registry: seed file only if none exists (shared with wq#1613, never clobbered).
#    The merge of the live predicate vocabulary is opt-in (--seed-registry) since
#    wq#3004: registry.yaml is now wq#1613's curated, commented, git-tracked file,
#    and seed-registry would append every unregistered live predicate (639 on
#    2026-09-28) as class=config and rewrite the file through yaml.safe_dump,
#    dropping its comments.
mkdir -p /home/lumen/minerva/verify
[ -e "$REG" ] || install -m 0644 "$HERE/registry.seed.yaml" "$REG"
[ "${1:-}" = --seed-registry ] && "$BIN" seed-registry

# 2. dry-run report
/home/lumen/bin/lumen-state-lifecycle backfill

cat <<'EOF'
Next (manual, per DESIGN.md):
  lumen-state-lifecycle backfill --apply   # after eyeballing the dry-run
  state-regen                              # re-render projections
  # thoth: leave status=OFFLINE to the wq#1608 reconciler / an operator:
  #   lumen-state-lifecycle close-on-up thoth --source-ref 'reconcile:<probe>'
EOF
