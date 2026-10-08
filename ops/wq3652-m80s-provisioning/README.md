# wq#3652 — M80s provisioning: vPro AMT + fworker sandbox + NVMe isolation

Status per ticket step, and why each one landed where it did. This BUILD worker's
Bash sandbox has no `ssh`, no `docker` beyond `ps`/`logs`, no `sudo`, and no
`useradd`/`mkfs`/`mount` (`harness/worker-settings.json`'s allowlist); and
`harness/fleet-exec-policy.yaml` denies all of those on every node regardless of
any future grant (`never.sudo_argv_deny` permanently excludes `useradd`/`mkfs*`;
`never.nodes: [thor]` blocks everything but read-only `run`/`copy` on Thor,
independent of file edits — THOR_HARDLOCK enforces it in code too). No M80 node
is network-reachable yet either (checked `lumen_state.py` and
`fleet-exec-policy.yaml`: neither tracks one). So the steps below split cleanly
into "a BUILD worker can author this" and "someone with console/root access has
to run it" — this PR does all of the former and queues the rest as follow-up wq
tickets rather than leaving them as unactioned prose (full-autonomy instinct 3:
queue a physical/privileged dependency, never let it gate the rest).

| step | what | this PR | follow-up |
|---|---|---|---|
| 1 | MEBx/vPro AMT config (Ctrl+P at boot) | — | physical/console (chicken-and-egg: no remote KVM exists before AMT is configured) |
| 2 | MeshCommander on Thor | — | privileged session, Thor hardlock means fleet-exec can never do this either |
| 3a | Ubuntu install on nvme0n1 | — | physical/console |
| 3b | format nvme1n1 as /var/fworker | `provision-fworker.sh` | privileged/root session runs it |
| 4 | fworker user (nologin, owns /var/fworker only) | `provision-fworker.sh` | privileged/root session runs it |
| 5 | systemd sandbox | `fworker-sandbox@.service` + `fworker_sandbox_slot.sh`, hardening verified by `test_fworker_sandbox_hardening.py` | privileged/root session installs + starts it |
| 6 | migrate Claude auth (~/.claude/) from Lumen | — | privileged session (copies a live credential — same class as a key mint, instinct 4/3) |
| 7 | point fworker env at Lumen LiteLLM+wq | `fworker.env.example` + `wq-remote` | privileged session fills in the real LiteLLM key |

## Note on "Full systemd unit in research docs"

Searched `research/`, `kb/`, and this worktree for an existing M80s/fworker-
sandbox systemd spec (grepped for `m80`, `nvme1n1`, `fworker.*sandbox`,
`vpro`, `meshcommander`, `ProtectHome=yes`) and found none — only unrelated hits
on the existing Lumen-side `fworker` dispatcher (`harness/fworker.py`). Logged as
ISSUE-LOG row (see `kb/ISSUE-LOG.md`); the unit in this directory was built from
the ticket's own literal spec plus this repo's existing hardening conventions
(`harness/systemd/lumen-despag.service`, `token-engine/systemd/token-engine.service`,
`harness/systemd/wq-worker@.service`), not from a doc that could not be located.

## Design notes worth citing

- **wq-remote exists because wq has no remote-DB mode.** `/home/lumen/bin/wq:101`
  resolves its queue to a local sqlite file (`WQ_DB`); mounting that over the
  network for concurrent access risks corruption. `fworker.env.example` points
  `FOREMAN_WQ_BIN` at `wq-remote`, an ssh-wrapped shim, instead of letting the
  M80 node touch `queue.db` directly.
- **The IPAddressAllow/Deny design is the opposite problem from the one that
  bit despag.** `harness/systemd/lumen-despag.service:44-48` documents wq#1931:
  a blanket `IPAddressDeny=any` filtered ALL egress for that unit's cgroup,
  including the spawned `claude` child's call to `api.anthropic.com`, and the
  fix there was to drop the filter entirely. That doesn't apply here because
  fworker's architecture is different: step 7 routes every model call through
  Lumen's LiteLLM gateway, not directly to `api.anthropic.com`, so the single
  allowed egress target (Lumen's /32) is also the only one fworker ever needs —
  unlike despag's `claude` CLI child, which talks to Anthropic directly.
- **`fworker-sandbox@.service` is a templated oneshot, not a persistent daemon**,
  because `harness/fworker.py`'s only long-running-unit-shaped subcommand is
  `run --ticket ID --repo PATH` (claim one ticket, drive it to completion, exit —
  `harness/fworker.py:4879`); there is no daemon/serve mode to wrap. This mirrors
  `harness/systemd/wq-worker@.service` (wq#1182) exactly, substituting a
  `/var/fworker`-rooted slot script because that unit's `wq_worker_slot.sh`
  hardcodes `/home/lumen/foreman`, which `ProtectHome=yes` makes unreachable here.
- **fleet-exec-policy.yaml is deliberately left unedited by this PR.** Onboarding
  an M80 node into `allow.ssh` / `allow.restart: fworker-sandbox@*` /
  `copy_dirs` is real follow-up work once a node exists and has a name — adding
  entries for a node with no confirmed hostname would be guessing a value this
  repo's own doctrine forbids. The follow-up ticket below carries that checklist.

## Follow-up tickets filed

Filed via `wq add` (full-autonomy instinct 3 — physical/privileged dependencies
are queued, not left as a gate):

- wq#3703 — physical/console: MEBx/vPro AMT config (step 1) + Ubuntu install on
  nvme0n1 (step 3a).
- wq#3704 — privileged session, Thor hardlock applies: MeshCommander on Thor
  (step 2). Exact command (fleet-exec cannot reach it; `never.nodes: [thor]`
  blocks it there permanently): `docker run -d --name meshcommander --restart
  unless-stopped -p 3000:3000 ylianst/meshcommander`.
- wq#3705 — privileged session, depends on wq#3703: run `provision-fworker.sh`,
  install/start `fworker-sandbox@1`, migrate `~/.claude/` from Lumen (step 6),
  fill in the real LiteLLM key in `/var/fworker/fworker.env`, and onboard the
  M80 node's real hostname into `harness/fleet-exec-policy.yaml` (`allow.ssh`,
  `copy_dirs`, `allow.restart: fworker-sandbox@*`) by reviewed PR.
