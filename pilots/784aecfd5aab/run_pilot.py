import ast, hashlib, json, os, subprocess, sys, time, statistics, re
import tiktoken
S="/tmp/orch-burn-B-3f4634/lsp"; C=S+"/corpus"
enc=tiktoken.get_encoding("cl100k_base")
tok=lambda s: len(enc.encode(s))
files=sorted(f for f in os.listdir(C) if f.endswith(".py"))
src={f:open(f"{C}/{f}",errors="replace").read() for f in files}
digest=hashlib.sha256("".join(hashlib.sha256(src[f].encode()).hexdigest() for f in files).encode()).hexdigest()
print("files",len(files),"bytes",sum(len(v.encode()) for v in src.values()),"sha256",digest,flush=True)
defs={}; uses={}; trees={}
for f in files:
    try: t=ast.parse(src[f])
    except SyntaxError: continue
    trees[f]=t
    for n in t.body:
        if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)): defs.setdefault(n.name,[]).append((f,n))
    for n in ast.walk(t):
        if isinstance(n,ast.Name) and isinstance(n.ctx,ast.Load): uses.setdefault(n.id,[]).append((f,n.lineno,n.col_offset))
        elif isinstance(n,ast.Attribute) and isinstance(n.ctx,ast.Load): uses.setdefault(n.attr,[]).append((f,n.end_lineno,n.end_col_offset-len(n.attr)))
cand=[]
for name,d in defs.items():
    if len(d)!=1 or name.startswith("__") or len(name)<4: continue
    df,dn=d[0]
    u=[x for x in uses.get(name,[]) if not (x[0]==df and x[1]==dn.lineno)]
    if len(u)>=2: cand.append((hashlib.sha256(name.encode()).hexdigest(),name,df,dn,sorted(set(u))))
cand.sort(); sample=cand[:30]
print("candidates",len(cand),"sampled",len(sample),flush=True)
# LSP client
p=subprocess.Popen([S+"/npm/node_modules/.bin/pyright-langserver","--stdio"],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,cwd=C)
_id=0
def send(o):
    b=json.dumps(o).encode(); p.stdin.write(b"Content-Length: %d\r\n\r\n"%len(b)+b); p.stdin.flush()
def read():
    h={}
    while True:
        l=p.stdout.readline()
        if not l: raise EOFError
        l=l.strip()
        if not l: break
        k,v=l.split(b":",1); h[k.lower()]=int(v)
    return json.loads(p.stdout.read(h[b"content-length"]))
def req(m,params,timeout=120):
    global _id; _id+=1; me=_id; send({"jsonrpc":"2.0","id":me,"method":m,"params":params}); t0=time.time()
    while True:
        r=read()
        if "method" in r and "id" in r:   # server->client request
            res=[{} for _ in r.get("params",{}).get("items",[])] if r["method"]=="workspace/configuration" else None
            send({"jsonrpc":"2.0","id":r["id"],"result":res}); continue
        if r.get("id")==me: return r.get("result"),time.time()-t0
        if time.time()-t0>timeout: raise TimeoutError(m)
uri=lambda f:"file://"+C+"/"+f
t0=time.time()
res,_=req("initialize",{"processId":os.getpid(),"rootUri":"file://"+C,"workspaceFolders":[{"uri":"file://"+C,"name":"c"}],
 "capabilities":{"workspace":{"configuration":True},"textDocument":{"hover":{"contentFormat":["markdown","plaintext"]},"definition":{},"references":{}}}})
send({"jsonrpc":"2.0","method":"initialized","params":{}})
opened=set()
def openf(f):
    if f in opened: return
    send({"jsonrpc":"2.0","method":"textDocument/didOpen","params":{"textDocument":{"uri":uri(f),"languageId":"python","version":1,"text":src[f]}}}); opened.add(f)
def pos_def(dn,f):
    line=src[f].split("\n")[dn.lineno-1]; return dn.lineno-1,line.index(dn.name,dn.col_offset)
rows=[]; lat=[]
for i,(h,name,df,dn,u) in enumerate(sample):
    uf,ul,uc=u[0]
    # arms A,B
    afiles={df}|{x[0] for x in u}
    A=tok("\n".join(src[f] for f in sorted(afiles)))
    out=[]
    for f in files:
        L=src[f].split("\n"); hit=[k for k,l in enumerate(L) if re.search(r"\b%s\b"%re.escape(name),l)]
        if not hit: continue
        keep=set()
        for k in hit: keep.update(range(max(0,k-3),min(len(L),k+4)))
        prev=None
        for k in sorted(keep):
            if prev is not None and k!=prev+1: out.append("--")
            out.append(f"{f}{':' if k in hit else '-'}{k+1}{':' if k in hit else '-'}{L[k]}"); prev=k
    B=tok("\n".join(out))
    # arm C
    openf(uf); openf(df); tt=time.time()
    d,l1=req("textDocument/definition",{"textDocument":{"uri":uri(uf)},"position":{"line":ul-1,"character":uc}})
    hv,l2=req("textDocument/hover",{"textDocument":{"uri":uri(uf)},"position":{"line":ul-1,"character":uc}})
    dl,dc=pos_def(dn,df)
    rf,l3=req("textDocument/references",{"textDocument":{"uri":uri(df)},"position":{"line":dl,"character":dc},"context":{"includeDeclaration":False}})
    lat.append(time.time()-tt)
    d=d if isinstance(d,list) else ([d] if d else [])
    dloc=[(x.get("uri") or x.get("targetUri"),(x.get("range") or x.get("targetSelectionRange"))["start"]["line"]+1) for x in d]
    hv_txt=(hv or {}).get("contents",{}); hv_txt=hv_txt.get("value","") if isinstance(hv_txt,dict) else str(hv_txt)
    refs=sorted({(x["uri"].replace("file://"+C+"/",""),x["range"]["start"]["line"]+1) for x in (rf or [])})
    txt=hv_txt+"\n"+"\n".join(f"defined: {a.replace('file://'+C+'/','')}:{b}" for a,b in dloc)+"\n"+"\n".join(f"{a}:{b}: {src[a].split(chr(10))[b-1].strip()}" for a,b in refs if a in src)
    Ct=tok(txt)
    truth={(x[0],x[1]) for x in u}; got=set(refs)
    rec=len(truth&got)/len(truth); prec=len(truth&got)/len(got) if got else 0.0
    defok=any(a.endswith("/"+df) and b==dn.lineno for a,b in dloc)
    rows.append(dict(got_sites=[list(x) for x in sorted(got)],name=name,A=A,B=B,C=Ct,recall=rec,precision=prec,def_ok=defok,truth=len(truth),got=len(got)))
    print(f"{i+1:2d} {name[:34]:34s} A={A:6d} B={B:5d} C={Ct:5d} rec={rec:.2f} prec={prec:.2f} def={'Y' if defok else 'N'}",flush=True)
send({"jsonrpc":"2.0","method":"exit"}); p.kill()
mA,mB,mC=(statistics.median(r[k] for r in rows) for k in "ABC")
rec=statistics.mean(r["recall"] for r in rows); prec=statistics.mean(r["precision"] for r in rows)
res=dict(files=len(files),sha256=digest,n=len(rows),median_A=mA,median_B=mB,median_C=mC,ratio_C_over_B=mC/mB,ratio_C_over_A=mC/mA,
 mean_recall=rec,mean_precision=prec,def_ok=sum(r["def_ok"] for r in rows),median_lsp_seconds_per_question=statistics.median(lat),total_seconds=time.time()-t0)
json.dump(dict(summary=res,rows=rows),open(S+"/results.json","w"),indent=1)
print(json.dumps(res,indent=1))
