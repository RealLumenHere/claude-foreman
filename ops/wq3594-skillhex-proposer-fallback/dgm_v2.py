#!/usr/bin/env python3
"""DGM v2 engine: LLM-authored prompt mutation (operator a) + paired-bootstrap gate.
Scores a candidate against the live baseline on the SAME benchmark-v2 prompts (paired),
and only proposes promotion when a bootstrap CI on the per-prompt deltas clears the margin.
Promotion stays human-gated (writes pending; never auto-activates) — same contract as dgm.py.
"""
import json, random, copy, statistics, sys
import a5_common as C
import benchmark_v2 as B

PROPOSER_MODEL   = "deepseek-v4-pro"
PROMOTE_MARGIN   = 0.03
BOOT_ITERS       = 10000
MAX_PROMPT_CHARS = 1400
GUARD_SENTENCE = (" When illustrating or proposing hardware, reference only Lumen's current fleet "
                  "or generic commodity components; never present specific decommissioned, legacy, "
                  "or non-fleet equipment (such as particular datacenter GPU or storage model numbers) "
                  "as if Lumen runs it.")
ROLE = {
 "generator_sys":"the GENERATOR: writes the final technical research report the evaluator grades.",
 "planner_sys":  "the PLANNER: turns a research prompt into an explicit plan / acceptance criteria.",
 "evaluator_sys":"the EVALUATOR: judges whether a draft meets the plan and accepts or rejects it.",
}
PROPOSER_SYS = ("You are an expert prompt engineer improving ONE agent's system prompt in an autonomous "
                "research pipeline. Output ONLY a JSON object, no prose, no markdown.")

def _proposer_prompt(target, current, baseline_summary):
    return (f"AGENT: {ROLE.get(target,target)}\n\n"
            "OPTIMISED METRIC: a 0..1 rubric averaging source_quality, integration_specificity, "
            "factual_grounding, criteria_coverage, concision.\n"
            f"CURRENT BASELINE (graded benchmark): {baseline_summary}\n\n"
            f'CURRENT SYSTEM PROMPT:\n\"\"\"\n{current}\n\"\"\"\n\n'
            "Propose ONE improved version of this system prompt. Constraints:\n"
            f"- A single tight system prompt under {MAX_PROMPT_CHARS} characters.\n"
            "- A real, targeted improvement (credible cited sources, concrete integration steps, "
            "directly addressing each acceptance criterion, concision) — do NOT just paraphrase.\n"
            "- Stay general; do NOT overfit to specific questions.\n"
            "- Do NOT instruct the model to expose chain-of-thought.\n"
            "- Preserve any rule about not referencing retired/non-fleet hardware.\n\n"
            'Return ONLY: {"new_prompt":"<full revised system prompt>","rationale":"<one sentence>"}')

def propose_prompt_mutation(cfg, target="generator_sys", baseline_summary="(none)", proposer=PROPOSER_MODEL):
    current = cfg.get(target) or C.cfg(target,"")
    pr = _proposer_prompt(target, current, baseline_summary)
    # wq#3594: "deepseek-v4-flash" is the same deepseek account as the primary
    # "deepseek-v4-pro" proposer, so a 402 Insufficient Balance (wq#3410) took out both —
    # the old fallback never actually fell back to a different vendor. cerebras-llama is
    # the funded, different-account fallback a5_common.MODEL_CHAIN already relies on.
    for mdl in (proposer, "cerebras-llama", "deepseek-v4-flash"):
        txt, _ = C.call_llm(pr, PROPOSER_SYS, "mutate", max_tokens=1800, models=[mdl])
        if not txt:
            print(f"[dgm_v2] propose_prompt_mutation: {mdl} returned no text", flush=True)
            continue
        j = B._parse(txt)
        if j and isinstance(j.get("new_prompt"), str) and len(j["new_prompt"].strip()) >= 80:
            new = j["new_prompt"].strip()[:MAX_PROMPT_CHARS]
            if target == "generator_sys" and "non-fleet" not in new.lower():
                new = new.rstrip() + GUARD_SENTENCE
            cand = copy.deepcopy(cfg); cand[target] = new
            return {"candidate":cand,"target":target,"new_prompt":new,"rationale":j.get("rationale",""),"proposer":mdl}
        print(f"[dgm_v2] propose_prompt_mutation: {mdl} returned unusable JSON: {txt[:200]!r}", flush=True)
    return None

def paired_bootstrap(deltas, iters=BOOT_ITERS, seed=1337):
    n=len(deltas)
    if n==0: return {"mean":None}
    rng=random.Random(seed); means=[]
    for _ in range(iters):
        means.append(sum(deltas[rng.randrange(n)] for _ in range(n))/n)
    means.sort()
    return {"mean":round(statistics.fmean(deltas),4),
            "ci_low":round(means[int(0.025*iters)],4),
            "ci_high":round(means[int(0.975*iters)],4),
            "p_better":round(sum(1 for m in means if m>0)/iters,4),"n":n}

def paired_eval(base_cfg, cand_cfg, items=None, judge=B.JUDGE_MODEL, progress=False):
    items=items or B.BENCHMARK_PROMPTS
    deltas=[]; rows=[]; gfail=[]
    for it in items:
        ba,_=B.generate_answer(it,base_cfg); bg=B.grade_answer(it,ba,judge=judge)
        ca,_=B.generate_answer(it,cand_cfg); cg=B.grade_answer(it,ca,judge=judge)
        if cg.get("guard_fail"): gfail+=cg["guard_fail"]
        if bg["score"] is None or cg["score"] is None: continue
        d=cg["score"]-bg["score"]; deltas.append(d)
        rows.append({"id":it["id"],"base":bg["score"],"cand":cg["score"],"delta":round(d,4)})
        if progress: print(f"  {it['id']:3} base={bg['score']} cand={cg['score']} delta={round(d,4)}",flush=True)
    boot=paired_bootstrap(deltas)
    promote=(not gfail) and boot.get("ci_low",-1)>0 and (boot.get("mean") or 0)>=PROMOTE_MARGIN
    return {"boot":boot,"rows":rows,"guard_fail":sorted(set(gfail)),"decision":"PROMOTE" if promote else "DISCARD"}

if __name__=="__main__":
    cmd=sys.argv[1] if len(sys.argv)>1 else "boottest"
    if cmd=="boottest":
        print("gate unit test (PROMOTE needs ci_low>0 AND mean>=0.03):")
        print("  inert (mean~0, noisy):", paired_bootstrap([0.2,-0.18,0.05,-0.1,0.12,-0.15,0.0,0.08]))
        print("  real (+0.10 shift):   ", paired_bootstrap([0.12,0.08,0.10,0.06,0.14,0.09,0.11,0.07]))
        print("  tiny lucky (mean .02):", paired_bootstrap([0.02,-0.3,0.25,-0.2,0.18,0.05,-0.1,0.12]))
    elif cmd=="propose":
        bs="mean 0.76 (easy .80, medium .86, hard .63); weakest on hard analytical prompts and concision"
        r=propose_prompt_mutation(B.active_config(),"generator_sys",bs)
        if not r: print("proposal FAILED"); sys.exit(1)
        print("RATIONALE:", r["rationale"])
        print("GUARD preserved:", "non-fleet" in r["new_prompt"].lower(), "| len:", len(r["new_prompt"]))
        print(r["new_prompt"])
    elif cmd=="cycle":
        import json as _j
        ids=sys.argv[2].split(",") if len(sys.argv)>2 else None
        items=[x for x in B.BENCHMARK_PROMPTS if (not ids or x["id"] in ids)]
        base=B.active_config()
        bs="mean 0.76 (easy .80, medium .86, hard .63); weakest on hard analytical prompts and concision"
        prop=propose_prompt_mutation(base,"generator_sys",bs)
        if not prop: print("propose failed"); sys.exit(1)
        print("CANDIDATE rationale:", prop["rationale"], flush=True)
        res=paired_eval(base, prop["candidate"], items, progress=True)
        res["candidate_prompt"]=prop["new_prompt"]; res["rationale"]=prop["rationale"]
        print("DECISION:", res["decision"], "| boot:", res["boot"], "| guard_fail:", res["guard_fail"], flush=True)
        _j.dump(res, open(".dgm_v2_cycle.json","w"), indent=2)
        print("wrote .dgm_v2_cycle.json", flush=True)
