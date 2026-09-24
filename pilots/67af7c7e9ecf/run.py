#!/usr/bin/env python3
# wq#2446 pilot 67af7c7e9ecf. usage: run.py baseline|treatment. Client only; LiteLLM gateway on Lumen. Sequential (1 worker) for latency comparability.
import json,sys,time,urllib.request,os
arm=sys.argv[1]; assert arm in('baseline','treatment')
D='/home/lumen/foreman/pilots/67af7c7e9ecf/data'
key=[l.split('=',1)[1].strip().strip('"\'') for l in open('/home/lumen/agent/.env') if l.startswith('LITELLM_MASTER_KEY=')][0]
H={'Authorization':'Bearer '+key,'Content-Type':'application/json'}
MODELS=['qwen36-local','deepseek-v4-flash']
rows=[json.loads(l) for l in open(D+'/sample.jsonl')]
def call(model,q):
    body={'model':model,'messages':[{'role':'system','content':'Answer with only the answer, no explanation.'},{'role':'user','content':q}],'max_tokens':24,'temperature':0}
    if arm=='treatment': body.update(logprobs=True,top_logprobs=5)
    for attempt in range(3):
        t=time.time()
        try:
            j=json.load(urllib.request.urlopen(urllib.request.Request('http://localhost:4000/v1/chat/completions',json.dumps(body).encode(),H),timeout=120))
            dt=time.time()-t; ch=j['choices'][0]
            lp=[x['logprob'] for x in ((ch.get('logprobs') or {}).get('content') or [])]
            return {'ans':(ch['message'].get('content') or '').strip(),'lat':dt,'lp':lp,'err':None}
        except Exception as e:
            err=str(e)[:120]; time.sleep(2)
    return {'ans':'','lat':None,'lp':[],'err':err}
for m in MODELS:
    out=open(f'{D}/{arm}_{m}.jsonl','w')
    for i,r in enumerate(rows):
        x=call(m,r['q']); x['id']=r['id']; out.write(json.dumps(x)+'\n'); out.flush()
    out.close(); print(arm,m,'done',flush=True)
