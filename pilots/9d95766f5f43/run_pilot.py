#!/usr/bin/env python3
"""wq#1791 pilot 9d95766f5f43 — static vs explore-exploit prompt sampling, offline replay.

Both policies are run over COMMON RANDOM NUMBERS: for each seed a reward matrix
reward[arm][t] ~ Bernoulli(p_arm) is generated once, and every policy reads the cell
for whichever arm it played at step t. Identical realizations, so the per-seed
difference is paired and the CI is a paired CI.

p_arm is the arm's empirical acceptance rate in sample.json (production decisions).
Nothing here touches the live prompt-engine path.

Usage:
  run_pilot.py --arm baseline  --out results_baseline.json
  run_pilot.py --arm treatment --out results_treatment.json
  run_pilot.py --arm all       --out results.json
"""
import argparse
import collections
import hashlib
import json
import math
import random
from pathlib import Path

HERE = Path(__file__).resolve().parent
HORIZON = 300
SEEDS = 2000
BOOTSTRAP = 10000


def load_sample(path):
    raw = path.read_bytes()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def build_arms(rows, key, min_n=1):
    agg = collections.defaultdict(lambda: [0, 0])  # name -> [n, accepted]
    for r in rows:
        name = r[key] or 'unknown'
        agg[name][0] += 1
        agg[name][1] += r['accepted']
    arms = [(name, n, acc, acc / n) for name, (n, acc) in agg.items() if n >= min_n]
    arms.sort(key=lambda a: -a[1])
    return arms


def reward_matrix(rng, probs, horizon):
    return [[1 if rng.random() < p else 0 for _ in range(horizon)] for p in probs]


def play_uniform(rng, rewards, horizon, k, weights=None):
    idx = list(range(k))
    total = 0
    for t in range(horizon):
        a = rng.choices(idx, weights=weights)[0] if weights else rng.randrange(k)
        total += rewards[a][t]
    return total


def play_thompson(rng, rewards, horizon, k, delay=0):
    alpha, beta = [1.0] * k, [1.0] * k
    pending, total = [], 0
    for t in range(horizon):
        a = max(range(k), key=lambda i: rng.betavariate(alpha[i], beta[i]))
        r = rewards[a][t]
        total += r
        pending.append((t, a, r))
        if delay:
            ready = [p for p in pending if t - p[0] >= delay]
            pending = [p for p in pending if t - p[0] < delay]
        else:
            ready, pending = pending, []
        for _, aa, rr in ready:
            alpha[aa] += rr
            beta[aa] += 1 - rr
    return total


def play_epsilon(rng, rewards, horizon, k, eps=0.1):
    n = [0] * k
    s = [0.0] * k
    total = 0
    for t in range(horizon):
        if rng.random() < eps or min(n) == 0:
            a = rng.randrange(k)
        else:
            a = max(range(k), key=lambda i: s[i] / n[i])
        r = rewards[a][t]
        total += r
        n[a] += 1
        s[a] += r
    return total


def play_oracle(rewards, horizon, best):
    return sum(rewards[best][:horizon])


def mean(xs):
    return sum(xs) / len(xs)


def paired_bootstrap_ci(diffs, rng, reps=BOOTSTRAP, alpha=0.05):
    n = len(diffs)
    means = []
    for _ in range(reps):
        means.append(mean([diffs[rng.randrange(n)] for _ in range(n)]))
    means.sort()
    lo = means[int((alpha / 2) * reps)]
    hi = means[min(reps - 1, int((1 - alpha / 2) * reps))]
    return lo, hi


def run(arms, horizon, seeds, delay=0):
    """Returns per-seed accepted-per-100 vectors for every policy."""
    probs = [a[3] for a in arms]
    k = len(arms)
    best = max(range(k), key=lambda i: probs[i])
    hist_w = [a[1] for a in arms]
    out = collections.defaultdict(list)
    scale = 100.0 / horizon
    for seed in range(seeds):
        rng = random.Random(1000003 + seed)
        rewards = reward_matrix(rng, probs, horizon)
        out['baseline_uniform'].append(
            play_uniform(random.Random(seed * 7 + 1), rewards, horizon, k) * scale)
        out['baseline_histmix'].append(
            play_uniform(random.Random(seed * 7 + 2), rewards, horizon, k, hist_w) * scale)
        out['treatment_thompson'].append(
            play_thompson(random.Random(seed * 7 + 3), rewards, horizon, k, delay) * scale)
        out['treatment_epsilon_greedy'].append(
            play_epsilon(random.Random(seed * 7 + 4), rewards, horizon, k) * scale)
        out['oracle_best_arm'].append(play_oracle(rewards, horizon, best) * scale)
    return out, arms[best][0]


def summarize(vectors, best_arm, arms, horizon, delay, rng):
    base = vectors['baseline_uniform']
    res = {'horizon': horizon, 'seeds': len(base), 'feedback_delay': delay,
           'best_arm': best_arm,
           'arms': [{'name': a[0], 'n': a[1], 'accepted': a[2], 'rate': round(a[3], 4)}
                    for a in arms],
           'policies': {}}
    for name, v in vectors.items():
        entry = {'mean_accepted_per_100': round(mean(v), 3)}
        if name != 'baseline_uniform':
            diffs = [x - y for x, y in zip(v, base)]
            lo, hi = paired_bootstrap_ci(diffs, rng)
            entry['delta_vs_baseline'] = round(mean(diffs), 3)
            entry['ci95'] = [round(lo, 3), round(hi, 3)]
        res['policies'][name] = entry
    return res


def stationarity(rows, key):
    rows = sorted(rows, key=lambda r: r['created_at'])
    half = len(rows) // 2
    out = {}
    for label, part in (('first_half', rows[:half]), ('second_half', rows[half:])):
        for name, n, acc, rate in build_arms(part, key):
            out.setdefault(name, {})[label] = {'n': n, 'rate': round(rate, 4)}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--arm', choices=['baseline', 'treatment', 'all'], default='all')
    ap.add_argument('--key', default='category', choices=['category', 'source'])
    ap.add_argument('--horizon', type=int, default=HORIZON)
    ap.add_argument('--seeds', type=int, default=SEEDS)
    ap.add_argument('--delay', type=int, default=0)
    ap.add_argument('--min-n', type=int, default=1)
    ap.add_argument('--out', default='results.json')
    args = ap.parse_args()

    sample, sha = load_sample(HERE / 'sample.json')
    rows = sample['rows']
    arms = build_arms(rows, args.key, args.min_n)
    vectors, best = run(arms, args.horizon, args.seeds, args.delay)
    if args.arm == 'baseline':
        vectors = {k: v for k, v in vectors.items() if k.startswith('baseline')}
    elif args.arm == 'treatment':
        # baseline_uniform stays: it is the pairing reference for every delta.
        vectors = {k: v for k, v in vectors.items()
                   if k.startswith('treatment') or k == 'baseline_uniform'}
    res = summarize(vectors, best, arms, args.horizon, args.delay, random.Random(4242))
    res['sample_sha256'] = sha
    res['sample_rows'] = len(rows)
    res['arm_key'] = args.key
    res['attribution'] = sample['attribution']
    res['stationarity'] = stationarity(rows, args.key)
    out = HERE / args.out
    out.write_text(json.dumps(res, indent=2, sort_keys=True))
    for name, e in sorted(res['policies'].items()):
        line = f"{name:26} {e['mean_accepted_per_100']:7.3f}/100"
        if 'delta_vs_baseline' in e:
            line += f"  delta {e['delta_vs_baseline']:+.3f}  CI95 {e['ci95']}"
        print(line)
    print(f"wrote {out}")


if __name__ == '__main__':
    main()
