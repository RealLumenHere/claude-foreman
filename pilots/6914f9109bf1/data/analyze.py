#!/usr/bin/env python3
import json,statistics as st,os
D='/home/lumen/foreman/pilots/6914f9109bf1/data'
def L(c): return [json.loads(l) for l in open(f'{D}/res_{c}.jsonl')]
base=L('none'); cfgs=['ngram-simple','ngram-map-k','ngram-mod']
def med(xs): return st.median(xs) if xs else float('nan')
def ratio(res,kind,key):
    return med([r[key]/b[key] for r,b in zip(res,base) if r['kind']==kind and r.get(key) and b.get(key)])
out={}
if os.path.exists(f'{D}/conn.log'):
    import collections
    cc=collections.Counter(); bad=collections.Counter()
    for l in open(f'{D}/conn.log'):
        f=l.split()
        if len(f)>=3:
            cc[f[1]]+=1
            if f[2]!='conn=0': bad[f[1]]+=1
    print('conn.log samples per config',dict(cc),'samples with an ollama client',dict(bad))
print('baseline decode tok/s:',{r['id']:round(r['pred_tps'],2) for r in base})
if os.path.exists(f'{D}/res_none2.jsonl'):
    n2=L('none2'); drift=med([a['pred_tps']/b['pred_tps'] for a,b in zip(n2,base)]); print('drift none2/none decode median: %.3f'%drift); out['drift']=drift
for c in cfgs:
    if not os.path.exists(f'{D}/res_{c}.jsonl'): print(c,'MISSING'); continue
    r=L(c)
    ident=sum(1 for a,b in zip(r,base) if a['text']==b['text'] and a['reasoning']==b['reasoning'])
    div=[]
    for a,b in zip(r,base):
        x=(a['reasoning']+'|'+a['text']); y=(b['reasoning']+'|'+b['text'])
        if x!=y: div.append((a['id'],next((i for i,(p,q) in enumerate(zip(x,y)) if p!=q),min(len(x),len(y)))))
    dn=sum(x['draft_n'] or 0 for x in r); da=sum(x['draft_acc'] or 0 for x in r)
    o=dict(reason_ratio=ratio(r,'reason','pred_tps'),copy_ratio=ratio(r,'copy','pred_tps'),prefill_ratio=med([a['prompt_tps']/b['prompt_tps'] for a,b in zip(r,base) if a.get('prompt_tps') and b.get('prompt_tps')]),
           identical=f'{ident}/8',first_divergence_char=div,accept=(da/dn if dn else None),draft_n=dn)
    out[c]=o; print(c,{k:(round(v,3) if isinstance(v,float) else v) for k,v in o.items()})
json.dump(out,open(f'{D}/results.json','w'),indent=1)
