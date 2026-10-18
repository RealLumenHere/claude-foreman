# wq#4213: qwen-local-small has two api_base entries, one dead, no fallback

Found by overnight benchmarking 2026-10-18 07:3xZ: `docker logs litellm` shows
`ClientConnectorError` to `192.168.8.125:11434` for `model_group=qwen-local-small`
("No fallback model group found"). `litellm_config.yaml` lines ~299 and ~311
define two `qwen-local-small` deployments — `api_base .125` (commented
"hathor judge box") and `api_base .135` (commented "ptah").

## Host identity (lumen_state.py current, 2026-10-18)

The config's own inline `description`/comment text is stale and wrong — do
not trust it:

| api_base | config says | lumen_state says | status |
|---|---|---|---|
| `192.168.8.125` | "hathor judge box" | **ptah**.lan_ip (hathor.lan_ip is `192.168.8.172`) | `ptah.availability`: OFFLINE since 2026-10-16 18:10 PDT (no route to host + tailnet offline), but returned 2026-10-15 after a kernel-upgrade reboot — flaps, has a live precedent in the last 3 days. conf 0.88. |
| `192.168.8.135` | "ptah" | **sekhmet**.lan_ip | `sekhmet.status`: DOWN since 2026-10-11, LAN+tailnet both unreachable 7+ days, "needs physical/Frank check". conf 0.53 (the log entry itself flags low confidence). |

Both hosts are Station P1(/Pro) RK3399 SBCs provisioned 2026-10-05 for a
qwen2.5-coder serving pair (kb/ISSUE-LOG.md #1194, #1195). Live probes from
this BUILD sandbox on 2026-10-18 (network egress confirmed working — isis
`100.100.129.24:11434` answered 200 in 2ms) got connection failures on
**both** `192.168.8.125` and `192.168.8.135`, consistent with the history:
kb/ISSUE-LOG.md #1194 (wq#3541) could never get sekhmet to answer a health
probe at all; #1206 (wq#3496) hit the same `grant_missing_env` wall this
ticket hits — a BUILD worker cannot `fleet-exec run ptah|sekhmet` or read
`/home/lumen/agent/litellm_config.yaml` (outside the granted worktree roots)
to check live state directly.

So right now neither deployment answers. The ticket's own framing ("verify
which host is current, drop the dead entry") assumes one is live; the honest
state is "one flaps with a recent live window, the other has had none in
three weeks." **Decision: drop the `192.168.8.135` (sekhmet) deployment —
no live precedent since 2026-10-11 and a standing physical-check flag — and
keep `192.168.8.125` (ptah)**, which at least recovers on its own.  If ptah
degrades into a permanent outage like sekhmet's, that's a `remove_dead_rungs.py`
-style full removal for a follow-up ticket, not this one's call to make.

## The actual bug, independent of which host is "current"

Two `model_list` entries sharing one `model_name` are LiteLLM deployments —
normal load-balancing, not a problem by itself. The problem is
`router_settings.fallbacks` has **no entry for `qwen-local-small`**, so once
every deployment in the group is down, LiteLLM has nowhere left to go and the
raw connector error reaches the caller instead of a graceful fallback. That's
true whether one deployment is down or both.

## Fix

`dedupe_qwen_local_small.py`:
- drops the `qwen-local-small` deployment whose `api_base` names
  `192.168.8.135` (comment-preserving text surgery, same pattern as
  wq#3212/wq#4135 — never a YAML round-trip);
- adds `qwen-local-small: [lumen-local-judge]` to `router_settings.fallbacks`.
  `lumen-local-judge` (`qwen2.5-coder:7b` on the Lumen docker-gateway ollama,
  `172.17.0.1:11434`) is reused rather than invented: it's already live
  (confirmed reachable from this sandbox 2026-10-18), already
  `lumen/judge`'s own local fallback, and a local small-coder model is the
  closest-in-kind live rung to fail over to — same reasoning wq#4135 used to
  pick `openrouter-free` for the paid groups it touched.
- the rendered result is re-parsed and compared against an independently
  built expected config before any write (`build()`/`plan()`); refuses
  rather than writes a mismatch.

```
dedupe_qwen_local_small.py check CONFIG                                  # gate, can fail
dedupe_qwen_local_small.py apply CONFIG --write                          # defaults match the decision above
```

## Window plan

Same constraint as wq#3212/wq#4135: this BUILD worker cannot read or write
`/home/lumen/agent/litellm_config.yaml` (outside its granted roots) or touch
the `litellm` container. The actual apply is a fleet-window op:

```
python3 ops/wq4213-litellm-qwen-local-small-group/dedupe_qwen_local_small.py check /home/lumen/agent/litellm_config.yaml   # gate
python3 ops/wq4213-litellm-qwen-local-small-group/dedupe_qwen_local_small.py apply /home/lumen/agent/litellm_config.yaml --write
ops/wq4213-litellm-qwen-local-small-group/reload_and_verify.sh
```

`reload_and_verify.sh` probes `lumen-local-judge` live first (precheck —
retargeting onto a dead rung would be worse than the current bug), then
restarts `litellm` only if the running gateway doesn't already reflect the
edit, and verifies with wq#4135's `probe_rung.py` that `qwen-local-small`
answers (directly or via its new fallback) after.

## Rollback

Restore the timestamped `.bak-wq4213-<ts>` written next to the config by
`--write`, recreate/restart `litellm`.

## Tests

`python3 -m pytest ops/wq4213-litellm-qwen-local-small-group/tests/` — 8
cases: the can-fail gate (duplicate deployments + no fallback), that
`check` passes once fixed, that only the named host's deployment is
dropped, that the group comment and every other entry survive untouched,
that the new fallback doesn't disturb existing ones, idempotency on a
second pass, the plan-mismatch refusal, and that `--write` leaves a
byte-identical backup.
