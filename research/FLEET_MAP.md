# FLEET_MAP — which side of the wifi bridge each node is on

wq#1297: kb/tooling-findings-review.md §4 and kb/fleet-upgrade-plan-review.md §4.1
(Opus review) both flag the same gap — the bridge-safety policy
(ISSUE-LOG #26, `research/*` bridge-safe conventions) covers *monitors*
(no ICMP/ping, no router IPs, HTTP-only probes) but never named which physical
side of the router-to-router wifi bridge each fleet node sits on, or which
operations are heavy enough to matter. An agent-driven `ollama pull` (tens of
GB) or an unbounded container/git-lfs pull crossing that bridge is a bigger
outage risk than any probe, and was on no exclusion list.

This doc is the map; the enforcement is `harness/guard.sh`'s wq#1297 block
(ollama/podman/git-lfs pull denied from every cron/fworker Bash call — see
below) plus `harness/fleet-exec-policy.yaml`'s `run_readonly_cmds` allowlist,
which has never included any pull verb (fail-closed: a verb absent from the
allowlist is refused, not silently downgraded).

## Node roster and bridge side

Bridge side is a physical-topology fact, not something derivable from
`lumen_state.py` (no `bridge_side` fact exists there today) or from this repo.
Rows below are either confirmed from existing docs/policy, or marked
`UNKNOWN — needs Frank` per the operational protocol's instinct #2 (a physical
fact nobody else can confirm is a queued physical task, never a blocking gate
on the rest of this ticket).

| node   | role                                   | bridge side        | source |
|--------|----------------------------------------|---------------------|--------|
| lumen  | gateway / dispatcher / worker host     | house-primary side  | guard.sh only runs on `hostname == lumen` (harness/guard.sh:53-58); every fworker/cron Bash call this policy governs originates here |
| isis   | Ollama / GPU inference box              | UNKNOWN — needs Frank | fleet-exec-policy.yaml allow.restart lists `isis: [ollama]` (harness/fleet-exec-policy.yaml:89) confirming isis hosts the Ollama service, but not which side of the bridge it is physically on |
| thor   | hard-locked, no verb but read-only run  | UNKNOWN — needs Frank | never.nodes (harness/fleet-exec-policy.yaml:19); out of scope for pulls regardless — thor gets no exec verbs at all |
| maat   | restart-locked (`"*"` never-restart)    | UNKNOWN — needs Frank | harness/fleet-exec-policy.yaml:36-37 |
| sobek  | postgresql/cloudflared host             | UNKNOWN — needs Frank | harness/fleet-exec-policy.yaml:38-40 |
| bes    | copy target                             | UNKNOWN — needs Frank | harness/fleet-exec-policy.yaml:143 |

The `UNKNOWN` rows are a documentation gap, not an enforcement gap: no verb in
`fleet-exec-policy.yaml`'s `run_readonly_cmds`/`sudo_cmds`/`restart` allowlists
grants a pull of any kind on any node regardless of bridge side (fail-closed
default — see `harness/capability.py:236-265` `policy_grants()`), and
`guard.sh` denies pull verbs locally regardless of which node a command's argv
names. Filling in the physical side per node sharpens *why* the rule exists
for a future reader; it does not change what is allowed today.

## What is blocked, and where

1. **Local worker/cron Bash (`harness/guard.sh`, wq#1297 block).** Denies
   `ollama pull`, `podman pull`, and `git lfs pull|fetch` outright for any
   Bash call a foreman worker makes — this hook fires only for fworker/cron
   spawns (never an interactive human session), so "denied from cron/agents"
   is the whole population it sees. `docker pull` was already covered by the
   pre-existing "docker denied except ps/logs" rule. Tests:
   `harness/tests/test_guard.py` MUST_BLOCK entries for `ollama pull` /
   `podman pull` / `git lfs pull` / `git lfs fetch` (straight hit + `bash -c`
   + whitespace evasion), MUST_ALLOW entries for `ollama list` / `ollama show`
   to prove inspection verbs still work.
2. **Remote fleet-exec broker (`harness/fleet-exec-policy.yaml`).** No pull
   verb has ever appeared in `run_readonly_cmds`, `sudo_cmds`, or any `allow.*`
   list — the broker is allowlist-only (`policy_grants()` in
   `harness/capability.py`), so an absent verb is refused by construction, not
   by a special-case rule.

## The manual off-peak path

Large pulls stay something Frank runs by hand, off-peak, on the target node's
own console/ssh session — outside guard.sh's scope (guard.sh only governs
fworker/cron-spawned Bash) and outside fleet-exec's allowlist (which never
grants a pull verb to any grant tag). No new "helper" binary is introduced by
this ticket: the existing refusal *is* the guardrail, and the absence of an
automated bypass is what keeps the manual path manual.
