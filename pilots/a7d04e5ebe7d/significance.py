#!/usr/bin/env python3
"""wq#1684 pilot a7d04e5ebe7d — is the arm A / arm B R@1 gap real, or one query?

At n=50 one query moves R@1 by 0.02. Exact paired McNemar on the per-query
rank-1 hit/miss vectors already written to results.json by run_pilot.py:
discordant pairs only (b = A hit & B miss, c = B hit & A miss), two-sided exact
binomial on min(b, c) against b + c trials at p = 0.5. Pre-registered win
condition on CARD.md: arm_b_r_at_1 > arm_a_r_at_1 AND p < 0.05.
"""
import json
from math import comb
from pathlib import Path

HERE = Path(__file__).resolve().parent
results = json.loads((HERE / 'results.json').read_text())

b = c = both = neither = 0
for row in results['per_query']:
    if row['hit_a'] and not row['hit_b']:
        b += 1
    elif row['hit_b'] and not row['hit_a']:
        c += 1
    elif row['hit_a'] and row['hit_b']:
        both += 1
    else:
        neither += 1

n_disc = b + c
if n_disc == 0:
    p_value = 1.0
else:
    k = min(b, c)
    p_value = sum(comb(n_disc, i) for i in range(0, k + 1)) * 2 / (2 ** n_disc)
    p_value = min(p_value, 1.0)

out = {
    'both_hit': both,
    'neither_hit': neither,
    'a_only': b,
    'b_only': c,
    'discordant_pairs': n_disc,
    'mcnemar_p_value_exact': p_value,
    'arm_a_r_at_1': results['arm_a_r_at_1'],
    'arm_b_r_at_1': results['arm_b_r_at_1'],
    'win_condition_met': results['arm_b_r_at_1'] > results['arm_a_r_at_1'] and p_value < 0.05,
}
(HERE / 'significance.json').write_text(json.dumps(out, indent=2))
print(json.dumps(out, indent=2))
