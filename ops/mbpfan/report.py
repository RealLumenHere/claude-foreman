#!/usr/bin/env python3
"""wq#2508: render the khepri soak as a markdown section (stdout). Equal-elapsed comparison."""
import csv, datetime as dt, collections
import os, sys
CSV = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'reports', '2508_khepri_soak.csv')
rows = [r for r in csv.DictReader(open(CSV)) if r['fan_in'] not in ('', 'ERR')]
ts = lambda r: dt.datetime.fromisoformat(r['ts'].replace('Z', '+00:00'))
ph = collections.OrderedDict()
for r in rows:
    ph.setdefault(r['phase'], []).append(r)
C = lambda r, k: int(r[k]) / 1000

def at(phase, minute):
    xs = ph.get(phase, [])
    if not xs: return None
    t0 = ts(xs[0]); win = [r for r in xs if minute*60-30 <= (ts(r)-t0).total_seconds() < minute*60+30]
    return win[-1] if win else None

def watts(xs):
    w = []
    for a, b in zip(xs, xs[1:]):
        de = int(b['rapl_uj']) - int(a['rapl_uj']); s = (ts(b) - ts(a)).total_seconds()
        if de > 0 and s > 0: w.append(de / 1e6 / s)
    return sum(w) / len(w)

def stat(phase):
    xs = ph[phase]
    fan = [int(r['fan_in']) for r in xs]
    return dict(n=len(xs), fan=(min(fan), max(fan)), man=max(int(r['fan_manual']) for r in xs),
                core=max(C(r, 'core_max') for r in xs), te0t=max(C(r, 'Te0T') for r in xs),
                tc0p=max(C(r, 'TC0P') for r in xs),
                tg=max(max(C(r, 'TG0D'), C(r, 'TG1D')) for r in xs), w=watts(xs),
                t0=xs[0]['ts'], t1=xs[-1]['ts'])

print('| phase | window (UTC) | samples | fan rpm | fan1_manual | peak core | peak Te0T | peak TC0P | peak GPU die (TG0D/TG1D) | mean pkg W (RAPL) |')
print('|---|---|---|---|---|---|---|---|---|---|')
for p in ph:
    if p in ('init', 'install'): continue
    s = stat(p)
    print(f"| {p} | {s['t0'][11:19]}-{s['t1'][11:19]} | {s['n']} | {s['fan'][0]}-{s['fan'][1]} | {s['man']} | "
          f"{s['core']:.1f} | {s['te0t']:.1f} | {s['tc0p']:.1f} | {s['tg']:.1f} | {s['w']:.1f} |")
print()
print('Equal elapsed time under load (max core C / fan rpm / GPU die C):')
print()
print('| minute | SMC auto | mbpfan |')
print('|---|---|---|')
for m in (1, 3, 5, 10, 15, 20, 25, 29):
    a, b = at('load_auto', m), at('load_mbpfan', m)
    f = lambda r: '-' if r is None else f"{C(r,'core_max'):.0f} / {r['fan_in']} / {max(C(r,'TG0D'),C(r,'TG1D')):.1f}"
    print(f'| {m} | {f(a)} | {f(b)} |')
