# wq#3301 — apply wq#3212's dead-rung removal out-of-band, resolve aq 2579/2583/2585

wq#3212 (merged `a48711d4`) already designed and shipped
`ops/wq3212-litellm-dead-rungs/remove_dead_rungs.py`, but deferred `--apply`
to the Mon 2026-10-05 `litellm-upgrade` window so the config edit rides the
same container recreate. By 2026-10-01 the three dead-rung rows
(`anthropic`, `host.docker.internal:8000`, `host.docker.internal:8091`) had
been open 30h+ unassigned (x62 since 2026-09-29 15:28 PDT) — past the point
where waiting another four days for the window is the right call.

The window coupling is not load-bearing for this specific fix: the
completion probe (`ops/wq3169-litellm-completion-probe/litellm-completion-probe`,
`CONFIG_PATH`) enumerates hosts by reading `litellm_config.yaml` directly,
not from the running container's loaded model state. Editing the file is
enough to drop the three dead hosts from the probe's set and clear their aq
rows; no container recreate is required for that. The model_list-consistency
reason for riding the window recreate (so the running gateway's in-memory
state matches the file) is untouched — it still rides Mon 2026-10-05.

## What ran

```
python3 ops/wq3212-litellm-dead-rungs/remove_dead_rungs.py --check   # gate, PASS
python3 ops/wq3212-litellm-dead-rungs/remove_dead_rungs.py --apply   # edit + aq resolve
python3 ops/wq3301-litellm-completion-probe-apply/verify_resolved.py # evidence, exit 0
```

- `/home/lumen/agent/litellm_config.yaml` edited, backup at
  `/home/lumen/agent/litellm_config.yaml.bak.20261001_101255_wq3212`.
- aq rows 2579 (`anthropic`), 2583 (`host.docker.internal:8000`), 2585
  (`host.docker.internal:8091`) — all `status=resolved`.
- Live probe re-run (`python3 /home/lumen/bin/litellm-completion-probe --print`):
  `probed 10 hosts, 2 failing: 100.98.74.87:11434,host.docker.internal:11434`
  — neither failing host is one of this ticket's three; they are a separate,
  pre-existing Ollama-port issue out of scope here.

## Verification

`ops/wq3301-litellm-completion-probe-apply/verify_resolved.py` re-checks both
facts (aq row status, dead hosts absent from the live probe's host set) and
exits non-zero if either regresses.
