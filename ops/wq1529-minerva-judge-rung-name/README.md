# wq#1529 — name the rung that answers the judge retry, then drop it

Follow-up to wq#1501 (`ops/wq1501-minerva-judge-rung/`). wq#1501 a2 found
`judge_ms=[230078,1995]`-shaped rows on the 2026-09-08 10:45 run: the primary
(qwen36-local) pins at `VERIFY_JUDGE_TIMEOUT` under LPP load, yet a second
number ~2s later shows a retry answered. `VERIFY_JUDGE_MODEL_2` is `''` in the
deployed drop-in, so the question was: which rung answers, and is it paid?

## Root cause

`minerva_verify.py`:

```python
JUDGE_MODEL_2 = os.getenv('VERIFY_JUDGE_MODEL_2', 'deepseek-v4-flash')
```

`os.getenv(name, default)`'s default only fires when `name` is **absent**
from the environment. `ops/wq1501-minerva-judge-rung/40-judge-model.conf` sets
`Environment=VERIFY_JUDGE_MODEL_2=` — present, empty — so on a host where that
drop-in is actually installed and loaded, `JUDGE_MODEL_2` is `''` and
`judge_entailment_uq`'s fallback branch (`if not all_runs and JUDGE_MODEL_2
and ...`) never fires. But any run where the drop-in is missing, not yet
`daemon-reload`'d, or reverted, `JUDGE_MODEL_2` silently reverts to
`deepseek-v4-flash` — a paid rung — with no error, no CONFIG BUG log line
(`_rung_sanity` only logs when `JUDGE_MODEL_2` is truthy AND not in the
gateway's model list; a valid paid default trips neither check), and no spend
alert distinguishing it from the intended free-only path.

**Confirming it's deepseek-v4-flash, not a guess:** `PROMOTER_JUDGE_REPAIR_DESIGN.md`
§1c already measured this exact model out-of-band: "the same prompt on
deepseek-v4-flash returns in 1.4-2.2s cache-miss (measured 10x)" — the ~2s
retry latency in the found judge_ms rows matches that window, not any other
rung on the fleet's judge path.

## Decision: drop, not spend (Frank, PROMOTER_JUDGE_REPAIR_DESIGN.md §2e)

The constraint is "free/local, no paid spend" on the judge — unconditional,
not "unless the drop-in didn't load." So the fix is at the code layer, not
just the ops layer that already existed: `minerva_verify.py`'s own default
becomes `''`, matching the drop-in's intent even when the drop-in is absent.
Two layers now agree: env override empty (wq#1501) + code default empty
(this ticket) = a primary timeout fails closed with zero fallback calls,
independent of whether the systemd drop-in is present.

## llama-server / abandoned-request note (ticket's "also")

llama-server does not cancel a non-streaming request when the client's own
socket times out — the abandoned 6144-token job keeps occupying an Isis slot
after `VERIFY_JUDGE_TIMEOUT` fires. Recorded, not fixed here: switching
`_judge_once` to `stream=True` would let a client-side abort actually cancel
the upstream generation, but it also means re-deriving the JSON-object
`response_format` handling (`_judge_once` currently parses one complete
`choices[0].message.content`; streaming needs incremental reassembly before
the same regex/`json.loads` parse) — a distinct, riskier change with no local
harness to validate against a loaded Isis. `VERIFY_JUDGE_TIMEOUT` is already
bounded (wq#1501, 230s) and Isis capacity/timeout tuning for qwen36-local
generally is wq#1623's scope (`litellm_params.timeout` ceiling 180s at the
router layer). Filing the stream=True change as separate follow-up work
rather than bundling an unvalidated behavior change into this rung-naming fix.

## What this directory installs

`minerva_verify.py`: one-line change (plus explanatory comment) —
`JUDGE_MODEL_2` default `'deepseek-v4-flash'` -> `''`.

## Install / rollback

```
/home/lumen/bin/lumen-run start --root --name wq1529-judge-rung -- \
    /home/lumen/foreman/ops/wq1529-minerva-judge-rung-name/install.sh
```

Hash-gated against the mirror pre-image (sha256 in `install.sh`; built from
`/home/lumen/research/minerva-ref`, ISSUE-LOG #233) and backs up to
`/home/lumen/backups/wq1529/`. Rollback: restore the `.bak` file from that
directory over `/home/lumen/minerva/minerva_verify.py`; no service restart
needed either way (env/code takes effect on the next timer start).

## Verify (replay, runs as lumen)

`ops/wq1501-minerva-judge-rung/verify_judge.py env` already fails on a
sub-120s timeout or a phantom `VERIFY_JUDGE_MODEL_2`; it does not currently
check the code-level default. No new replay script added — the change is a
single literal-default edit, confirmed by `diff` against the mirror pre-image
and by the `ast.parse` syntax check `install.sh` runs before installing.
