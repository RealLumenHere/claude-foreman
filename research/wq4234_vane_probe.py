"""wq#4234 probe: Vane-shaped pipeline (search -> local-LLM cited synthesis) on
the fleet's own code paths, so answer quality can be judged without deploying Vane.
Prints timings, tier, answer length, and the answer; never prints the key."""
import sys
import time

sys.path.insert(0, "/home/lumen/foreman/research")
import search_tier  # noqa: E402
import trigger  # noqa: E402

MODEL = sys.argv[1] if len(sys.argv) > 1 else "deepseek-v4-flash"
Q = sys.argv[2] if len(sys.argv) > 2 else "What is the default context window Ollama loads a model with, and how do you raise it?"

SYS = ("You are an answer engine. Answer the question using ONLY the numbered sources. "
       "Cite every claim as [n]. If the sources do not answer it, say so. Under 150 words.")

t0 = time.time()
results, tier = search_tier.tiered_search(Q, 6)
t1 = time.time()
src = "\n".join(f"[{i}] {r.get('title','')} {r.get('url','')}\n{(r.get('snippet') or '')[:700]}"
                for i, r in enumerate(results, 1))
print(f"search tier={tier} n={len(results)} {t1 - t0:.1f}s prompt_chars={len(src)}")
ans = trigger.gateway_chat(MODEL, SYS, f"Question: {Q}\n\nSources:\n{src}", timeout=240)
t2 = time.time()
print(f"model={MODEL} synth={t2 - t1:.1f}s answer_chars={len(ans or '')} none={ans is None}")
print((ans or "")[:1400])
