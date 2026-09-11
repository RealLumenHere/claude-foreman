#!/usr/bin/env bash
# wq#1629 deploy — run from a session with access to /home/lumen/minerva.
# The backfill stays dry-run here on purpose: read the plan, then re-run step 3
# with --apply. minerva.db is backed up first either way.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
MINERVA=/home/lumen/minerva

# 1. the splitter, beside the module that imports it (lumen_state.py:46)
install -m 0644 "$HERE/state_value_split.py" "$MINERVA/state_value_split.py"
install -m 0755 "$HERE/state_value_split.py" /home/lumen/bin/lumen-state-split

# 2. schema: `note` is added by lumen_state._migrate on the next ingest/current,
#    which this touch makes explicit.
python3 "$MINERVA/lumen_state.py" current lumen root_disk >/dev/null

# 3. backup + dry-run backfill
python3 - <<'PY'
import sqlite3
src = sqlite3.connect('/home/lumen/minerva/minerva.db')
dst = sqlite3.connect('/home/lumen/minerva/minerva.db.bak.wq1629')
with dst:
    src.backup(dst)
print('backup: /home/lumen/minerva/minerva.db.bak.wq1629')
PY
/home/lumen/bin/lumen-state-split backfill

cat <<'EOF'
Next:
  lumen-state-split backfill --apply          # after reading the plan
  lumen-state-split backfill -p ALL --apply   # every predicate, not just the big three
  state-regen                                 # re-render the projections
  python3 ops/wq1609-minerva-contradiction-scan/measure_reach.py   # re-measure reach
EOF
