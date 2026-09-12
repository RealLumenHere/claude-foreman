#!/usr/bin/env python3
"""wq#1791 pilot 9d95766f5f43 — time-ordered holdout transfer test.

The simulation in run_pilot.py assumes each arm's historical rate IS its true rate.
This script drops that assumption: it learns the arm ranking on the OLDER half of
the proposal record and scores the resulting policy on the NEWER half only, which is
the question that actually matters — does exploiting past acceptance still pay on
proposals decided later?

Baseline: uniform over arms (what prompt_engine.py does today).
Treatment: play the arm that looked best on the training half.
Both scored on the test half's own accepted/not rows, bootstrap CI over test rows.
"""
import argparse
import collections
import json
import random
from pathlib import Path

HERE = Path(__file__).resolve().parent


def rates(rows, key, min_n):
    agg = collections.defaultdict(lambda: [0, 0])
    for r in rows:
        a = agg[r[key] or 'unknown']
        a[0] += 1
        a[1] += r['accepted']
    return {k: (n, acc, acc / n) for k, (n, acc) in agg.items() if n >= min_n}


def score(pool, arms, weights, rng, reps=10000):
    """Mean accepted per 100 draws when arms are drawn with `weights` and each draw
    resolves to a real test-half row of that arm."""
    names = list(arms)
    out = []
    for _ in range(reps):
        a = rng.choices(names, weights=weights)[0]
        out.append(rng.choice(pool[a]))
    m = sum(out) / len(out) * 100
    return m, out


def ci(vals, rng, reps=2000):
    n = len(vals)
    ms = sorted(sum(vals[rng.randrange(n)] for _ in range(n)) / n * 100 for _ in range(reps))
    return round(ms[int(0.025 * reps)], 3), round(ms[int(0.975 * reps)], 3)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--key', default='category')
    ap.add_argument('--min-n', type=int, default=20)
    ap.add_argument('--out', default='results_holdout.json')
    args = ap.parse_args()

    sample = json.loads((HERE / 'sample.json').read_text())
    rows = sorted(sample['rows'], key=lambda r: r['created_at'])
    half = len(rows) // 2
    train, test = rows[:half], rows[half:]

    tr = rates(train, args.key, args.min_n)
    te = rates(test, args.key, args.min_n)
    arms = sorted(set(tr) & set(te))
    best_train = max(arms, key=lambda a: tr[a][2])

    pool = collections.defaultdict(list)
    for r in test:
        name = r[args.key] or 'unknown'
        if name in arms:
            pool[name].append(r['accepted'])

    rng = random.Random(20260912)
    base_m, base_v = score(pool, arms, [1] * len(arms), rng)
    treat_m, treat_v = score(pool, arms, [1 if a == best_train else 0 for a in arms], rng)
    res = {
        'arm_key': args.key, 'min_n': args.min_n, 'arms': arms,
        'train_rows': len(train), 'test_rows': len(test),
        'split_at': test[0]['created_at'],
        'train_rates': {a: round(tr[a][2], 4) for a in arms},
        'test_rates': {a: round(te[a][2], 4) for a in arms},
        'best_arm_on_train': best_train,
        'best_arm_on_test': max(arms, key=lambda a: te[a][2]),
        'baseline_uniform_per_100': round(base_m, 3),
        'baseline_ci95': ci(base_v, rng),
        'treatment_exploit_train_best_per_100': round(treat_m, 3),
        'treatment_ci95': ci(treat_v, rng),
        'delta_per_100': round(treat_m - base_m, 3),
    }
    (HERE / args.out).write_text(json.dumps(res, indent=2, sort_keys=True))
    print(f"split {res['split_at']}  arms {arms}")
    print(f"train best {best_train} ({res['train_rates'][best_train]}) | "
          f"test best {res['best_arm_on_test']} ({res['test_rates'][res['best_arm_on_test']]})")
    print(f"baseline  {res['baseline_uniform_per_100']}/100 CI {res['baseline_ci95']}")
    print(f"treatment {res['treatment_exploit_train_best_per_100']}/100 CI {res['treatment_ci95']}")
    print(f"delta     {res['delta_per_100']:+.3f}/100")


if __name__ == '__main__':
    main()
