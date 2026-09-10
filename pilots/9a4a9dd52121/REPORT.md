# PILOT REPORT 9a4a9dd52121 — DeLS-Spec Parallel Speculative Drafting into LiteLLM Inference

wq#1570. Card: `CARD.md` (hypothesis/metric/sample/baseline/treatment sections
filled in during this run, per the card's own "edit it before the run" clause —
same situation as wq#1560, ISSUE-LOG #257).

## Lane defect encountered (same as wq#1560, still unresolved for new tickets)

This ticket's `needs:` field is empty (`wq show 1570` -> `needs :` blank).
`fleet-exec grant` from this session: `DENY [grant:grant_missing_env]: grant
file did not verify` — `fworker.resolve_exec_grant` derives the grant from the
ticket's `needs:` tags and writes no `FOREMAN_GRANT_FILE` when they're empty,
so every `fleet-exec run hathor` is denied regardless of the policy already
permitting it (`harness/fleet-exec-policy.yaml:108-113`). `research/
DEFER_PILOT_LANE.md:71-81` documents this exact gap from wq#1560 and says a
pilot ticket "must be created with `needs: ssh(<node>)`" — `minerva_defer_review`
evidently still isn't doing that for new pilot tickets. Logged as a new
ISSUE-LOG row below since this is a second live instance of the same defect,
not a duplicate to skip.

Separately, even with a grant, `fleet-exec-policy.yaml`'s `allow.ssh: [...,
hathor]` only permits `run_readonly_cmds` (true/uptime/hostname/cat/ls/stat/
df/free/systemctl status.../journalctl/ss/ip addr/tailscale status/docker
ps|logs) plus `copy` into `/home/hathor/pilots` or `/home/hathor/lumen-drop`.
There is no verb in fleet-exec v1 to execute an arbitrary command (a benchmark
script, a docker run) on hathor at all — so "run on hathor" is not
achievable by a BUILD worker today even with a correct grant.

## Substitution (disclosed, same fix as wq#1560)

Both arms ran from Lumen against the local LiteLLM gateway
(`127.0.0.1:4000`), which itself routes `hathor-lfm-1b` onto hathor — so the
draft half of the treatment is still hathor CPU-only compute, only the
harness process (timing, orchestration) runs on Lumen. Same host issues both
arms of the comparison, so the relative result (treatment vs baseline) is not
biased by running off-node; only absolute seconds are Lumen-process-clock,
not hathor-console latency.

## Architecture note (why the treatment is a black-box approximation, not true DeLS-Spec)

The proposal is "wrap the LiteLLM gateway; draft model generates speculative
tokens; target model validates them with DeLS-Spec logic." Real speculative
decoding needs token-level access: the draft and target share (or are mapped
across) a vocabulary, and the target does one forward pass to accept/reject
each drafted token, amortizing cost across the batch. LiteLLM here is a
routing proxy in front of independently served chat-completion endpoints
(different backends, different tokenizers for `hathor-lfm-1b` vs
`gemma4-e4b-local`); it exposes no logprob-level verify hook across two
different served models. The only thing buildable at this layer is a
completion-level approximation: ask the draft model for a full answer, then
ask the target model to accept-or-correct it as a second, independent HTTP
call. That is what `run_pilot.py` implements and measures.

## Fixed sample

6 one-sentence factual prompts (`run_pilot.py:SAMPLE`), both arms, same order,
one process run. `results.json` in this directory has the full per-prompt
record (prompts, both models' text, all latencies).

## Baseline

`gemma4-e4b-local` called directly per prompt. Per-prompt latency (s):
2.300, 4.465, 3.087, 2.287, 2.582, 4.637. Median = **2.835s**.

## Treatment

`hathor-lfm-1b` draft + `gemma4-e4b-local` verify, latencies summed. Per-prompt
total latency (s): 3.446, 5.144, 4.668, 3.620, 3.569, 5.105. Median =
**4.144s**.

Ratio (treatment median / baseline median) = **1.462** — treatment is 46.2%
*slower* than baseline, not faster. All 6/6 prompts individually were slower
under treatment than baseline (no prompt where the draft+verify pipeline won).

Quality: all 6 verify-stage answers were substantively correct and equivalent
to the baseline answers (spot-checked by reading; e.g. both arms answered
"Paris" / "Hypertext Transfer Protocol" / "Au" correctly). Exact-token
equivalence, the real DeLS-Spec lossless-decoding bar, was not testable on
this stack (see Architecture note) — quality was not the failure mode here,
latency was.

## Kill criteria check

None triggered on time/budget (total pilot wall-clock, from first
investigation to this report, well under the 90 min budget; token spend well
under 150000). The baseline reproduced cleanly (6/6 successful calls, no
errors). The result itself is the answer: the hypothesis (>=20% speedup) is
falsified, and by a wide margin in the opposite direction.

## Conclusion

On this fleet's actual serving stack (LiteLLM gateway fronting independently
served, differently-tokenized models, CPU-only), a draft-then-verify pipeline
built the only way this layer allows is not a speedup — it is a ~46% latency
*regression*, because it pays two sequential HTTP + full-generation costs
instead of one. This confirms the original defer's stated uncertainty
("benefit is uncertain on CPU-only hardware") resolves negative for the
architecture as proposed (a gateway-level wrapper). True token-level DeLS-Spec
would need to be built inside a single serving engine with shared KV-cache
across draft/target (e.g. vLLM/llama.cpp draft-model mode) — a materially
larger build than this pilot's budget, and orthogonal to "wrap the LiteLLM
gateway" as proposed. Recommend against pursuing the gateway-wrapper shape of
this proposal; if speculative decoding is still wanted, it would need to be
re-scoped as "add draft-model support to the serving engine(s) behind the
gateway," a different and much larger proposal.

RECOMMENDATION: SKIP — treatment median 4.144s vs baseline median 2.835s
(ratio 1.462, i.e. 46% slower, 6/6 prompts slower under treatment); win
condition was treatment <=0.8x baseline.
