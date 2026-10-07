#!/usr/bin/env python3
"""skillhex.py — SkillHEX-style hypothesis loop over the agent5 pipeline.
(Minerva proposal 5ccfeb0adf86, wq#461, Scribe #322. Method: SkillHEX,
arXiv cs.AI 2026-08 — hypothesis-driven self-verification + evidence-guided
search over persistent skill-revision branches.)

What exists already: DGM v2 (dgm.py/dgm_v2.py) mutates ONE prompt from the
ACTIVE config with only a coarse benchmark summary, paired-evals it, and
human-gates promotion. SkillHEX's delta, implemented here:
 1. EVIDENCE HARVEST — hypotheses are grounded in real failures: recent dead-
    queue and review-queue feedback (db5), summarized into the proposer prompt
    so each mutation is a falsifiable "this failure class is caused by X in
    the prompt" hypothesis rather than a blind rewrite.
 2. PERSISTENT BRANCH BANK — every candidate (incl. discarded ones) is stored
    in minerva.db skillhex_results with lineage + scores.
 3. EXPLORE vs EXPLOIT — escapes the paper's "exploitation trap": with prob
    1-EXPLOIT_P the next mutation branches from the best NEAR-MISS prior
    candidate (positive mean, not promoted) instead of the incumbent.

Reuses DGM machinery wholesale: DV2.propose_prompt_mutation (proposer),
DV2.paired_eval (executable test / dense reward), dgm.py pending+ntfy
human gate. NEVER self-promotes; at most writes reg["pending"].
Usage: skillhex.py [--propose-only] [--n N] [--target KEY]
Timer: lumen-skillhex.{service,timer} installed DISABLED (Frank-gated).
"""
import argparse, copy, datetime, json, random, sqlite3, subprocess, sys

sys.path.insert(0, "/home/lumen/autonomy/agent5")
import a5_common as C
import benchmark_v2 as BV2
import dgm as DGM
import dgm_v2 as DV2

MINERVA_DB = "/home/lumen/minerva/minerva.db"
EXPLOIT_P = 0.6
EVIDENCE_K = 15
SEED = None  # time-seeded: exploration should differ across runs

def db_init():
    c = sqlite3.connect(MINERVA_DB)
    c.execute("""CREATE TABLE IF NOT EXISTS skillhex_results(
      id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,
      target TEXT, parent TEXT, branch_kind TEXT,   -- incumbent | near-miss
      hypothesis TEXT, config_json TEXT,
      tested INT DEFAULT 0, mean REAL, ci_low REAL, ci_high REAL,
      p_better REAL, guard_fail TEXT, decision TEXT, pending_version TEXT)""")
    c.commit()
    return c

def harvest_evidence(k=EVIDENCE_K):
    """Recent failure feedback from db5 dead + review queues."""
    r = C.get_redis()
    ev = []
    for q in (C.Q_DEAD, C.Q_REVIEW):
        for raw in r.lrange(q, 0, k - 1):
            try:
                e = json.loads(raw)
            except Exception:
                continue
            fb = (e.get("feedback") or "").strip()
            if fb:
                ev.append(f"[{q.split(':')[-1]}] {fb[:220]}")
    return ev[: 2 * k]

def record_llm_unavailable(target):
    """wq#3594: no configured model answered the proposer (e.g. deepseek-v4-pro and
    deepseek-v4-flash both 402 Insufficient Balance, wq#3410). File a deduped aq row
    (aq upserts on open source+key) instead of failing the systemd unit -- the per-model
    errors are already printed by dgm_v2.propose_prompt_mutation above, for the journal."""
    try:
        subprocess.run(
            ["python3", "/home/lumen/bin/aq", "add", "skillhex", "llm-unavailable",
             "skillhex proposer: no configured model answered",
             f"target={target} tried=deepseek-v4-pro,cerebras-llama,deepseek-v4-flash"],
            timeout=15, check=False)
    except Exception as e:
        print(f"[skillhex] aq add failed: {e}", flush=True)

def pick_branch(c, active_cfg, target):
    """Exploit incumbent, or explore best near-miss branch (SkillHEX escape
    from the exploitation trap). Returns (cfg, parent_label, kind)."""
    if random.random() < EXPLOIT_P:
        return active_cfg, "active", "incumbent"
    row = c.execute(
        "SELECT id, config_json, mean FROM skillhex_results WHERE tested=1 "
        "AND decision='DISCARD' AND mean > 0 AND target=? "
        "ORDER BY mean DESC LIMIT 1", (target,)).fetchone()
    if row:
        try:
            return json.loads(row[1]), f"skillhex#{row[0]}", "near-miss"
        except Exception:
            pass
    return active_cfg, "active", "incumbent"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--propose-only", action="store_true",
                    help="harvest+propose+record, skip paired eval (no bench cost)")
    ap.add_argument("--n", type=int, default=6, help="benchmark items for eval")
    ap.add_argument("--target", default="generator_sys")
    args = ap.parse_args()
    random.seed(SEED)

    c = db_init()
    reg = DGM.load_reg()
    active = copy.deepcopy(DGM.active_cfg(reg))
    branch_cfg, parent, kind = pick_branch(c, active, args.target)

    evidence = harvest_evidence()
    ev_txt = "\n".join(evidence) or "(no recent failure feedback found)"
    summary = (
        "RECENT FAILURE EVIDENCE from the live pipeline (dead/review queues). "
        "Form ONE falsifiable hypothesis about which prompt weakness causes "
        "the dominant failure class, and rewrite the prompt to fix exactly "
        "that. Do not regress other behaviors.\n" + ev_txt)
    print(f"[skillhex] branch={kind} parent={parent} "
          f"evidence_items={len(evidence)}", flush=True)

    prop = DV2.propose_prompt_mutation(branch_cfg, args.target, summary)
    if not prop:
        print("[skillhex] proposer failed; no cycle (no model answered -- "
              "see per-model errors above); recording aq row, not failing unit", flush=True)
        record_llm_unavailable(args.target)
        return 0
    ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
    cur = c.execute(
        "INSERT INTO skillhex_results(ts,target,parent,branch_kind,hypothesis,"
        "config_json) VALUES(?,?,?,?,?,?)",
        (ts, args.target, parent, kind, prop["rationale"][:500],
         json.dumps(prop["candidate"])))
    rid = cur.lastrowid
    c.commit()
    print(f"[skillhex] hypothesis #{rid}: {prop['rationale'][:160]}", flush=True)

    if args.propose_only:
        print(f"[skillhex] propose-only: recorded untested branch #{rid}")
        return 0

    items = BV2.BENCHMARK_PROMPTS[: args.n]
    res = DV2.paired_eval(active, prop["candidate"], items, progress=True)
    boot, decision = res["boot"], res["decision"]
    pending_ver = None
    if decision == "PROMOTE":
        ver = "shx-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        reg = DGM.load_reg()  # re-read: eval takes a while
        reg["versions"][ver] = {
            "created": DGM.now(), "author": "skillhex",
            "note": f"{args.target} [{kind}] {prop['rationale'][:150]}",
            "config": prop["candidate"], "parent": reg["active_version"]}
        reg["pending"] = {
            "version": ver, "config": prop["candidate"],
            "note": f"skillhex {args.target} hypothesis #{rid}",
            "score_delta": boot.get("mean"), "proposed": DGM.now(),
            "baseline_version": reg["active_version"]}
        DGM.save_reg(reg)
        pending_ver = ver
        DGM.ntfy("SkillHEX candidate awaiting approval",
                 f"{ver} ({kind} branch)\n{prop['rationale'][:160]}\n"
                 f"mean {boot.get('mean')} ci[{boot.get('ci_low')},"
                 f"{boot.get('ci_high')}]\napprove: dgm.py approve {ver}",
                 "high", "robot,skillhex")
    c.execute(
        "UPDATE skillhex_results SET tested=1, mean=?, ci_low=?, ci_high=?, "
        "p_better=?, guard_fail=?, decision=?, pending_version=? WHERE id=?",
        (boot.get("mean"), boot.get("ci_low"), boot.get("ci_high"),
         boot.get("p_better"), ",".join(res["guard_fail"]), decision,
         pending_ver, rid))
    c.commit()
    print(f"[skillhex] #{rid} {decision} mean={boot.get('mean')} "
          f"pending={pending_ver}", flush=True)
    return 0

if __name__ == "__main__":
    sys.exit(main())
