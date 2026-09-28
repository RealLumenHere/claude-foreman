#!/bin/sh
# wq#2019 — Minerva proposal reviewer: stop rejecting real ideas as
# duplicate-route false-positives and stale hardware-infeasible.
#
# Bug 1 (minerva_dedup.route_conflict, called from minerva_proposal.py's dedup
# gate): flagged a "duplicate route" REJECT on ANY proposal whose text merely
# co-occurred a generic litellm/gateway keyword with an existing route's
# model_name anywhere in the (long) title+proposed_change blob. A proposal
# about prompt caching, quality routing, or memory orchestration that used an
# existing model (glm-5.3-flash-local, qwen36-local, bge-m3, claude-max-sonnet)
# was indistinguishable from a proposal to re-add that exact route. Fixed by
# requiring an explicit add/create/register/onboard/introduce intent verb with
# the route name inside the same local text window, not co-occurrence anywhere
# in the proposal (test_route_conflict.py: 4 real false-positive shapes cleared,
# 2 genuine duplicate-route-adds still caught).
#
# Bug 2 (minerva_proposal.py INVARIANTS + LUMEN_STATE_FALLBACK): hardcoded
# "Xeon E5-2699 v4 (Broadwell), NO AVX-512, NO AMX, no GPU — reject anything
# requiring them" text, unconditionally appended to every judge call regardless
# of the live hardware probe. Stale since Frank added the 7x MacPro6,1 pool
# (E5-2697 v2, 24 threads, 64GB each, ~448GB aggregate) + BC-250 GPUs incoming.
# Updated to describe current fleet hardware and require the judge name which
# node a hardware-infeasible verdict was checked against.
#
# Primary deploy is git: land this PR and let lumen-pr fast-forward
# /home/lumen/minerva. This script is the change-safety replay for a host
# where that ff has not happened: backup -> apply the shipped files ->
# import-health -> auto-revert on fail.
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
DEDUP_TARGET=/home/lumen/minerva/minerva_dedup.py
PROPOSAL_TARGET=/home/lumen/minerva/minerva_proposal.py

# wq#1696 deploy-verify contract (wq#2985). Read-only, before any install step.
# These are in-place library edits, and later tickets (wq#2027 and onward) edit
# the same two files, so a byte-compare would report MISS for every fix that
# landed AFTER this one. The contract here is that this ticket's two behaviours
# are still present.
if [ "${1:-}" = --verify ]; then
  . "$HERE/../_verify.sh"
  vcontains "$DEDUP_TARGET"    'route_conflict'
  vcontains "$DEDUP_TARGET"    'add|create|register|onboard|introduce'
  vcontains "$PROPOSAL_TARGET" 'MacPro6,1'
  vdone
fi

cp "$DEDUP_TARGET" "$DEDUP_TARGET.bak.wq2019"
cp "$PROPOSAL_TARGET" "$PROPOSAL_TARGET.bak.wq2019"
cp "$HERE/minerva_dedup.py" "$DEDUP_TARGET"
cp "$HERE/minerva_proposal.py" "$PROPOSAL_TARGET"

if python3 -c "
import importlib.util
spec = importlib.util.spec_from_file_location('minerva_dedup', '$DEDUP_TARGET')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
assert callable(getattr(mod, 'route_conflict', None))
import tempfile, yaml
cfg = {'model_list': [{'model_name': n} for n in
       ['glm-5.3-flash-local', 'qwen36-local', 'bge-m3', 'claude-max-sonnet']]}
f = tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False)
yaml.safe_dump(cfg, f); f.close()
mod.LITELLM_CONFIG = f.name
fp, _ = mod.route_conflict(
    'Route judge calls through qwen36-local for quality-based model selection '
    'before falling back to a paid rung on the same LiteLLM gateway route.')
assert fp is False, 'false positive still triggers'
tp, name = mod.route_conflict(
    'Add model glm-5.3-flash-local as a new LiteLLM route for local judge traffic.')
assert tp is True and name == 'glm-5.3-flash-local', 'genuine duplicate route-add no longer caught'
print('minerva_dedup health OK')

import ast
src = open('$PROPOSAL_TARGET').read()
ast.parse(src)
assert 'MacPro6,1' in src and 'BC-250' in src, 'hardware invariants not updated'
print('minerva_proposal health OK')
"; then
    echo "wq2019 install: deployed, dedup false-positive fix + hardware invariants updated"
else
    cp "$DEDUP_TARGET.bak.wq2019" "$DEDUP_TARGET"
    cp "$PROPOSAL_TARGET.bak.wq2019" "$PROPOSAL_TARGET"
    echo "wq2019 install: health FAILED — reverted" >&2
    exit 1
fi
