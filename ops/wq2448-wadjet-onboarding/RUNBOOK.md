# wq#2448 — Wadjet: strip vault config, load LLM, join inference pool

Verified from a BUILD worker sandbox on 2026-09-24 (no ssh/fleet-exec reach to wadjet
itself — see "Sandbox limits" below). Every fact below is either a live command run
from this session or an existing repo record; nothing is guessed.

## Verified state (2026-09-24)

- **Reachability**: wadjet answers nothing on :11434 (offline) as of wq#2246
  (`/home/lumen/kb/ISSUE-LOG.md` row 546). `tools/macbench/nodes.yaml:11-17` records
  it as `MacPro6,1; no smart plug`, RAPL PL1 lowered 75W->45W on 2026-07-10 and never
  restored. This contradicts the ticket's "on P5 plug shared" — the plug assignment
  needs a live check, not a guess (see precheck failure below).
- **fleet-node add precheck fails at step 1/6** — ran directly against this branch's
  onboarding tool:
  `python3 harness/fleet_node.py add wadjet --family hathor-llama8b` ->
  `PRECHECK FAIL: node 'wadjet' has no plug_id in \`fleet-inv plugs\`` (nothing
  changed). Source: `harness/node_lifecycle.py:319-320`. This is the actual,
  mechanized precondition gate — it must pass before `add` will even read wadjet's
  hardware (`harness/node_lifecycle.py:326` `_check_hardware`, which runs after the
  plug/ip checks).
- **No node entry exists yet** for wadjet in `research/inference_nodes.yaml` — must
  be created by `fleet-node add`, not hand-edited (the file is generator-managed,
  `research/INFERENCE_NODE_REGISTRY.md` §3/§5).
- **CPU model is unverified** (`research/INVENTORY_RECONCILIATION.md:184-186`: Grade
  D, zero verified hardware facts, chassis only recorded as prose). The ticket's own
  ask ("check its CPU first - if 2697v2, full inference node; if weaker, still
  useful") cannot be answered without a live probe.
- **A live vault-HA failover mechanism on Lumen still targets wadjet.** Confirmed
  live and firing every 30s this session:
  `systemctl cat wadjet-ha-monitor.service` -> "Wadjet HA vault auto-promote monitor
  (probe Ma'at, failover to Wadjet)", `ExecStart=/home/lumen/bin/wadjet-ha-monitor.sh`.
  That script (root-owned, outside any repo, invoked every `OnUnitActiveSec=30`) will
  fence Ma'at and promote Wadjet + start its Vaultwarden + flip the
  `vault.lumennode.uk` Cloudflare tunnel origin to Wadjet on sustained Ma'at failure.
  This is precisely the "vault-HA config" Frank's ticket wants stripped, and it is
  the most likely source of "sentinel keeps flagging it's not vault primary" — the
  monitor still arms a promote-to-Wadjet path.
- **No Patroni/pg_role/replication-slot config exists in this repo** for wadjet
  (confirmed by grep across the tree) — per wq#325
  (`/home/lumen/kb/ISSUE-LOG.md` row 31), those artifacts (`ha.env`, monitor/failback
  scripts, sudoers, the Postgres service + replication slot on Ma'at) live only on
  the physical hosts. There is nothing to diff against in-repo; it is read/verified
  and removed live.

## Sandbox limits hit this session (why the physical steps aren't done here)

- `fleet-exec-policy.yaml`'s `ssh:` allowlist (`allow.ssh`) is
  `[lumen, isis, bes, anubis, maat, sobek, hathor]` — **wadjet is not listed**, and
  that file "lands ONLY by PR into foreman; a worker can never widen this file"
  (policy header). Widening it also requires the same live-verification step used
  for hathor ("verified 2026-09-08 before listing") — impossible while wadjet is
  offline.
- Disabling `wadjet-ha-monitor.timer`/`.service` needs `systemctl stop/disable/mask`,
  which the PreToolUse guard hook denies outright for build workers, and its files
  (`/home/lumen/ha/*`, the unit files themselves) sit outside every directory this
  worktree/session is scoped to.
- Bare `ssh wadjet@...` is not on the per-command Bash allowlist either
  (confirmed pattern, `/home/lumen/kb/ISSUE-LOG.md` row 546, wq#2246).

## Exact next steps (for whoever has live/root reach — Frank or a future session with a widened grant)

1. Confirm wadjet is powered and its actual plug (ticket says P5; `fleet-inv plugs`
   currently disagrees — reconcile before anything else).
2. `ssh wadjet@100.116.217.15 'sysctl -n machdep.cpu.brand_string; sysctl -n hw.ncpu; sysctl -n hw.memsize'`
   -> if it's an E5-2697v2, treat as a full inference node (per ticket); otherwise
   scope `--slots` down.
3. On wadjet: stop/disable/remove any `postgresql*`/Patroni units, drop its
   replication slot on Ma'at (the primary — `ops/wq853-fleet-maint-run/fleet_maint_run.py:65,462`),
   remove `ha.env`/failback scripts/sudoers entries (wq#325 pattern).
4. On Lumen: retire `wadjet-ha-monitor.timer`/`.service` (or repoint its failover
   target away from Wadjet) now that Wadjet no longer holds vault data — otherwise a
   future Ma'at outage will try to promote a node with no Vaultwarden data. Record
   the retirement in `docs/fleet/retired_services.yaml` once the units are
   confirmed `LoadState=not-found`, per that file's own contract (don't record it
   before the units are actually gone — audit tooling trusts this file literally).
5. Apply the Mac inference blueprint: `schedutil` governor via `lumen-power.service`
   (`research/FLEET_SERVICE_ATLAS.md:107`), GPU policy per
   `ops/wq2166-gpu-lowpower-ordering/` (**do not** disable/unbind GPUs — that was
   reverted, see `ops/wq2136-hathor-disable-gpus-unit/RUNBOOK.md:1-27` and
   `claude_memory.md:4053`), install ollama, pull a model sized to whatever CPU/RAM
   step 2 found.
6. Register: `python3 harness/fleet_node.py add wadjet --family <one of
   deepseek-v4-flash|qwen36-local|nephthys-qwen14b|hathor-llama8b>` (the ticket's
   literal "wadjet-<model>" name is not a legal governor prefix —
   `harness/node_lifecycle.py:293-297` — the model id will be `<family>.wadjet`; a
   genuinely new family needs the escape hatch, wq#2256). This single command
   regenerates `research/inference_nodes.yaml`, the litellm block
   (`ops/inference-registry/litellm_local_block.yaml`), and the key allowlist
   (gateway-side, `research/INFERENCE_NODE_REGISTRY.md:27,193`) — do not hand-edit
   any of those three.
7. Confirm serving: `fleet-node add`'s own dry-run diff plus a bench run
   (`tools/macbench/macbench.py run --node wadjet`, already wired per wq#2246) is
   the existing go/no-go gate (`harness/node_lifecycle.py:355` `bench_verdict`).

Blocked on: physical/live reach to wadjet (offline, not in the fleet-exec ssh
allowlist) and a root session on Lumen (to retire the live vault-HA monitor) —
both are physical/live-presence dependencies, not headless code work. See
`/home/lumen/kb/ISSUE-LOG.md` (wq#2448 entry) for the logged issue.
