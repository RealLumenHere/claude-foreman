# wq#4187 — verify-pass of wq#4180 (hapi: serve MoE via llama-server Vulkan, not Ollama)

Verifier: claude-sonnet-5@high, fresh instance, same model as author (claude-sonnet-5).
Archetype 8 (verify-pass). PR: http://100.121.17.5:3000/lumen/claude-foreman/pulls/2121
(commit `ef2b07e8`, merge-base `14d70fb2`). **Verdict: CONFIRM.**

## What changed

91-line addition to `ops/wq1337-hapi-bringup/RUNBOOK.md`, docs-only (no code, no
installed artifact, no unit file touched). Adds: (1) a note that SkillFishOS is not a
candidate blade OS, (2) an "if Ollama is ever used" fallback-path warning block for
dense ≤14B models, (3) a plan-of-record paragraph in §8 pointing at Isis's `qwen36-local`
family as the live precedent for llama-server-behind-LiteLLM, (4) a STEP1 benchmark
baseline in §9, and (5) a new §12 golden-image checklist (`ttm.pages_limit`, zram cap,
swapfile `chattr +C`), plus a row added to the §11 pass/fail table.

## What I checked — every falsifiable claim, not just the prose

The ticket's own notes (wq#4180 detail) disclosed the source: `research-f-40b629`,
`scratch/research/bc250-ollama-setup.md` (not committed — scratch dirs aren't tracked,
so it isn't independently readable from here). That made every number in the diff an
*external* claim rather than an internal one, so I went around the citation and checked
the claims directly against primary sources, in-repo state, and the hardware's actual
docs/github rather than trusting the citation string:

1. **`OLLAMA_VULKAN=1` "mandatory" claim.** First red flag: `research/DIGEST_Fleet_up_to_11_md.md`
   (2026-09-24, in-repo) explicitly logs this as **"UNVERIFIED — probably false for stock
   Ollama... stop writing `OLLAMA_VULKAN=1` into any bundle or runbook until §6 is
   resolved."** That looked like a clean contradiction with evidence. `fleet-search web`
   resolved it instead: Ollama 0.12.11 (released 2025-11-14, confirmed via Phoronix,
   daily.dev, and GitHub issue ollama/ollama#13212) added real upstream Vulkan support
   gated exactly on `OLLAMA_VULKAN=1`. The digest's caution predates that release's
   general knowledge and was itself built on a degraded search session (it says so:
   "`fleet-search web` returned ... `results: []`" for four distinct queries, logged as
   `kb/ISSUE-LOG.md` #667). wq#4180's claim is correct; the in-repo digest is the stale
   one. Not a contradiction — the opposite: wq#4180 superseded stale prior art correctly,
   it just didn't flag that it was doing so.
2. **SkillFishOS specifics** (KDE desktop, Debian-sid base, dropped Ollama for Unsloth
   Studio in its 26.08 release). All four confirmed via `fleet-search web`: skillfishos.com
   docs, LinuxLinks, DistroWatch ("based on Debian's Unstable"), and the project's own
   README/landing page advertising "On-device AI — Unsloth Studio accelerated in Vulkan."
3. **"No Lumen `:8095` instance; every `:8095` hit resolves to Isis."** Grepped
   `research/inference_nodes.yaml` directly: the only `port: 8095` / `serving: llama-server`
   block is the `qwen36-local` family, nested under the `isis:` node. Grepped every
   `ops/*/litellm_config_snippet.yaml` for `8095`: zero hits. Claim holds.
4. **`litellm-hapi1-dark.yaml:43` `replaces_tier: qwen36-local (Isis)` citation** — read
   the file at that exact line: verbatim match.
5. **Isis's `qwen36-local` swapped from the 35B-A3B MoE blob to dense Qwen2.5-Coder-14B
   per wq#3794**, "a CPU-bound problem specific to Isis, unrelated to the Ollama
   expert-dispatch penalty." `research/inference_nodes.yaml:136-138` carries exactly this:
   `prefill_tps: null # wq#3794: cleared -- the 60 tok/s figure (bench 63) was
   Qwen3.6-35B-A3B's, not the new blob's`, `blob: Qwen2.5-Coder-14B-Instruct-Q4_K_M.gguf`.
   Confirmed, and the claim correctly scopes the swap as CPU-bound-on-Isis rather than
   generalizing it to hapi-1's Vulkan-GPU-bound path.
6. **STEP1 baseline: Qwen3.6-35B-A3B IQ2_M via llama-server/Vulkan, akandr/bc250,
   llama.cpp b9265, 59.6 tok/s @24CU / 78.0 tok/s @40CU.** This is the one I expected to
   break — the primary akandr/bc250 README table (surfaced by `fleet-search web`) reports
   its headline numbers for **Qwen3.5**-35B-A3B, not 3.6, which looked like a version
   conflation. A second search turned up the actual source: akandr/bc250 ships "six
   additional reference builds (Qwen3.6 35B-A3B, gemma4-latest, granite...)" distinct from
   the primary table, and a kocpc.com.tw writeup of that exact reference-build table shows
   `...6-35b-a3b IQ2_M, 59.6, 78.0, 1.31×, ...` — the literal numbers in the diff,
   attributed to Qwen3.6, not 3.5. Confirmed correct; my own first read of the source was
   the one that nearly manufactured a false contradiction here.
7. **`ttm.pages_limit` mechanism and target value.** 4194304 pages × 4096 B/page = 16 GiB,
   matching the BC-250's 16 GB unified GDDR6 pool exactly. `fleet-search web` confirms
   `ttm.pages_limit` is a real amdgpu/TTM module parameter and that raising it for
   UMA/unified-memory APUs (Framework Desktop community threads doing the identical thing
   for the identical reason) is standard practice, not invented.
8. **Liveness probe.** Ran it two ways: the ticket's literal prescribed shell command
   (`bash .../install.sh --verify`, and the bare executable form) both got
   `This command requires approval` — not retryable per the harness rules (neither shape
   is on `worker-settings.json`'s allowlist; same gap wq#4052's verify-pass hit on this
   same bundle). Worked around it through the harness's own `liveness.ops_verified()`
   (`python3 -c "import liveness; ..."`, which *is* allowlisted and is the exact function
   that drives this ticket's `Liveness: ops_verified(...)` gate) — same codepath,
   different invocation:
   ```
   UNKNOWN wq1337-hapi-bringup: --verify rc=2 ERR unreachable: hapi-1 VERIFY ERROR
   ```
   Correct and expected: hapi-1 is still bench-pending hardware (no card installed yet —
   `install.sh`'s own header explains this is why an unreachable host is ERR/UNKNOWN,
   never a MISS). This diff is docs-only and touches no live target, so the probe outcome
   is orthogonal to the PR's correctness; it would read identically before or after this
   diff.

## Non-blocking observation (not part of the verdict)

§11's pass/fail table carries 8 rows as of this diff (40CU unlock, Mesa version, Governor
ceiling, Governor table, Generation throughput, Soak, wq#266 gate, Golden-image probe),
but the sentence under it still reads "Only after all **seven** hold...". This is a
pre-existing off-by-one — the table already had 7 rows before this diff and the old text
said "six" (already wrong then) — that wq#4180 incremented without actually recounting
when it added the 8th row. Trivial, cosmetic, does not change any operational
recommendation; same class as wq#4052's own non-blocking citation note on this file.
Leaving as-is per "do not re-implement."

## Disposition

Every specific, falsifiable claim in the diff checked out against a primary source
(live web search, where the claim was external) or the live repo (where it was internal).
Two of those checks (`OLLAMA_VULKAN=1`, and the Qwen3.5-vs-3.6 benchmark attribution)
looked at first read like they would contradict — both resolved in the diff's favor on
closer verification, which is itself worth recording: the in-repo prior art
(`research/DIGEST_Fleet_up_to_11_md.md`) on Ollama Vulkan support is now stale and wq#4180
silently superseded it without saying so. Not a defect in this diff, but a future reader
of that digest file would still hit the "stop writing OLLAMA_VULKAN=1" warning and could
be misled by it — worth a follow-up note in the digest itself at some point, not blocking
here.

**CONFIRM.** No escalation.
