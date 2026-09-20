## What

Local-rung workers (`is_local_rung`) get a restricted `claude -p` profile: six tools, no MCP, no session-title side call, so one slot per worker. The size gate now counts the CLI's own fixed request, and a spawn whose prompt cannot prefill inside the first-frame budget is refused before Mac time is spent. Wq#2185, follow-up to wq#2128 under wq#2127.

Measured first request (stub-recorded, chars/3.5, representative mechanical-edit prompt of about 1.4k tokens, CLI 2.1.276, scratch HOME and repo; `python3 harness/cli_calibration.py`):

| | requests to the Mac | first model request | tools sent |
|---|---|---|---|
| before (bare command) | 2 (2,407-token session-title call, then 22,583) | 22,583 | 22 |
| after (profile) | 1 | 5,361 (system 970, tools 2,845, messages 1,546) | 6 |

The live 18,567 figure was counted on the llama tokenizer, roughly 0.7x of chars/3.5, so the real first request should land near 3.8k. Under the 6,000 target either way. The before row has no fleet CLAUDE.md, auto-memory or MCP definitions, which real workers also carry, so it understates the old cost.

## Why

At hathor's 13.6 tok/s prefill an 18.5k first request is a 23-45 minute first frame against a 1500 s budget, so every local attempt could only end as a first_frame kill. The wq#2128 gate estimated our prompt text only (about 4k of the 18.5k) and could not see it.

## How

- `build_claude_cmd(local_profile=)` adds `--tools Bash,Read,Edit,Write,Grep,Glob --strict-mcp-config --exclude-dynamic-system-prompt-sections` (harness/fworker.py:1312, :1349). Not `--bare`: the action gate and stop gate hooks keep running. Flags checked against `claude --help` on 2.1.276. The three flags alone left 5,455 tokens of overhead; the env half brings it to 3,925, so no `--system-prompt` replacement was needed.
- Side request (fworker.py:1319): it is the CLI's session-title call, fired on longer prompts and sent to the same model, so it took the box's second slot. `CLAUDE_CODE_DISABLE_TERMINAL_TITLE=1` stops it (probed: `--name` and `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` do too). `ANTHROPIC_SMALL_FAST_MODEL` and `ANTHROPIC_DEFAULT_HAIKU_MODEL` route any other small/fast call to `deepseek-v4-flash` (probed: the title call moves there). The same env drops the CLI's per-machine context: CLAUDE.md files (the fleet CLAUDE.md busy-key protocol is wrong for a headless worker, and it made the estimate depend on HOME), auto-memory, git status and instructions.
- Calibration: `harness/cli_calibration.py` runs the exact command line against a local HTTP stub that records bodies and answers with a canned stop. `charging.LOCAL_CLI_OVERHEAD_TOKENS = 3925` sits next to `CALIBRATED_CLI_VERSION = "2.1.276"`; a test fails when the installed CLI differs or a fresh measurement drifts more than 3%. `prompt_tokens_est` (attempt record and both gates) includes it under the profile.
- Budget gate (fworker.py:2452): `dispatcher/config.json` rows carry `prefill_tps` (13.6 / 7.4 / 60, measured 2026-09-20), the dispatcher passes `FOREMAN_PREFILL_TPS`, and fworker refuses `prompt_tokens_est / prefill_tps * 2 > first_frame_s` as `PROMPT_OVER_BUDGET`. That is a SpawnRefused: uncharged, no attempt record, no backend cooldown. A 5.4k prompt costs about 790 s on hathor against 1500 s.
- Charging nuance: a local first_frame kill with `prompt_tokens / prefill_tps > first_frame_s` is charged to `harness` (`prompt_over_budget`), not `rung`, so `handle_rung_fault` never cools the backend. It cannot fire for a spawn that passed the budget gate (the gate is stricter); it covers attempts recorded without the gate.

## Testing

`harness/tests/test_local_cli_profile.py`: command line for local, cloud and both levers; child argv and env through the fake CLI; CLI version pin; live stub calibration including the one-request and 6,000-token checks; budget refusal end to end; charging nuance. Dispatcher lint and `spawn_env` tests for `prefill_tps`. `test_charging.py`, `test_local_rung.py`, `test_local_capacity_dispatch.py` still pass. The live re-proof on a Mac is the orchestrator's.

## Risk / rollback

`FOREMAN_LOCAL_CLI_PROFILE=off` (or `FOREMAN_CHARGE_MODE=legacy`) restores the bare command and the prompt-only size gate. Disabling CLAUDE.md for local workers means repo-level CLAUDE.md conventions no longer reach them; the slim local prompt is self-contained by design. If the CLI is upgraded the version-pin test fails until `cli_calibration.py` is re-run.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
