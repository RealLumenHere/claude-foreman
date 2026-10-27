# wq#4677 research-prompt-adapter timer

Every 10 min (`research-prompt-adapter.timer`, user lumen) files each `*.txt` in
`/home/lumen/scratch/research_prompts/` as `wq add --lane research --pri 3 --title "RESEARCH: …"`
and moves the file to `queued/<wq-id>_<name>.txt`. Empty, stale (>30 d) and already-filed
prompts go to `expired/` or join their existing ticket; nothing is deleted. No provider calls.

Why: the research-prompt-queue inbox was advertised at every boot and drained by nothing since
2026-07 (wq#2741/3911/4626). Its old engine (`agent/lumen_deep_research_orchestrator.py`) is a
direct Groq/Gemini surface and stays DORMANT. The fleet already drains the wq research lane, so
the inbox now feeds that. Design: `design/research_prompt_adapter.md`.

Idempotence: queued-name check → content sha256 ledger
(`/home/lumen/logs/.research-prompt-adapter.state.json`) → wq's own DUPLICATE-title refusal
(parsed, file joins the named ticket). Bodies >8 KB send a 2000-char head + the path.
Per-run cap 10; `flock` so ticks never overlap.

This bundle owns the **unit and timer only**. The binary `/home/lumen/bin/research-prompt-adapter`
lands through the lumen-bin PR; the copy here is for `--verify` (`vfile`, superseded-aware).

Install: `bash install.sh` (sudo), or from a worker
`/home/lumen/bin/lumen-run start --root -- bash <abs path>/install.sh`.
Verify (read-only): `bash install.sh --verify`.
Tests (no real tickets, stub wq): `python3 tests/test_adapter.py`.
Dry run on the live inbox: `/home/lumen/bin/research-prompt-adapter --dry-run`.
Logs: `journalctl -u research-prompt-adapter.service -n 30 --output=cat`.

Rollback: `sudo systemctl disable --now research-prompt-adapter.timer; sudo rm
/etc/systemd/system/research-prompt-adapter.{service,timer}; sudo systemctl daemon-reload`.
Queued files keep their `<id>_` prefix so the tickets they produced stay traceable.

Related: `agent/github_scout.py` re-emission gate now also looks in `queued/` and `expired/`
(hand-patched with `.bak`, `agent/` is not a git repo); scout itself stays dormant (no unit).
Follow-up: `harness/fleet-exec-policy.yaml allow.read_dirs` lists `/home/lumen/research_prompts`
(wrong path) — fworkers cannot read the real inbox for >8 KB prompts until that is fixed by PR.
