# macbench (wq#2246)

Unattended model-after-model bench for the Mac Pro 6,1 boxes. Speed from the server's own
counters, plus an agentic score, because speed alone picks the wrong model. Results feed
the capability profiles in the inference node registry design (wq#2241).

```
python3 tools/macbench/macbench.py plan    --node wadjet              # dry run, no node contact
python3 tools/macbench/macbench.py run     --node wadjet              # walk matrix.yaml, resumable
python3 tools/macbench/macbench.py agentic --model <gateway alias> --node <label>
python3 tools/macbench/macbench.py render  --node wadjet --md wadjet.md
python3 -m pytest tools/macbench/tests -q
```

Run `run` from a host that can ssh to the node and reach its ollama/llama-server ports
(Lumen), detached (`systemd-run`), since a matrix takes hours.

## Files

| file | what |
|---|---|
| `matrix.yaml` | rows: model, quant, engine (ollama, llama-server), flags (threads, ctx, slots, batch, kv_type, thinking, extra_args). Seed matrix from the ticket; swap in `research/MAC_CPU_MODEL_SHORTLIST.md` rows when it lands |
| `nodes.yaml` | ssh target, models dir, plug id per node |
| `mb_runner.py` | sequential, resumable walk of one node; restores what was resident |
| `mb_measure.py` | the measurement sequence; one JSON line per measurement |
| `mb_engines.py` | ollama and llama-server adapters (fetch, start, warm, complete, stop) |
| `mb_node.py` | ssh transport, SMC sensors, plug watts, every safety guard |
| `mb_agentic.py`, `agentic/` | the 8-task suite and its scorer |
| `mb_render.py` | markdown table |

## What one row measures

warm, then: prefill tok/s at 512, 4k and 8k tokens (a fresh nonce per prompt so nothing is
cached); the same 4k prompt again (prefix-cache reuse); decode tok/s; a solo 2k prefill
then two concurrent 2k prefills (`slowdown` about 2 = time-sliced, about 1 = parallel);
resident memory; package temperature and fan from the SMC sysfs
(`applesmc.768/fan1_input`, hwmon `coretemp`), sampled through the row; wall watts from
`plug-ctl <plug> watts` when the node has a plug. Then unload.

Every number is a server counter: ollama `prompt_eval_count/duration`, `eval_count/duration`;
llama-server `timings`. Prompt length is calibrated per row from the server's own token
counts, so "4k" means about 4096 tokens, and the actual count is in each record
(`prompt_new_n`, `prompt_total_n`).

Records are JSON lines in `tools/macbench/results/<node>.jsonl` (`schema: macbench/1`).
A row finished with `row_done` and an unchanged `row_digest` is skipped on the next run;
an edited row re-runs. Failures are `row_error` / `row_skipped` (with a reason) and the walk
continues.

## Quant sweep

The `sweep` row resolves at run time to the best measured candidate (by `metric`, default
`decode_tps`), cloned onto each quant in that candidate's `variants`. A quant with no
`variants` entry is skipped with a reason, never guessed.

## Agentic score

Eight tasks, four `mechanical-edit` (rename, default change, %-format to f-string, thread a
parameter) and four `probe-build` (disk, ports, log, env probes), each a fresh scratch git
repo with its own test. Same suite for every row. Each task is run with
`fworker.build_claude_cmd(local_profile=True)`, `fworker.local_profile_env()` and the LiteLLM
gateway env, exactly what a local-rung worker gets; the settings file is
`agentic/settings.json` (no hooks, no evidence gate). The task's test files are restored
from the suite before scoring, so editing a test cannot pass a task. Per task:
`success` (test passes, the diff is non-empty, no timeout), `turns`, `tool_calls`,
`tool_errors`, `wall_s`, `diff_files`, `diff_lines`, and the model the init frame reported.
Summary: `agentic_score` = successes / 8, median turns, total wall, tool errors.

Scoring never trusts a size+mtime cache. Both the test result and the diff can be read
from one: CPython revalidates a `.pyc` on its source's mtime and size, and git skips
reading a file whose mtime and size still match the index. A task prompt tells the model
to run the test itself mid-session, and these tasks are single-line edits by design, so
"the bytes changed but mtime and size did not" is a shape the suite actively invites. So
`run_test()` purges every `__pycache__` and runs under `PYTHONDONTWRITEBYTECODE=1`, and
`diff_stats()` stages into a scratch index built from scratch, hashing content rather
than consulting the baseline commit's recorded stat.

The gateway only serves aliases it is configured with, so an agentic score for a bench row
needs an alias pointing at the bench endpoint (set `agentic.model` on the row and run
`agentic --model <alias>`); `--base-url` points the suite at an engine's own
Anthropic-compatible endpoint instead (untested here).

## Safety

- Never benches a node in the dispatcher's local-worker group (read from
  `dispatcher/config.json`) without `--allow-serving-group`; agentic runs against a group
  member's alias need the same flag. Thor is refused in code.
- Refuses when free disk (`/` and the models dir) is under 30 GB, and when a fetch would
  leave under 30 GB.
- Restores whatever ollama had resident (name, context, remaining keep-alive) when the run
  ends, also on error or SIGTERM. It does not stop or restart services: a node already
  running a `llama*` unit is refused for llama-server rows. Bench llama-servers run on port
  8097 as a transient process, killed by pidfile+port only.
- No power actions: every command sent to a node is screened against a deny-list
  (reboot, shutdown, suspend, `systemctl` state changes, `ip link`, ...), and the plug tool
  is only ever asked for `watts`.

## Not verified from the build sandbox

The worker sandbox has no ssh and no non-serving Mac was online, so the speed run and the
agentic runs beyond the hathor baseline were not executed live. Unverified inputs, all
flagged in the yaml: the isis unit's exact flags and GGUF path, `models_dir` and
`llama_server_bin` per node, wadjet's ssh user, plug ids, the gemma4 tag. wadjet needs
RAPL PL1 back at 75 W before benching (macbench never changes it).

A `needs:ssh(wadjet)` dispatch tag alone does not fix this (wq#2636, ISSUE-LOG #757):
`mb_node.py`'s remote-exec path shells raw `ssh user@host …` (`tools/macbench/mb_node.py:97`),
never fleet-exec, and raw `ssh` is categorically absent from the BUILD sandbox's Bash
allowlist regardless of any grant. Separately, `fleet-exec-policy.yaml`'s `allow.ssh` list
does not include wadjet at all, and even a listed node only permits `run_readonly_cmds`
probes — never script execution — so widening that policy would not unblock this script
either. The live run needs either a `bench` verb added to fleet-exec (routing macbench's
remote launch/poll/copy-results through the broker) or an operator running
`macbench.py run --node wadjet` directly from a shell with real ssh.
