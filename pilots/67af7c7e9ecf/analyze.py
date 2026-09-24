#!/usr/bin/env python3
import json,re,random,statistics as st
D='/home/lumen/foreman/pilots/67af7c7e9ecf/data'
rows=[json.loads(l) for l in open(D+'/sample.jsonl')]
def norm(s):
    s=s.lower(); s=re.sub(r'[^a-z0-9 ]',' ',s); s=re.sub(r'\b(a|an|the)\b',' ',s); return ' '.join(s.split())
def correct(ans,r): a=norm(ans); return bool(a) and any(al and (al in a) for al in r['aliases'])
def load(arm,m): return [json.loads(l) for l in open(f'{D}/{arm}_{m}.jsonl')]
def auroc(score,wrong):  # P(score_wrong > score_right), ties 0.5
    pos=[s for s,w in zip(score,wrong) if w]; neg=[s for s,w in zip(score,wrong) if not w]
    if not pos or not neg: return float('nan')
    g=sum((p>n)+0.5*(p==n) for p in pos for n in neg); return g/(len(pos)*len(neg))
def boot(score,wrong,n=1000):
    random.seed(0); idx=list(range(len(score))); v=[]
    for _ in range(n):
        s=[random.choice(idx) for _ in idx]; a=auroc([score[i] for i in s],[wrong[i] for i in s])
        if a==a: v.append(a)
    v.sort(); return v[int(.025*len(v))],v[int(.975*len(v))]
import math
R={}
for m in ['qwen36-local','deepseek-v4-flash']:
    b=load('baseline',m); t=load('treatment',m)
    cb=[correct(x['ans'],r) for x,r in zip(b,rows)]; ct=[correct(x['ans'],r) for x,r in zip(t,rows)]
    same=sum(x['ans']==y['ans'] for x,y in zip(b,t))/len(rows)
    errs=sum(1 for x in b+t if x['err'])
    U=[(1-math.exp(sum(x['lp'])/len(x['lp']))) if x['lp'] else None for x in t]
    have=[u is not None for u in U]
    wrong=[not c for c in ct]
    a=auroc([u for u,h in zip(U,have) if h],[w for w,h in zip(wrong,have) if h])
    lo,hi=boot([u for u,h in zip(U,have) if h],[w for w,h in zip(wrong,have) if h])
    lb=[x['lat'] for x in b if x['lat']]; lt=[x['lat'] for x in t if x['lat']]
    R[m]=dict(acc_base=sum(cb)/len(cb),acc_treat=sum(ct)/len(ct),same_answer=same,call_errors=errs,
              with_logprobs=sum(have),auroc=a,ci=(lo,hi),lat_med_base=st.median(lb),lat_med_treat=st.median(lt),lat_ratio=st.median(lt)/st.median(lb),U=U,correct=ct)
    print(m,{k:(round(v,3) if isinstance(v,float) else v) for k,v in R[m].items() if k not in('U','correct')})
# routing sim: weak=qwen36-local escalates top-30% U to strong=deepseek-v4-flash
w=R['qwen36-local']; s=R['deepseek-v4-flash']
n=len(rows); k=int(round(.3*n))
order=sorted([i for i in range(n) if w['U'][i] is not None],key=lambda i:-w['U'][i])
esc=set(order[:k])
routed=sum((s['correct'][i] if i in esc else w['correct'][i]) for i in range(n))/n
rand_exp=0.7*w['acc_treat']+0.3*s['acc_treat']
ora=sorted(range(n),key=lambda i:(w['correct'][i],-0))  # oracle: escalate the wrong ones first
oesc=set(i for i in range(n) if not w['correct'][i]); oesc=set(list(oesc)[:k])
oracle=sum((s['correct'][i] if i in oesc else w['correct'][i]) for i in range(n))/n
print('ROUTING @30%% escalation: weak_only=%.3f strong_only=%.3f uncertainty_routed=%.3f random_expected=%.3f oracle=%.3f gain_vs_random=%+.3f pts'%(w['acc_treat'],s['acc_treat'],routed,rand_exp,oracle,(routed-rand_exp)*100))
json.dump({'per_model':{m:{k:v for k,v in R[m].items() if k not in('U','correct')} for m in R},'routing':dict(weak_only=w['acc_treat'],strong_only=s['acc_treat'],routed=routed,random_expected=rand_exp,oracle=oracle,gain_pts=(routed-rand_exp)*100)},open(D+'/results.json','w'),indent=1)
