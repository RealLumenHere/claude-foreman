# wq#1501 — Minerva verify judge: free/local rung, no phantom fallback

Source of truth for `/etc/systemd/system/minerva-proposal.service.d/40-judge-model.conf`.
Report with evidence: `reports/1501_minerva_judge_rung.md`.

## What was wrong (since 09-04)

| symptom | cause |
|---|---|
| majority of proposal verifies REJECT with `judge_err:ReadTimeout` | judge on deepseek-v4-flash behind a hard-coded 40s request timeout |
| every fallback ends `fallback:judge_http_4xx` | `VERIFY_JUDGE_MODEL_2=mistral-small` was never a LiteLLM rung (phantom) |
| judge never sees the digest tail or any retrieved fact | `FACTPACK_CAP` (= `JUDGE_CTX_CHARS` 16000 - reserve) < proposal digest (~17.1k chars); wq#1383 window class, 3rd occurrence |

## What this directory installs

`40-judge-model.conf`:

| var | value | why |
|---|---|---|
| `VERIFY_JUDGE_MODEL` | `qwen36-local` | free rung (llama-server on Isis :8095, n_ctx 8192); Frank: no paid spend on the judge |
| `VERIFY_JUDGE_MODEL_2` | empty | no free+alive second rung exists; empty = fail closed on outage, never a phantom |
| `VERIFY_JUDGE_TIMEOUT` | 230 | cold prefill on Isis is 150s+ under LPP load; see the report for what the timeout really buys |
| `VERIFY_JUDGE_SAMPLES` | 1 | 3 samples x cold prefill saturates Isis for hours; vote confidence is advisory only |
| `VERIFY_JUDGE_MAX_TOKENS` | 600 | labels + one-sentence rationale |

## Apply (root, detached, idempotent)

```
/home/lumen/bin/lumen-run start --root --name wq1501-judge -- \
    /home/lumen/foreman/ops/wq1501-minerva-judge-rung/apply.sh
```

Backs up the previous file to `/home/lumen/backups/wq1500-1501/`, installs, `daemon-reload`.
Never restarts the service; the env is read at the next timer start (every 4h at :45).

## Verify (replay, runs as lumen)

```
python3 ops/wq1501-minerva-judge-rung/verify_judge.py env journal --since "2026-09-08 10:45"
python3 ops/wq1501-minerva-judge-rung/verify_judge.py window --digest /home/lumen/minerva/lumen_state.txt
```

`env` fails on a phantom fallback or a sub-120s timeout. `journal` fails on any
`fallback:judge_http_4xx` or a ReadTimeout share above 10%. `window` warns when
`JUDGE_CTX_CHARS - reserve` cannot hold the digest (the §2e condition).

## Rollback

```
sudo cp /home/lumen/backups/wq1500-1501/<newest>.40-judge-model.conf.bak \
    /etc/systemd/system/minerva-proposal.service.d/40-judge-model.conf && sudo systemctl daemon-reload
```

## Not in this directory

The `build_context` budget change (design §2e: reserve room for retrieved facts
before the digest, raise `JUDGE_CTX_CHARS` within Isis's 8192-token window) lives
in `/home/lumen/minerva/minerva_factpack.py`, which is outside the foreman
worker sandbox. It is filed as its own ticket (see the report) and must be
dispatched with `--needs dir(/home/lumen/minerva)`.
