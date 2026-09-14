#!/bin/sh
# wq#1927 — meta-review synthesis stage for minerva_verify (proposal f75013a36dcd,
# plan 19544a054f8d, minerva PR #18). Primary deploy was git: PR #18 merged and
# lumen-pr fast-forwarded /home/lumen/minerva. This script is the change-safety
# replay for a host where that ff has not happened: backup -> apply the shipped
# file -> import-health -> auto-revert on fail.
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
TARGET=/home/lumen/minerva/minerva_verify.py

cp "$TARGET" "$TARGET.bak.wq1927"
cp "$HERE/minerva_verify.py" "$TARGET"

if python3 -c "
import importlib.util
spec = importlib.util.spec_from_file_location('minerva_verify', '$TARGET')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
assert callable(getattr(mod, 'meta_review_synthesis', None))
assert mod.META_REVIEW_ENABLED is False  # flag defaults OFF
print('health OK')
"; then
    echo "wq1927 install: deployed, flag off (enable with META_REVIEW_ENABLED=1)"
else
    cp "$TARGET.bak.wq1927" "$TARGET"
    echo "wq1927 install: health FAILED — reverted" >&2
    exit 1
fi
