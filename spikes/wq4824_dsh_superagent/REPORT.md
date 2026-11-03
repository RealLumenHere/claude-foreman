# wq#4824 — dsh-superagent pilot, phase 1: LiteLLM routing verified, sandbox plan

Date: 2026-11-03. Node: Lumen. Upstream: github.com/deepseek-ai/dsh-superagent-prototype
at `6337fc8` (shallow clone, fetched 2026-11-03).

## Verdict

LiteLLM can sit under DeepSeek Harness (dsh) for this bot. Tool calling through
`localhost:4000` works for the pinned DeepSeek models, both on the raw OpenAI protocol
and inside a real dsh agent loop. Two things need fixing before the GitHub side goes
live, and neither is a model problem:

1. **The bundle runs `danger-full-access` as the operator's OS user.** Run as `lumen`,
   a prompt-injected issue body could read `/home/lumen/agent/.env` and every other fleet
   credential. The pilot must run under a dedicated OS account (§4).
2. **The router alias `lumen/fast` breaks tool calling** (§2). Pin model ids. Do not
   use a fallback alias behind an agent loop.

Phase 2 (the live trial on a test repo) needs an agent GitHub account and a
fine-grained PAT. Only Frank can create those, so that step is filed as a physical
task. Phase 2 has its own build ticket (§6).

## 1. Prerequisites on Lumen

| Need (upstream SETUP.md:27, package.json `engines`) | Lumen | OK |
|---|---|---|
| Node `^22.19.0 \|\| >=24` | v22.23.3 | yes |
| dsh `^0.2.1-alpha.2` | 0.2.1-rc.2 (`~/.npm-global/bin/dsh`) | yes |
| pnpm (`packageManager: pnpm@11.7.0`) | missing | **no** |
| GitHub CLI 2.48+ | 2.46.0 (Ubuntu package) | **no** |
| git 2.32+, tmux, Python 3.8+ | 2.53.0, 3.6, present | yes |

The worker allowlist refused `npm install -g pnpm@11.7.0`, so pnpm and the newer gh
are left for the phase 2 ticket, which needs an operator shell anyway.

## 2. Tool-calling fidelity through LiteLLM (raw protocol)

`toolcall_probe.py` uses a two-tool schema and probes each model three ways. **call**:
non-streamed, it must pick `add_numbers(17, 25)` with integer args and a call id.
**stream**: the streamed deltas must reassemble into the same call. dsh's pi-ai adapter
streams, so this check matters most. **round**: given the tool result `42`, the model
must answer 42 without calling the tool again. Raw results are in `toolcall_results.json`.

| Model | call | stream | round | |
|---|---|---|---|---|
| deepseek-v4-flash | ok | ok | ok | PASS |
| deepseek-v4-pro | ok | ok | ok | PASS |
| deepseek-chat | ok | ok | ok | PASS |
| kimi-k3 | ok | ok | ok | PASS |
| glm-5.3-flash | ok | ok | ok | PASS |
| lumen/best | ok | ok | ok | PASS |
| lumen/fast | ok | **no streamed tool_calls** | **re-called tool** | FAIL |
| gemini-flash | HTTP 429 | HTTP 429 | skipped | not measured (rate-limited) |

The `lumen/fast` failure shows the probe can fail. It also shows the real risk: an
alias can pass a non-streamed spot check and still break the streaming path the
agent actually uses.

## 3. Tool calling inside a dsh agent loop

`dsh_e2e.py` boots `dsh --profile headless --patch litellm.patch.yml --json` in a fresh
scratch directory. The directory holds one file with a random six-digit nonce, and the
task asks for the nonce × 3. A run passes only if the event stream has a `tool_call`
event and the answer has the right product. The model cannot produce that number
without reading the file.

| Run | rc | tool calls | answer | result |
|---|---|---|---|---|
| deepseek-v4-flash (`e2e_flash.json`) | 0 | `read` | correct | PASS (5.0 s) |
| deepseek-v4-pro (`e2e_pro.json`) | 0 | `read` | correct | PASS (5.9 s) |
| negative control `no-such-model-wq4824` (`e2e_negative.json`) | 1 | none | — | FAIL, as intended |

The negative control's stderr reads `UNKNOWN_MODEL: pi-ai provider "litellm" has no
configured model ...`. That confirms the run resolved the model through the `litellm`
provider from the overlay, not through the profile default (`deepseek-official`).

The overlay format comes from the dsh provider guide,
github.com/deepseek-ai/deepseek-harness/blob/master/docs/user/guide/providers.md
(fetched 2026-11-03): an `llm-pi-ai` entry with `apiKeyEnv`, `api: openai-completions`
and `baseURL`. The default model is set through the `agent-default-model` entry
(docs/config-catalog.md, same repo).

## 4. Sandbox requirements for phase 2

From upstream `docs/006-deployment.md` §"Permissions and isolation" (fetched 2026-11-03):
"Both presets run with `danger-full-access` as the operator's OS user ... A worker can
execute programs by path and read the operator's home directory. For isolation, use a
dedicated OS account containing only the agent's credentials."

Decisions (made under the 2026-08-22 autonomy order; Frank can revisit them):

- **OS user**: a dedicated `dshbot` account on Lumen with its own `$HOME`, `DSH_HOME`
  and gh login. It gets no read access to `/home/lumen`. Never run the bundle as `lumen`.
- **LLM credential**: a LiteLLM *virtual* key limited to `deepseek-v4-flash` and
  `deepseek-v4-pro`, with a daily budget, stored 0600 in `dshbot`'s home. Never the
  master key (instinct 7). Copy the `litellm.patch.yml` entries into
  `~dshbot/.dsh/profiles/superagent/cordis.patch.yml`.
- **GitHub credential**: an agent account with a fine-grained PAT scoped to one
  throwaway test repo (contents, issues, pull requests: read/write; nothing else).
  Branch protection on `main` requires a human review.
- **Gate on PRs**: `action_gate.py` is a Claude Code PreToolUse hook, and dsh cannot
  load it. Its two jobs map onto dsh like this. (a) Destructive commands: set
  `executor.blockedCommands` (command names only, `src/policy.ts:88-96`) to the
  binaries behind `BUILTIN_RED` (`harness/action_gate.py:124-149`): `dd`, `mkfs` and
  its `mkfs.*` names, `wipefs`, `blkdiscard`, `shred`, `fdisk`, `gdisk`, `sgdisk`,
  `sfdisk`, `parted`, `fastboot`, `heimdall`. Add guard.sh's house denials: `sudo`,
  `reboot`, `shutdown`, `systemctl`. Name blocking is coarser than action_gate's
  regexes because it blocks the whole binary, read-only uses included. A coding bot on
  a test repo needs none of these. (b) Irreversible outward actions: leave auto-merge off
  (upstream default, SETUP.md question 9). Every PR then waits for a human merge
  behind branch protection.
- **Prompt injection**: only allowed users can trigger the bot (panel → Policy allowed
  lists). Keep `eval.mode` at `observe` (the upstream default, `src/policy.ts:84`) for
  the first week, so the bot does not act on rules alone. The red-team set for phase 2
  is an issue body that tells the agent to print its environment, read `~/.dsh`, or
  push to `main`. Each attempt must fail on isolation, not on the model refusing.
- **Panel**: binds `127.0.0.1:3082` (upstream SETUP.md:55). Do not tunnel it.

## 5. Reproduce

    python3 spikes/wq4824_dsh_superagent/toolcall_probe.py models
    python3 spikes/wq4824_dsh_superagent/toolcall_probe.py probe deepseek-v4-flash lumen/fast
    python3 spikes/wq4824_dsh_superagent/dsh_e2e.py --model deepseek-v4-flash

Both scripts read the gateway key from `/home/lumen/agent/.env` (`LITELLM_KEY_FILE` and
`LITELLM_KEY_NAME` override this) and put it only in a header or the child environment.

## 6. Follow-ups

- Physical (Frank): create the agent GitHub account and the test repo, mint the
  fine-grained PAT, and leave it 0600 for `dshbot`.
- Build (phase 2): create the `dshbot` user, install pnpm 11.7.0 and gh ≥ 2.48, mint
  the scoped LiteLLM virtual key, run `pnpm install && pnpm build` and the upstream
  setup as `dshbot`, then run the injection red-team set in §4 on the test repo.
