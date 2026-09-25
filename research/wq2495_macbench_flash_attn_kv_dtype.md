# wq#2495 — macbench flash-attention + KV cache dtype + batch rows

Source: `research/DIGEST_Orchestation_2_md.md` T1 (wq#2466 digest of Frank Orchestation 2.md,
O2 L906-916), which claims q8_0 KV cache halves context memory and flash attention is a
prerequisite for the quantized-KV path in llama.cpp. This ticket was BUILD scope: add the
matrix rows to measure that claim on our own MacPro6,1 hardware, not adopt it on the doc's say-so.

## What landed

Four new `llama-server` rows in `tools/macbench/matrix.yaml`, alongside the existing
`qwen36-35b-a3b-q4km-t12` f16/no-FA baseline (`matrix.yaml:20-28`), sweeping exactly the
dimensions the ticket named:

| row id | matrix.yaml | flags added vs. baseline |
|---|---|---|
| `qwen36-35b-a3b-q4km-t12` (baseline, pre-existing) | :20-28 | `kv_type: f16` (default), no `-fa`, stock batch |
| `qwen36-35b-a3b-q4km-fa-f16` | :45-52 | `extra_args: ["-fa", "on"]` |
| `qwen36-35b-a3b-q4km-fa-q8_0` | :54-61 | `+ kv_type: q8_0` |
| `qwen36-35b-a3b-q4km-fa-q8_0-b2048` | :63-70 | `+ batch: 2048`, `extra_args` `--ubatch-size 2048` |
| `qwen36-35b-a3b-q4km-fa-q4_0-b2048` | :72-79 | same as above with `kv_type: q4_0` (aggressive bound) |

Flag names (`-fa on`, `--cache-type-k/-v`, `-b`/`-ub`) are taken from
`research/MAC_CPU_MODEL_SHORTLIST.md:412,475` and
`research/DIGEST_Orchestation_2_md.md:176`, not guessed. `ubatch` has no dedicated key in
`tools/macbench/mb_matrix.py`'s `FLAG_KEYS` set, so it rides in `extra_args` as
`--ubatch-size`; `mb_engines.py:134-144` (`LlamaServerEngine.argv`) confirms `batch` maps to
`--batch-size` and non-f16 `kv_type` maps to `--cache-type-k/-v`.

Verified with a dry run (no node contact, matches the runner's own `plan` mode):

```
python3 tools/macbench/macbench.py plan --node wadjet
```

produced correct argv for all four new rows, e.g. row 5:

```
/usr/local/bin/llama-server --model <models_dir unset>/qwen3.6-35b-a3b-Q4_K_M.gguf \
  --host 100.116.217.15 --port 8097 --jinja --threads 12 --ctx-size 65536 --parallel 2 \
  --batch-size 2048 --cache-type-k q8_0 --cache-type-v q8_0 -fa on --ubatch-size 2048
```

`python3 -m pytest tools/macbench/tests -q` still passes at the same rate as before this
change (41/42; the one pre-existing failure, `test_agentic.py::...[e2_default]`, is an
unrelated agentic-suite fixture and reproduces identically with `matrix.yaml` reverted).

## What did NOT land: live numbers

This ticket's ACCEPTANCE line asks for a results table with prefill_tps/decode_tps/peak
RSS/max-ctx per row plus the f16 baseline, a recommended flag set, and a stated new max ctx
for isis. None of that exists yet, and this doc will not fabricate it. Root cause, logged at
`/home/lumen/kb/ISSUE-LOG.md` #730:

- `tools/macbench/macbench.py run` has to reach a real node over ssh (`fleet-exec`); this
  BUILD worker's sandbox has no ssh grant. `tools/macbench/README.md`'s own "Not verified
  from the build sandbox" section says this is a standing limitation, and it reproduces live:
  `fleet-exec run wadjet 'echo ok'` → `DENY [grant:grant_missing_env]`.
- The grant is derived from the ticket's `needs:` tag at dispatch
  (`harness/fworker.py:resolve_exec_grant`), and `wq show 2495 --json` shows `needs: ""` —
  no tag was set, so no grant file was ever created for this attempt (same failure class as
  ISSUE-LOG #192 / #257).
- Separately, `tools/macbench/results/` currently has no `.jsonl` for the
  `qwen36-35b-a3b-q4km-*` rows at all — even the f16 baseline has never been benched here, so
  there is no existing baseline number to diff the new rows against either.
- `isis`, `hathor`, `nephthys` are production nodes already running a `llama*` unit; per
  `tools/macbench/README.md`'s Safety section the runner refuses to bench a node already
  running one, so none of this can or should run against them directly — `wadjet` (the
  non-serving MacPro6,1 in `tools/macbench/nodes.yaml`) is the only valid target, which is
  consistent with this ticket's own constraint not to touch the production units.

Filed **wq#2636** (`needs: ssh(wadjet)`, so dispatch actually provisions the grant this
attempt lacked) to run the matrix on wadjet end to end and fill in this table. No flag set
is recommended and no new max ctx for isis is stated here — that claim only becomes real once
wq#2636's live numbers exist. `llama-qwen36.service` and `research/inference_nodes.yaml`
(`context_tokens` at :23, `served_ctx` at :60/:88/:116) are unchanged by this ticket, per the
"do NOT edit the live unit from this ticket" constraint.

## Open question for wq#2636

`macbench.py plan` above shows `<models_dir unset>` for wadjet — `tools/macbench/nodes.yaml`
has `models_dir: null` for every node, so wq#2636's first live run will fail loudly
(`has_model()` raises `Refused`, per `mb_engines.py:146-149`) if wadjet's actual GGUF path
isn't absolute or `models_dir` isn't set first. That's a wadjet-inventory question, not
something this ticket can verify without the same ssh access.
