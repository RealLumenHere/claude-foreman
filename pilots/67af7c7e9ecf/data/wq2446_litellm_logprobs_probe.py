import json,time,urllib.request,concurrent.futures as cf
key=[l.split('=',1)[1].strip().strip('"\'') for l in open('/home/lumen/agent/.env') if l.startswith('LITELLM_MASTER_KEY=')][0]
H={'Authorization':'Bearer '+key,'Content-Type':'application/json'}
skip=('claude','gpt-3.5','bge','embed','vision')
d=json.load(urllib.request.urlopen(urllib.request.Request('http://localhost:4000/v1/models',headers=H),timeout=20))
ids=[m['id'] for m in d['data'] if not any(s in m['id'] for s in skip)]
def probe(m):
    body={'model':m,'messages':[{'role':'user','content':'Reply with one word: what is the capital of France?'}],'max_tokens':8,'temperature':0,'logprobs':True,'top_logprobs':5}
    t=time.time()
    try:
        r=urllib.request.urlopen(urllib.request.Request('http://localhost:4000/v1/chat/completions',json.dumps(body).encode(),H),timeout=100)
        j=json.load(r); dt=time.time()-t
        ch=j['choices'][0]; lp=ch.get('logprobs')
        c=(lp or {}).get('content') or []
        top=sum(1 for x in c if x.get('top_logprobs'))
        return (m,'OK',round(dt,2),len(c),top,(ch['message'].get('content') or '')[:20].replace('\n',' '))
    except Exception as e:
        msg=str(e)[:90]
        try: msg=e.read().decode()[:110].replace('\n',' ')
        except Exception: pass
        return (m,'ERR',round(time.time()-t,2),0,0,msg)
with cf.ThreadPoolExecutor(6) as ex:
    res=list(ex.map(probe,ids))
for r in sorted(res,key=lambda x:x[0]): print(*r,sep=' | ')
