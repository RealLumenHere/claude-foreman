#!/usr/bin/env python3
# POST-HOC descriptive extras, NOT used for the verdict (verdict rule was fixed in CARD.md before the run).
import json,re,math,random,collections
D='data'; rows=[json.loads(l) for l in open(D+'/sample.jsonl')]
def norm(s):
    s=s.lower(); s=re.sub(r'[^a-z0-9 ]',' ',s); s=re.sub(r'\b(a|an|the)\b',' ',s); return ' '.join(s.split())
def cor(x,r): a=norm(x['ans']); return bool(a) and any(al and al in a for al in r['aliases'])
T={m:[json.loads(l) for l in open(f'{D}/treatment_{m}.jsonl')] for m in ('qwen36-local','deepseek-v4-flash')}
d=T['deepseek-v4-flash']
lens=collections.Counter(len(x['lp']) for x in d); vals=[v for x in d for v in x['lp']]
print('deepseek lp: n_tokens hist',dict(lens.most_common(4)),'min',min(vals),'max',max(vals),'distinct',len(set(vals)),'nonzero',sum(1 for v in vals if v!=0))
q=T['qwen36-local']; wc=[cor(x,r) for x,r in zip(q,rows)]; sc=[cor(x,r) for x,r in zip(d,rows)]
U=[1-math.exp(sum(x['lp'])/len(x['lp'])) for x in q]
n=len(rows)
def routed(idx_order,k,wc,sc): 
    e=set(idx_order[:k]); return sum((sc[i] if i in e else wc[i]) for i in range(n))/n
order=sorted(range(n),key=lambda i:-U[i])
print('budget  routed  random  oracle  gain_pts')
for pct in (10,20,30,40,50,60):
    k=round(n*pct/100); r=routed(order,k,wc,sc); rand=(1-pct/100)*sum(wc)/n+(pct/100)*sum(sc)/n
    wrong=[i for i in range(n) if not wc[i]]; oe=set(wrong[:k]); o=sum((sc[i] if i in oe else wc[i]) for i in range(n))/n
    print('%3d%%   %.3f   %.3f   %.3f   %+.1f'%(pct,r,rand,o,(r-rand)*100))
random.seed(0); g=[]
for _ in range(2000):
    s=[random.randrange(n) for _ in range(n)]
    W=[wc[i] for i in s]; S=[sc[i] for i in s]; UU=[U[i] for i in s]
    o=sorted(range(n),key=lambda i:-UU[i]); k=round(.3*n)
    e=set(o[:k]); rt=sum((S[i] if i in e else W[i]) for i in range(n))/n
    g.append((rt-(0.7*sum(W)/n+0.3*sum(S)/n))*100)
g.sort(); print('gain@30%% bootstrap 95%% CI: %.1f .. %.1f pts (median %.1f)'%(g[50],g[1950],g[1000]))
