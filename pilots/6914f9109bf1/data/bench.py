#!/usr/bin/env python3
# wq#2447 client. usage: bench.py PORT OUTFILE  (prompts.jsonl beside it). greedy, seed 1, 160 new tokens, cache off.
import json,sys,time,urllib.request,os
port,out=sys.argv[1],sys.argv[2]
rows=[json.loads(l) for l in open(os.path.join(os.path.dirname(os.path.abspath(__file__)),'prompts.jsonl'))]
f=open(out,'w')
for r in rows:
    body={'messages':[{'role':'user','content':r['prompt']}],'max_tokens':160,'temperature':0,'seed':1,'cache_prompt':False}
    t=time.time()
    j=json.load(urllib.request.urlopen(urllib.request.Request(f'http://127.0.0.1:{port}/v1/chat/completions',json.dumps(body).encode(),{'Content-Type':'application/json'}),timeout=900))
    tm=j.get('timings',{})
    f.write(json.dumps({'id':r['id'],'kind':r['kind'],'wall':time.time()-t,'text':j['choices'][0]['message'].get('content') or '','reasoning':j['choices'][0]['message'].get('reasoning_content') or '',
        'prompt_n':tm.get('prompt_n'),'prompt_tps':tm.get('prompt_per_second'),'pred_n':tm.get('predicted_n'),'pred_tps':tm.get('predicted_per_second'),
        'draft_n':tm.get('draft_n'),'draft_acc':tm.get('draft_n_accepted')})+'\n'); f.flush()
f.close()
