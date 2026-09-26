#!/usr/bin/env python3
# wq#1789 pilot 992fcdd0fdfc driver. usage: run.py baseline|treatment
# baseline: every row answered by STRONG_MODEL alone (today's behavior).
# treatment: conformal_cascade.route() per row (weak-first, calibrated escalation).
import json, sys, os
sys.path.insert(0, os.path.dirname(__file__))
import conformal_cascade as cc

D = os.path.join(os.path.dirname(__file__), "data")


def main(arm):
    key = cc._load_key()
    limit = int(os.environ.get("PILOT_LIMIT", "15")) or None
    rows = [json.loads(l) for l in open(os.path.join(D, "sample.jsonl"))][:limit]
    calib_rows = [json.loads(l) for l in open(os.path.join(D, "calib.jsonl"))][:limit]
    out_path = os.path.join(D, f"{arm}.jsonl")
    out = open(out_path, "w")
    if arm == "baseline":
        for r in rows:
            res = cc.call_model(cc.STRONG_MODEL, r["q"], key)
            correct = cc._is_correct(res["answer"], r["aliases"])
            out.write(json.dumps({"id": r["id"], "model_used": cc.STRONG_MODEL,
                                   "answer": res["answer"], "latency": res["latency"],
                                   "correct": correct, "escalated": False}) + "\n")
            out.flush()
    else:
        threshold = cc.calibrate(calib_rows, key)
        for r in rows:
            res = cc.route(r["q"], key, threshold)
            correct = cc._is_correct(res["answer"], r["aliases"])
            res["id"] = r["id"]
            res["correct"] = correct
            out.write(json.dumps(res) + "\n")
            out.flush()
    out.close()
    print(arm, "done ->", out_path)


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in ("baseline", "treatment"):
        print("usage: run.py baseline|treatment", file=sys.stderr)
        sys.exit(1)
    main(sys.argv[1])
