import ast, json, os, statistics
S="/tmp/orch-burn-B-3f4634/lsp"; C=S+"/corpus"
R=json.load(open(S+"/results.json")); P=json.load(open(S+"/results_primary.json"))
assert R["summary"]["median_C"]==P["summary"]["median_C"] and R["summary"]["mean_recall"]==P["summary"]["mean_recall"], "primary numbers moved"
src={f:open(f"{C}/{f}",errors="replace").read() for f in sorted(os.listdir(C)) if f.endswith(".py")}
names={}; imps={}; attrs={}
for f,s in src.items():
    try: t=ast.parse(s)
    except SyntaxError: continue
    for n in ast.walk(t):
        if isinstance(n,ast.Name) and isinstance(n.ctx,ast.Load): names.setdefault(n.id,set()).add((f,n.lineno))
        elif isinstance(n,ast.Attribute) and isinstance(n.ctx,ast.Load): attrs.setdefault(n.attr,set()).add((f,n.end_lineno))
        elif isinstance(n,ast.ImportFrom):
            for a in n.names: imps.setdefault(a.name,set()).add((f,n.lineno))
sub=[];amb=[]
for r in R["rows"]:
    nm=r["name"]; got={tuple(x) for x in r["got_sites"]}
    if attrs.get(nm): amb.append(nm); continue
    truth=(names.get(nm,set())|imps.get(nm,set()))
    # drop the def line itself is not in Name loads; fine
    rec=len(truth&got)/len(truth) if truth else 1.0; prec=len(truth&got)/len(got) if got else 1.0
    sub.append((nm,rec,prec,r["def_ok"]))
print("subset without attribute uses:",len(sub),"of",len(R["rows"]),"; ambiguous (name also used as .attr):",len(amb))
print("mean recall",round(statistics.mean(x[1] for x in sub),3),"mean precision",round(statistics.mean(x[2] for x in sub),3),"def_ok",sum(x[3] for x in sub),"/",len(sub))
print("ambiguous names:",amb)
for x in sub:
    if x[1]<1 or x[2]<1: print("  miss:",x)
