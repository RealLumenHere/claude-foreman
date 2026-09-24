import numpy as np, time, json, resource, sys, hashlib
from sklearn.cluster import KMeans
from scipy.spatial.distance import cdist
import ot
S="/tmp/orch-burn-B-3f4634/gw"
d=np.load(S+"/extract.npz"); X=d["X"].astype(np.float64); nz=np.linalg.norm(X,axis=1)>0; print("dropped zero-norm rows:",int((~nz).sum())); X=X[nz]; X/=np.linalg.norm(X,axis=1,keepdims=True)
print("extract sha256",hashlib.sha256(d["X"].tobytes()).hexdigest(),X.shape,flush=True)
K=64; NI=4000; NQ=500; PS=(1,2,4)
def cents(Xi,assign):
    C=np.zeros((K,Xi.shape[1]))
    for j in range(K):
        m=assign==j
        if m.any(): C[j]=Xi[m].mean(0)
    return C/(np.linalg.norm(C,axis=1,keepdims=True)+1e-12)
def ivf(Xi,assign,Q,gt):
    C=cents(Xi,assign); order=np.argsort(-(Q@C.T),axis=1); out={}
    for p in PS:
        hits=0; scanned=0
        for qi in range(len(Q)):
            idx=np.where(np.isin(assign,order[qi,:p]))[0]; scanned+=len(idx)
            if len(idx): hits+=len(set(idx[np.argsort(-(Xi[idx]@Q[qi]))[:10]])&set(gt[qi]))
        out[p]=(hits/(10*len(Q)), scanned/(len(Q)*len(Xi)))
    return out
rows=[]
for s in range(5):
    rng=np.random.default_rng(s); perm=rng.permutation(len(X)); ii=perm[:NI]; qi=perm[NI:NI+NQ]
    Xi=X[ii]; Q=X[qi]; gt=np.argsort(-(Q@Xi.T),axis=1)[:,:10]
    t=time.time(); km=KMeans(K,n_init=10,random_state=s).fit(Xi); tk=time.time()-t
    rk=ivf(Xi,km.labels_,Q,gt)
    print(f"seed {s} kmeans fit {tk:.1f}s  "+"  ".join(f"p{p}: R={rk[p][0]:.3f} scan={rk[p][1]:.3f}" for p in PS),flush=True)
    t=time.time()
    C1=np.sqrt(np.maximum(2-2*Xi@Xi.T,0)); pn=np.ones(NI)/NI
    q=np.bincount(km.labels_,minlength=K)/NI; C2=cdist(km.cluster_centers_,km.cluster_centers_)
    G0=np.zeros((NI,K)); G0[np.arange(NI),km.labels_]=1/NI
    T=G0
    for it in range(5):
        T=ot.gromov.gromov_wasserstein(C1,C2,pn,q,"square_loss",G0=T,max_iter=200,tol_rel=1e-7,tol_abs=1e-7)
        C2=(T.T@C1@T)/np.outer(q,q)
    lab=T.argmax(1); tg=time.time()-t
    rg=ivf(Xi,lab,Q,gt)
    moved=float((lab!=km.labels_).mean())
    print(f"seed {s} GW      fit {tg:.1f}s  "+"  ".join(f"p{p}: R={rg[p][0]:.3f} scan={rg[p][1]:.3f}" for p in PS)+f"  moved={moved:.3f} empty={int((np.bincount(lab,minlength=K)==0).sum())}",flush=True)
    rows.append(dict(seed=s,kmeans={p:rk[p] for p in PS},gw={p:rg[p] for p in PS},t_kmeans=tk,t_gw=tg,moved=moved,peak_rss_mb=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024))
    json.dump(rows,open(S+"/results.json","w"),indent=1,default=float)
d2=[r["gw"][2][0]-r["kmeans"][2][0] for r in rows]
sc=np.mean([r["gw"][2][1] for r in rows])/np.mean([r["kmeans"][2][1] for r in rows])
print("SUMMARY p=2 recall diff (gw-kmeans) per seed",[round(x,4) for x in d2],"mean",round(float(np.mean(d2)),4),"gw ahead",int(sum(x>0 for x in d2)),"/5 scan ratio",round(float(sc),3),"max fit s",round(max(r["t_gw"] for r in rows),1),"peak MB",round(max(r["peak_rss_mb"] for r in rows)))
