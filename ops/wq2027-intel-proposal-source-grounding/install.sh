#!/bin/sh
# wq#2027 — intel->proposal source-grounding gate.
#
# Class defect (foreman/research/AGENTFORGE_EVAL.md section 0/1): proposals
# ff80d5e94515 and 37cb4ce26a9b were generated from a classified_intel row
# with an empty url AND empty summary — a repo TITLE only. Every existing
# verify() layer checks a claim against Lumen CONTEXT; none of them checked
# whether the claim was true of the EXTERNAL subject, so both scored 1.00.
#
# Three changes, all additive / fail-closed, all OFF-impact when a row
# already has a real url+summary (Deploy section below has the full list):
#   1. minerva_proposal.get_candidates() (ops/wq2019-proposal-reviewer-fix/
#      minerva_proposal.py) refuses to surface a classified_intel row with an
#      empty url or summary — it is not eligible to seed a proposal.
#   2. minerva_verify.source_grounding_check() / verify(source_url=,
#      source_summary=, subject_claim_text=) (ops/wq1997-external-advisory-
#      check/minerva_verify.py) checks the subject exists (GitHub repo API)
#      and that the proposal's claim about it is supported by the fetched
#      summary — fail-closed, flips passed True->False only, backward
#      compatible (no-op unless a caller opts in).
#   3. sweep_empty_source_proposals.py rejects pending proposals whose source
#      row still has no url/summary (this closes ff80d5e94515/37cb4ce26a9b
#      themselves, per the eval doc's own recommendation).
#
# fetch_source.py is the classifier-side fetcher (README head, license,
# commit count, pushed_at) that backfills url/summary so future intel is
# eligible; wiring it into minerva_classifier.py itself is out of scope here
# (that file lives at /home/lumen/minerva, unreachable from this worktree —
# see fetch_source.py's own docstring for the integration point).
#
# quarantine_fabricated_notes.py is the general form of the second finding:
# scratch/research/intel_new_github_repo__roshanrameshhub_agentforge_01a87c.md
# cited a repo tree that does not exist in the real repo.
#
# Primary deploy is git: land this PR and let lumen-pr fast-forward
# /home/lumen/minerva (same convention as wq#1997/wq#2005/wq#2010/wq#2019's
# minerva_verify.py / minerva_proposal.py fixes). This script is the
# change-safety replay for a host where that ff has not happened: backup ->
# apply the shipped files -> import-health -> auto-revert on fail.
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
PROPOSAL_SRC="$HERE/../wq2019-proposal-reviewer-fix/minerva_proposal.py"
VERIFY_SRC="$HERE/../wq1997-external-advisory-check/minerva_verify.py"
PROPOSAL_TARGET=/home/lumen/minerva/minerva_proposal.py
VERIFY_TARGET=/home/lumen/minerva/minerva_verify.py
FETCH_TARGET=/home/lumen/minerva/fetch_source.py
SWEEP_TARGET=/home/lumen/minerva/sweep_empty_source_proposals.py
QUARANTINE_TARGET=/home/lumen/minerva/quarantine_fabricated_notes.py

cp "$PROPOSAL_TARGET" "$PROPOSAL_TARGET.bak.wq2027"
cp "$VERIFY_TARGET" "$VERIFY_TARGET.bak.wq2027"
cp "$PROPOSAL_SRC" "$PROPOSAL_TARGET"
cp "$VERIFY_SRC" "$VERIFY_TARGET"
cp "$HERE/fetch_source.py" "$FETCH_TARGET"
cp "$HERE/sweep_empty_source_proposals.py" "$SWEEP_TARGET"
cp "$HERE/quarantine_fabricated_notes.py" "$QUARANTINE_TARGET"

if python3 -c "
import importlib.util, sys

spec = importlib.util.spec_from_file_location('minerva_verify', '$VERIFY_TARGET')
mv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mv)
sys.modules['minerva_verify'] = mv
assert callable(getattr(mv, 'source_grounding_check', None))
assert callable(getattr(mv, 'subject_exists', None))
assert mv.SOURCE_GROUNDING_CHECK_ENABLED is True  # flag defaults ON

# stub the network/embed legs so this health check proves wiring, not
# local-model/network availability (same technique the sibling installs use)
mv.subject_exists = lambda url: False
mv.semantic_citation_score = lambda claims, context: (1.0, [(c, 1.0, 'ok') for c in claims], None)
mv.judge_entailment_uq = lambda claims, context: (1.0, ['SUPPORTED'] * len(claims), '', None, [1.0] * len(claims))
r = mv.verify('Lumen runs LangGraph.', 'Lumen runs LangGraph.', subject='wq2027_health', mode='semantic',
              source_url='https://github.com/x/nope', source_summary='n/a', subject_claim_text='claim about x')
assert r['passed'] is False and 'subject_not_found' in (r['reason'] or ''), r
r2 = mv.verify('Lumen runs LangGraph.', 'Lumen runs LangGraph.', subject='wq2027_health_noopt', mode='semantic')
assert r2['passed'] is True and r2['source_grounding'] is None, r2   # backward compatible

spec2 = importlib.util.spec_from_file_location('minerva_proposal_health', '$PROPOSAL_TARGET')
mp = importlib.util.module_from_spec(spec2)
spec2.loader.exec_module(mp)
assert callable(getattr(mp, '_ensure_intel_source_columns', None))
import sqlite3
conn = sqlite3.connect(':memory:')
conn.execute('CREATE TABLE classified_intel (id TEXT, source TEXT, title TEXT, url TEXT, processed INTEGER)')
mp._ensure_intel_source_columns(conn)
cols = {r[1] for r in conn.execute('PRAGMA table_info(classified_intel)')}
assert 'summary' in cols, cols

import importlib.util as iu
fspec = iu.spec_from_file_location('fetch_source_health', '$FETCH_TARGET')
fs = iu.module_from_spec(fspec)
fspec.loader.exec_module(fs)
assert callable(getattr(fs, 'fetch_repo_source', None))
assert fs._repo_slug_from_url('https://github.com/owner/repo') == 'owner/repo'

print('wq2027 health OK')
"; then
    echo "wq2027 install: deployed, source-grounding gate ON"
else
    cp "$PROPOSAL_TARGET.bak.wq2027" "$PROPOSAL_TARGET"
    cp "$VERIFY_TARGET.bak.wq2027" "$VERIFY_TARGET"
    rm -f "$FETCH_TARGET" "$SWEEP_TARGET" "$QUARANTINE_TARGET"
    echo "wq2027 install: health FAILED — reverted" >&2
    exit 1
fi
