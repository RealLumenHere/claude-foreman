#!/bin/sh
# wq#1997 — class fix for wq#1512: a Fleet Advisories intel item fabricated
# CVE-2026-83601 / GHSA-3qh4-842w-fvrm for netdata, and minerva_verify's
# citation/judge guard scored the proposal that echoed it citation=1.0
# judge=1.0 final=1.0, because those layers only check a claim against
# CONTEXT, never whether CONTEXT is externally true. This adds a Layer 0
# external-existence check (NVD for CVE ids, GitHub's global advisory DB for
# GHSA ids, then the owning repo's own advisory list) that runs last in
# verify() — after any latent-guard rescue — and can only flip a passing
# verdict to failing. wq#2005 added the repo-scoped tier and an inconclusive
# bucket after wq#2001 showed CVE-2026-83601 is a REAL netdata advisory that
# the global indexes had not synced yet, so the original check auto-rejected
# the fresh security proposals it exists to protect. wq#2010 then closed the
# fail-open that tier opened: an errored global lookup no longer demotes a
# definitive repo-scoped miss into the soft bucket, and the soft bucket itself
# blocks (under a distinct reason) instead of passing on a review flag nothing
# consumed. ON by default
# (EXTERNAL_ADVISORY_CHECK_ENABLED=1): it is a correctness fix, and it is a
# no-op regex scan on the majority of proposals that cite no CVE/GHSA id.
#
# Primary deploy is git: land this PR and let lumen-pr fast-forward
# /home/lumen/minerva. This script is the change-safety replay for a host
# where that ff has not happened: backup -> apply the shipped file ->
# import-health -> auto-revert on fail.
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
TARGET=/home/lumen/minerva/minerva_verify.py

cp "$TARGET" "$TARGET.bak.wq1997"
cp "$HERE/minerva_verify.py" "$TARGET"

if python3 -c "
import importlib.util
spec = importlib.util.spec_from_file_location('minerva_verify', '$TARGET')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
assert callable(getattr(mod, 'external_advisory_check', None))
assert mod.EXTERNAL_ADVISORY_CHECK_ENABLED is True  # flag defaults ON
r = mod.external_advisory_check('cites CVE-2026-83601 as a MEDIUM netdata advisory')
assert r['unresolved'] == [] and r['inconclusive'] == [], r   # wq#2005: real, repo-scoped
r2 = mod.external_advisory_check('no advisory ids mentioned here')
assert r2['checked'] == [] and r2['unresolved'] == [], r2
r3 = mod.external_advisory_check('cites CVE-2026-99999 as a HIGH netdata advisory')
assert r3['unresolved'] == ['CVE-2026-99999'], r3             # fabricated still blocks
mod._cve_exists = lambda cve_id: None                         # wq#2010: NVD outage...
mod._repo_advisory_exists = lambda ident, repos: False        # ...over a definitive repo miss
r4 = mod.external_advisory_check('cites CVE-2026-99999 as a HIGH netdata advisory')
assert r4['unresolved'] == ['CVE-2026-99999'], r4             # must not demote to soft bucket
print('health OK')
"; then
    echo "wq1997 install: deployed, external advisory check ON"
else
    cp "$TARGET.bak.wq1997" "$TARGET"
    echo "wq1997 install: health FAILED — reverted" >&2
    exit 1
fi
