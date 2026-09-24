#!/usr/bin/env python3
"""Pilot 0a0c3351a80e (wq#1939): is there learnable signal in past proposal records?

Runs on Lumen: minerva.db is never copied to another node (card, Data handling). The
extract holds only id, created_at, status, title, proposed_change, impact, effort, risk,
eval_result and stays in scratch; this script and results.json are what gets committed.

  python3 run_pilot.py build     # writes the pinned sample, prints its sha256
  python3 run_pilot.py run       # baseline arms first, then the treatment, same sample
"""
import hashlib, json, math, re, sqlite3, sys
from collections import Counter
import numpy as np

DB = "/home/lumen/minerva/minerva.db"
SAMPLE = "/tmp/orch-burn-2-4611f5/pilot_0a0c_sample.json"
RESULTS = "/home/lumen/foreman/pilots/0a0c3351a80e/results.json"
POS = ("approved", "staged", "planned", "applied_unverified", "deployed", "shipped")
NEG = ("reject",)      # rejected_aged is NOT a judgement: it aged out, so it is excluded
TRAIN_FRAC = 0.7
SEED = 0


def build():
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    q = ("select id, created_at, status, title, proposed_change, impact, effort, risk, eval_result "
         "from proposals where status in (%s) and created_at is not null order by created_at, id"
         % ",".join("?" * (len(POS) + len(NEG))))
    rows = []
    for r in c.execute(q, POS + NEG):
        fs = None
        try:
            fs = json.loads(r[8]).get("final_score") if r[8] else None
        except Exception:
            pass
        rows.append({"id": r[0], "t": r[1], "y": 1 if r[2] in POS else 0,
                     "text": f"{r[3] or ''} {r[4] or ''}", "impact": r[5], "effort": r[6],
                     "risk": r[7], "final_score": fs})
    blob = json.dumps(rows, sort_keys=True).encode()
    json.dump(rows, open(SAMPLE, "w"))
    print(f"rows={len(rows)} pos={sum(r['y'] for r in rows)} sha256={hashlib.sha256(blob).hexdigest()}")


def auc(y, s):
    y = np.asarray(y); s = np.asarray(s, dtype=float)
    pos, neg = s[y == 1], s[y == 0]
    if len(pos) == 0 or len(neg) == 0:
        return float("nan")
    order = np.argsort(np.concatenate([pos, neg]), kind="mergesort")
    ranks = np.empty(len(order)); allv = np.concatenate([pos, neg])[order]
    i = 0
    while i < len(allv):
        j = i
        while j + 1 < len(allv) and allv[j + 1] == allv[i]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2 + 1
        i = j + 1
    return float((ranks[:len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


TOK = re.compile(r"[a-z][a-z0-9\-]{2,}")


def tfidf(train_text, test_text, max_terms=3000, min_df=3):
    toks = [set(TOK.findall(t.lower())) for t in train_text]
    df = Counter(w for s in toks for w in s)
    vocab = [w for w, n in df.most_common(max_terms) if n >= min_df]
    idx = {w: i for i, w in enumerate(vocab)}
    idf = np.array([math.log((1 + len(train_text)) / (1 + df[w])) + 1 for w in vocab])

    def mat(texts):
        m = np.zeros((len(texts), len(vocab)))
        for r, t in enumerate(texts):
            for w, n in Counter(TOK.findall(t.lower())).items():
                if w in idx:
                    m[r, idx[w]] = 1 + math.log(n)
        m *= idf
        return m / (np.linalg.norm(m, axis=1, keepdims=True) + 1e-9)
    return mat(train_text), mat(test_text), vocab


def logreg(X, y, l2=1.0, iters=400, lr=0.5):
    w = np.zeros(X.shape[1]); b = 0.0
    wpos = 0.5 * len(y) / max(1, y.sum()); wneg = 0.5 * len(y) / max(1, (1 - y).sum())
    sw = np.where(y == 1, wpos, wneg)
    for _ in range(iters):
        p = 1 / (1 + np.exp(-(X @ w + b)))
        g = (p - y) * sw
        w -= lr * (X.T @ g / len(y) + l2 * w / len(y)); b -= lr * g.mean()
    return w, b


RANK = {"H": 3, "M": 2, "L": 1, "S": 1}


def static_rubric(r):
    """What a record carries today with no learning: impact up, effort and risk down."""
    imp = RANK.get(str(r["impact"]).upper()[:1], 0)
    eff = {"S": 1, "M": 2, "L": 3}.get(str(r["effort"]).upper()[:1], 2)
    rsk = {"S": 1, "M": 2, "L": 3}.get(str(r["risk"]).upper()[:1], 2)
    return imp - 0.5 * eff - 0.5 * rsk


def fold(rows, lo, hi_train, hi_test):
    train, test = rows[lo:hi_train], rows[hi_train:hi_test]
    ytr = np.array([r["y"] for r in train]); yte = np.array([r["y"] for r in test])
    out = {"n_train": len(train), "n_test": len(test), "pos_test": int(yte.sum()),
           "base_rate_test": round(float(yte.mean()), 3)}
    out["baseline_static_rubric_auc"] = round(auc(yte, [static_rubric(r) for r in test]), 3)
    fs = [r["final_score"] for r in test]
    out["baseline_eval_final_score_auc"] = (round(auc(yte, [0 if f is None else f for f in fs]), 3)
                                             if any(f is not None for f in fs) else None)
    Xtr, Xte, vocab = tfidf([r["text"] for r in train], [r["text"] for r in test])
    w, b = logreg(Xtr, ytr)
    score = Xte @ w + b
    out["treatment_text_pattern_auc"] = round(auc(yte, score), 3)
    rng = np.random.default_rng(SEED); perm = []
    for _ in range(200):
        Xp, _, _ = tfidf([r["text"] for r in train], [r["text"] for r in test])
        wp, bp = logreg(Xp, rng.permutation(ytr), iters=150)
        perm.append(auc(yte, Xte @ wp + bp))
    out["perm_p_value"] = round(float((np.sum(np.array(perm) >= out["treatment_text_pattern_auc"]) + 1) / 201), 3)
    top = np.argsort(w)[::-1][:8]
    out["top_positive_terms"] = [vocab[i] for i in top]
    return out


def run():
    rows = json.load(open(SAMPLE))
    n = len(rows); cut = int(n * TRAIN_FRAC)
    res = {"n": n, "pos": sum(r["y"] for r in rows), "positive_statuses": POS, "negative_statuses": NEG,
           "primary_holdout": fold(rows, 0, cut, n)}
    # rolling-origin folds: three later windows, each trained only on what came before it
    res["rolling"] = [fold(rows, 0, int(n * a), int(n * (a + 0.15))) for a in (0.55, 0.70, 0.85)]
    json.dump(res, open(RESULTS, "w"), indent=2)
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    {"build": build, "run": run}[sys.argv[1]]()
