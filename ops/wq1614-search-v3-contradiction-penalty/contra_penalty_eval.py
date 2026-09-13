#!/usr/bin/env python3
"""wq#1614 contradiction-penalty evaluation.

Two modes, because the two questions have different data requirements.

  reach   Does the contradiction index even touch the queries trap@1 is made of?
          Needs only the frozen golden set, the note_contradictions export and a
          prior recall_bench artifact. No DB, no embeddings, runs anywhere.
          This is the diagnostic the ticket asks for FIRST: "if it does not move,
          the contradiction index is not reaching the notes that matter."

  gate    The adoption gate. Runs eval/recall_bench.py twice against a STAGED
          lumen_recall2.py -- once at MEMORY_V4_W_CONTRA=1.0 (which the unit
          tests prove is byte-identical to the unpatched file) and once at the
          candidate weight -- and refuses on any rise in trap@1 (wq#595), any
          control-gate failure, any drop in abstention pass-rate, and any
          per-class R@1 regression worse than 2pt. Needs minerva.db, so this runs
          on the live tree only; install.sh calls it before copying anything in.

Both modes record the sha256 of the golden file actually used.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

EVAL_DIR = Path('/home/lumen/minerva/eval')
GOLDEN = EVAL_DIR / 'golden_v4_frozen_20260814.jsonl'
GOLDEN_SHA = '8ba5afb0ce12585606b8dcff5ffb8245af24b0f0f32ccbddf3d4121f1bbf46b7'
FIXTURE = EVAL_DIR / 'fixtures' / 'note_contradictions_20260913.json'
BENCH = EVAL_DIR / 'recall_bench.py'
CLASS_R1_TOLERANCE = 0.02          # wq#574 adopt rule: no class may lose >2pt R@1


def sha256_file(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def load_golden(path):
    with open(path) as fh:
        return [json.loads(l) for l in fh if l.strip()]


def load_fixture(path):
    with open(path) as fh:
        rows = json.load(fh)
    return rows, {r['note_id'] for r in rows
                  if r.get('resolved_at') is None and r.get('cleared_at') is None}


# ── reach ──────────────────────────────────────────────────────────────────────

def reach(args):
    golden = load_golden(args.golden)
    rows, open_ids = load_fixture(args.fixture)
    trap_rows = [q for q in golden if q.get('traps')]

    id_typed = [q for q in trap_rows if (q['traps'].get('ids') or [])]
    reached = []
    for q in id_typed:
        for pfx in q['traps']['ids']:
            if any(nid.startswith(pfx) for nid in open_ids):
                reached.append({'qid': q['qid'], 'prefix': pfx})

    baseline_trap = None
    if args.baseline_results:
        with open(args.baseline_results) as fh:
            baseline_trap = json.load(fh)['summary']['trap']

    report = {
        'mode': 'reach',
        'ts': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'golden_path': str(args.golden),
        'golden_sha256': sha256_file(args.golden),
        'golden_sha256_expected': GOLDEN_SHA,
        'golden_sha256_ok': sha256_file(args.golden) == GOLDEN_SHA,
        'fixture_path': str(args.fixture),
        'fixture_sha256': sha256_file(args.fixture),
        'contradiction_rows': len(rows),
        'contradicted_notes_open': len(open_ids),
        'contradicted_subjects': sorted({r['subject'] for r in rows if r.get('subject')}),
        'contradicted_predicates': sorted({r['predicate'] for r in rows
                                           if r.get('predicate')}),
        'n_trap_rows': len(trap_rows),
        'trap_rows_id_typed': [q['qid'] for q in id_typed],
        'trap_rows_substr_only': [q['qid'] for q in trap_rows
                                  if not (q['traps'].get('ids') or [])],
        'id_typed_traps_in_contradiction_index': reached,
        'baseline_trap_block': baseline_trap,
        'note': ('Only id-typed trap rows can be resolved to a note id without '
                 'reading note content, so this is a LOWER BOUND on reach. '
                 'A substr-only trap may still be a contradicted note.'),
    }
    if baseline_trap:
        report['trap_at1_qids_id_typed'] = [
            q['qid'] for q in id_typed if q['qid'] in baseline_trap['trap_at1_qids']]
    print(json.dumps(report, indent=2))
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=2) + '\n')
        print(f'wrote {args.out}', file=sys.stderr)
    return 0


# ── gate ───────────────────────────────────────────────────────────────────────

_SHIM = '''
import importlib.util, runpy, sys
spec = importlib.util.spec_from_file_location('lumen_recall2', {staged!r})
m = importlib.util.module_from_spec(spec)
sys.modules['lumen_recall2'] = m          # recall_bench/lumen_episode import THIS
spec.loader.exec_module(m)
sys.argv = {argv!r}
runpy.run_path({bench!r}, run_name='__main__')
'''


def run_bench(staged, label, w_contra, golden, fixture, engine='v3'):
    """Run recall_bench against `staged` and return its parsed artifact."""
    env = dict(os.environ)
    env['MEMORY_V4_W_CONTRA'] = str(w_contra)
    env['MEMORY_V4_CONTRA_FIXTURE'] = str(fixture)
    argv = ['recall_bench.py', '--label', label, '--engine', engine,
            '--golden', str(golden)]
    code = _SHIM.format(staged=str(staged), argv=argv, bench=str(BENCH))
    before = set(EVAL_DIR.glob(f'results_{label}_*.json'))
    proc = subprocess.run([sys.executable, '-c', code], env=env,
                          cwd=str(EVAL_DIR), capture_output=True, text=True)
    new = sorted(set(EVAL_DIR.glob(f'results_{label}_*.json')) - before)
    if not new:
        sys.stderr.write(proc.stdout[-4000:] + proc.stderr[-4000:])
        raise SystemExit(f'{label}: recall_bench produced no artifact '
                         f'(exit {proc.returncode})')
    with open(new[-1]) as fh:
        art = json.load(fh)
    art['_artifact_path'] = str(new[-1])
    art['_exit_code'] = proc.returncode
    return art


def compare(base, treat, w_contra):
    b, t = base['summary'], treat['summary']
    checks = []

    def check(name, ok, detail):
        checks.append({'check': name, 'ok': bool(ok), 'detail': detail})

    check('golden_sha_matches_frozen',
          t['runtime_config']['golden_sha256'] == GOLDEN_SHA,
          t['runtime_config']['golden_sha256'])
    check('baseline_control_ok', b['control_ok'], b['control_results'])
    check('treatment_control_ok', t['control_ok'], t['control_results'])

    b_trap, t_trap = b['trap']['trap_at1_rate'], t['trap']['trap_at1_rate']
    check('trap_at1_no_rise', t_trap <= b_trap,
          {'baseline': b_trap, 'treatment': t_trap,
           'baseline_qids': b['trap']['trap_at1_qids'],
           'treatment_qids': t['trap']['trap_at1_qids']})

    check('abstention_pass_rate_no_drop',
          t['abstention']['pass_rate'] >= b['abstention']['pass_rate'],
          {'baseline': b['abstention']['pass_rate'],
           'treatment': t['abstention']['pass_rate']})

    check('headline_r1_no_drop', t['headline']['R@1'] >= b['headline']['R@1'],
          {'baseline': b['headline']['R@1'], 'treatment': t['headline']['R@1']})
    check('headline_mrr_no_drop', t['headline']['MRR'] >= b['headline']['MRR'],
          {'baseline': b['headline']['MRR'], 'treatment': t['headline']['MRR']})

    regressed = {c: round(t['per_class'][c]['R@1'] - v['R@1'], 4)
                 for c, v in b['per_class'].items()
                 if v.get('scope') == 'headline' and c in t['per_class']
                 and t['per_class'][c]['R@1'] < v['R@1'] - CLASS_R1_TOLERANCE}
    check('no_class_r1_regression_over_2pt', not regressed, regressed)

    moved = t_trap < b_trap
    return {
        'mode': 'gate',
        'ts': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'w_contra': w_contra,
        'golden_sha256': t['runtime_config']['golden_sha256'],
        'baseline_artifact': base['_artifact_path'],
        'treatment_artifact': treat['_artifact_path'],
        'trap_at1_rate': {'baseline': b_trap, 'treatment': t_trap,
                          'delta': round(t_trap - b_trap, 4),
                          'n_trap_rows': b['trap']['n_trap_rows']},
        'headline': {'baseline': b['headline'], 'treatment': t['headline']},
        'checks': checks,
        'pass': all(c['ok'] for c in checks),
        'trap_at1_moved': moved,
        'verdict': ('ADOPT' if all(c['ok'] for c in checks) and moved else
                    'INERT — penalty is safe but moves nothing; per the ticket the '
                    'contradiction INDEX is not reaching the trap notes, fix the '
                    'index not the weight'
                    if all(c['ok'] for c in checks) else 'REJECT'),
    }


def gate(args):
    if not BENCH.exists():
        raise SystemExit(f'{BENCH} not found — gate mode needs the live tree')
    base = run_bench(args.staged, args.label_prefix + '_w1', 1.0,
                     args.golden, args.fixture)
    treat = run_bench(args.staged, args.label_prefix + '_w%s' % args.w_contra,
                      args.w_contra, args.golden, args.fixture)
    report = compare(base, treat, args.w_contra)
    print(json.dumps(report, indent=2))
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=2) + '\n')
    return 0 if report['pass'] else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)

    r = sub.add_parser('reach', help='index-coverage diagnostic (no DB required)')
    r.add_argument('--golden', default=str(GOLDEN))
    r.add_argument('--fixture', default=str(FIXTURE))
    r.add_argument('--baseline-results', default=None,
                   help='a prior recall_bench artifact, for its trap block')
    r.add_argument('--out', default=None)
    r.set_defaults(func=reach)

    g = sub.add_parser('gate', help='adoption gate (needs minerva.db)')
    g.add_argument('--staged', required=True, help='patched lumen_recall2.py to test')
    g.add_argument('--w-contra', type=float, default=0.5)
    g.add_argument('--golden', default=str(GOLDEN))
    g.add_argument('--fixture', default=str(FIXTURE))
    g.add_argument('--label-prefix', default='wq1614')
    g.add_argument('--out', default=None)
    g.set_defaults(func=gate)

    args = ap.parse_args()
    sys.exit(args.func(args))


if __name__ == '__main__':
    main()
