# wq#1623 — qwen36-local is queue-starved, not down

Measured during wq#1588 on Isis `llama-qwen36.service` (pid 1936, :8095, 2
slots x 8192 ctx): `/health` returns ok and a 4-token prompt completed in
723ms. It is queue-starved, not dead — a 4,910-token prompt (task 104125)
spent 277s in PROMPT EVAL alone and was only 76% through at 17-20 tok/s, and
three sibling tasks were cancelled by their own callers while waiting. Isis
load average was 20.06 with `ollama.service` and `ollama-lpp.service` on the
same box.

That reframes the 33,292 qwen36-local failures FLEET_COST_PLAN D2 counted:
they are queue timeouts and client cancellations, not capability failures.
Full detail: `research/FLEET_COST_PLAN.md` §D2/D3, ticket body of wq#1623.

## (a) Capacity decision: qwen36-local stays a small-prompt-only rung

**Decision: do not dedicate Isis to `llama-qwen36.service`.** The queue
starvation traces to `ollama.service` and `ollama-lpp.service` sharing the
box with `llama-qwen36.service` (load average 20.06 at the time of the
wq#1588 measurement) — giving the rung exclusive capacity would mean
stopping those other services, which are load-bearing for other consumers
(`lumen-memoryd.service` Wants `ollama.service`; LPP's own brain routes
through `ollama-lpp.service`). That is a shared-infra capacity trade, not a
free win, and this worker's sandbox has no `systemctl stop` access to Isis
regardless (deny-list, and Isis is outside every `--add-dir` root).

Accepting qwen36-local as a small-prompt-only, queue-bounded rung is the
lower-risk call: it keeps every existing consumer of `ollama`/`ollama-lpp`
running unchanged, and pairs with (b)/(c) below to stop the rung from being
handed work it queues on. Reversing this (actually dedicating the box) is a
physical/ops capacity call for whoever owns Isis's other services — flagged
here, not gated on.

## (b) Timeout: bound to what callers actually wait, not 600s

qwen36-local carried `timeout: 600` and qwen36-live carried `timeout: 120`
against the same upstream (ollama/qwen36 on Isis). The wq#1588 live journal
(05:43-05:47Z) shows real callers cancelling at 40-160s — nothing observed
waits anywhere near 600s, so that ceiling bought nothing but a held
connection.

`audit_rung_timeout.py` encodes the rule: any deployment targeting the
qwen36/Isis upstream (`ollama/qwen36*` model string, or an api_base pointing
at Isis's tailscale address `100.100.129.24`) must carry
`litellm_params.timeout <= 180` (`MAX_CALLER_WAIT_S`) — a small margin above
the 160s longest observed caller wait, replacing both qwen36-local's 600 and
tightening nothing on qwen36-live's already-compliant 120.

```
python3 audit_rung_timeout.py /home/lumen/agent/litellm_config.yaml
```

Exits 1 with a human report (or `--json`) if any qwen36/Isis deployment
exceeds the ceiling; 0 otherwise. Same sandbox caveat as wq#1406/wq#1591/
wq#1593: `/home/lumen/agent` (where the live proxy config actually lives)
isn't in this BUILD worker's `--add-dir` set, so this is verified against
fixtures in `tests/`, not the live file — run it for real on first deploy.

`litellm_config_snippet.yaml` in this directory is the hand-merge fragment:
change qwen36-local's `timeout: 600` to `timeout: 180`.

## (c) FLEET_COST_PLAN gate: hold the "after D2/D3" moves

FLEET_COST_PLAN.md routes `lumen-agent (cerebras leg)`, `minerva verify
judge`, and `sia-news` onto `qwen36-local after D2/D3`. D2/D3 (breaking the
`qwen36-local <-> cerebras-oss-120b` circular fallback, fixing the
content-type rejection) are necessary but not sufficient: even with the
cycle broken, `cerebras-oss-120b -> qwen36-local` and
`cerebras-llama -> qwen36-local` survive by design (paid-to-free saves
money), and today they hand a fast paid failure to a rung that can sit for
minutes. Updated the three routing-table rows and the D2/D3 discussion in
`research/FLEET_COST_PLAN.md` to read "after D2/D3 and wq#1623" — those
moves stay blocked until this rung's timeout is bounded (b) and its capacity
question is answered (a), not just until the circular fallback is fixed.

## Test procedure (zero quota, no live gateway needed)

```
python3 ops/wq1623-qwen36-queue-guard/tests/test_audit_rung_timeout.py -v
```
