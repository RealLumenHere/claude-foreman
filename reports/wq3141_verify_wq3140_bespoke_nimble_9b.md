# wq#3141 — verify wq#3140 Bespoke-Nimble-9B rejection: CONFIRMED

PR http://100.121.17.5:3000/lumen/claude-foreman/pulls/1417 (`0739806b`, matches merged
`beacd3fe` on main — identical 44-line diff, `reports/wq3140_nimble_9b_bakeoff.md` only,
docs-only). Author: claude-opus-5-5. Verdict: **confirm**, no contradiction.

## What I checked

This is a reject-with-nothing-applied ticket, so there's no code diff to break — the
probe is re-deriving each factual claim in the reject report from primary sources rather
than trusting the author's citations.

1. **HF model shape.** `curl https://huggingface.co/api/models/bespokelabs/Bespoke-Nimble-9B`:
   `library_name=peft`, tag `base_model:adapter:Qwen/Qwen3.5-9B`. Sibling list: no
   `.gguf` file; `adapter_model.safetensors` is 173,188,512 bytes = 165.2 MiB, matching
   the report's "~165 MiB". `api/models?search=Bespoke-Nimble` returns exactly two hits,
   `Bespoke-Nimble-9B` and `-v2`, both `library_name=peft` — matches "only `-v2` (also a
   PEFT adapter)". Report claim 1: **confirmed**.
2. **Candidate-logit scorer, not a chat model.** Pulled `inference.py` from the HF repo
   directly. `candidate_logits()` (line 61) takes `logits[:, -1, :]` (last position only),
   `.gather()`s the candidate token ids, masks the rest to `-inf` — exactly "gathers
   last-position logits over candidate token ids and softmaxes them" (softmax happens in
   `decision_result()` at line 73). No generation path exists in the file. Report claim
   2: **confirmed**, and the file docstring self-describes as "Portable CUDA/BF16
   candidate scoring", corroborating independently.
3. **CUDA-BF16 requirement.** `inference.py:105-106`:
   `if not torch.cuda.is_available() or not torch.cuda.is_bf16_supported(): raise
   RuntimeError('This reference runner requires a CUDA GPU with BF16 support.')` — exact
   line match to the report's "`inference.py:105` raises without CUDA-BF16". Report claim
   3: **confirmed** on the CUDA half.
4. **isis dead.** `foreman/_runs/wq3140/snapshots/20260929T114045Z-home_lumen_kb_
   ORCHESTRATOR-UPPER-HAND-fable.md:76`: "Isis dead → no local fallback" — matches the
   report's citation verbatim. **Confirmed.**
5. **Nothing applied.** The wq3140 run's own pre-close snapshot,
   `foreman/_runs/wq3140/snapshots/20260929T114045Z-home_lumen_agent_litellm_config.yaml`,
   has zero matches for `nimble` — the rung was never written even transiently. Matches
   "Nothing was applied. The LiteLLM config and the classifier are untouched." **Confirmed.**
6. **wq3140 run hygiene.** `_runs/wq3140/mask-hits-1.json` shows two kv-secret hits
   during the run, both `suppressed: "pointer+call"`/`"call"` — caught and masked, nothing
   printed. `attempt-1.json` shows `classification: SUCCESS`, `killed_reason: null`. No
   run-integrity red flags.

## What I could not independently re-verify

- **Incumbent rung `cerebras-oss-120b` at `minerva_classifier.py:25`.** `/home/lumen/minerva`
  is outside this session's granted directories (worktree + `--add-dir` list does not
  include it), so I couldn't `grep` the exact line. Corroborating but not conclusive:
  `kb/ISSUE-LOG.md` rows #394 and #517 independently confirm `cerebras-oss-120b` is
  minerva's standing default (planner rescue model, `minerva_verify.py`'s
  `VERIFY_JUDGE_MODEL` default) — consistent with it also being the classifier rung, but
  I did not read line 25 itself.
- **"Draft diff appends JSON into YAML, `apply_cmd` only takes a backup."** The factory
  plan record (id `7f41ebb3c569`) lives in a minerva-side store I don't have a path to
  and isn't dumped into any `foreman/_runs/wq3140/` snapshot. Not independently checked.
  This claim is secondary to the verdict — claims 1-4 above already make the reject
  correct on their own; a well-formed diff would still have nothing runnable to apply it
  to (no Ollama-pullable model, wrong inference contract, no capable host).

Neither gap is load-bearing: the reject verdict rests on the model's PEFT/no-GGUF shape,
its logit-scoring (non-chat) contract, and the CUDA-BF16 hard requirement against a
GPU-less fleet — all three independently confirmed from primary sources (HF API, the
model's own `inference.py`, and the run's own hardware/environment snapshot).

## Verdict

**CONFIRM.** No contradiction found. wq#3140's reject stands. No judgment escalation
needed.
