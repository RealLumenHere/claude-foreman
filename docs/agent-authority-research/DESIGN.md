# DESIGN: fewer false blocks via a hold state (capability-denied) and an ops-apply daemon (root)

wq#4758. Design only; no code in this ticket. Build tickets are in §6.
Prior design this builds on: `design/orchestrator_verify_lane.md` (wq#4681). Its option A
(`verify-probe` lane, landed f376558c) and option B (`ops-apply` *lane profile*, wq#4688,
soak-blocked until 2026-11-03) stay. This doc adds the two mechanisms underneath them.

## 0. Inputs, and one gap

- **Research synthesis not read directly.** `/home/lumen/docs/agent-authority-research/`
  (SYNTHESIS.md + reports 1/2/3) is outside this worker's add_dirs: `ls` was refused with
  "may only list files in the allowed working directories". I did not route around the wall.
  This design works from the ticket detail, which carries the synthesis's decisions (25/67
  addressable: 14 capability-denied + 11 root; Pilot 1 = native defer vs Squidbrake; Pilot 2
  = systemd ops-apply daemon; do not pilot report 3's sudo approval plugin), and from primary
  sources I re-fetched on 2026-10-28 (cited inline). ISSUE-LOG row 1627. The miss is itself a
  Pilot-1 path-hold case (§2.4).
- Per-ticket lists of the 14 + 11 live in the synthesis. Build ticket B1-T0 (§6) replays them;
  this doc does not restate counts it could not check.

## 1. Invariants (both pilots)

Both pilots are **additive and sit above** everything that enforces today. None of these is
edited, relaxed or bypassed:

| Layer | Where | Stays |
|---|---|---|
| allowlist / deny list | `harness/worker-settings.json` (deny `:105-130`) | deny-by-default; `sudo *`, `docker *` deny unchanged |
| guard.sh | PreToolUse `Bash\|Read\|Edit\|Write` (`worker-settings.json:136-145`) | every deny wins |
| action_gate | PreToolUse `Bash\|Edit\|Write\|mcp__.*` (`:146-154`) | every deny wins, hardware RED floor (7e03af4c) |
| fleet-exec policy | `harness/fleet-exec-policy.yaml` `never.*` (`:15-55`), `THOR_HARDLOCK` | `never` beats any grant or approval |
| `never.sudo_argv_deny` | `fleet-exec-policy.yaml:44-55` | re-checked by the ops-apply daemon at apply time (§3.4) |
| capability_lint hard denies | `DIR_DENY_EXACT`, `DIR_DENY_PREFIXES`, guard credential deny | never held, never approvable |
| verify / critic gate | judgment.file_verify_pass, stop_gate | unchanged; Pilot-2 applies still get a [verify] |

Rule for both pilots: **an approval can only turn a would-be capability denial into an allow.
It can never turn a policy deny into anything.** Anything a lower layer denies stays denied,
and the hold mechanism never sees it as holdable.

## 2. Pilot 1: hold state for capability-denied calls

### 2.1 Decision: native PreToolUse `defer` leads; Squidbrake is not piloted for headless

**Chosen: native `defer`** (report 2). Reasons:

1. It does exactly what the hold needs, with no new dependency. From the hooks reference
   (https://code.claude.com/docs/en/hooks, fetched 2026-10-28): a PreToolUse hook may return
   `permissionDecision: "defer"`. It is honored only with `-p`, which every fworker uses
   (`harness/fworker.py:2595`). The tool does not run, the turn ends with
   `stop_reason: "tool_deferred"`, and the result carries `deferred_tool_use` {id, name,
   input}. `claude -p --resume <session-id>` re-fires PreToolUse for **the same call**, and an
   `allow` then runs it. There is no timeout. Sessions are kept until the `cleanupPeriodDays`
   sweep (30 days by default).
2. Squidbrake is a human review-hold for interactive sessions. Our own pilot doc already scopes
   it out of headless fworkers ("a hold would deadlock",
   `docs/hardware-re-safety/squidbrake/README.md:16-19`).
3. Licensing: Apache 2.0, "free for one person; more than one person, phone approvals,
   Slack/Teams/email and the audit export are part of the Team plan"
   (https://raw.githubusercontent.com/batrapulkit/squidbrake/main/README.md line 30, fetched
   2026-10-28). The parts that would matter to a fleet (phone approval, audit export) are paid.
   The free part adds a gateway process to every tool call to buy a hold that `defer` already
   gives us.

Squidbrake remains the **comparison**: its interactive observe-mode pilot (wq#4750) continues
unchanged, and B1-T5 (§6) compares the two hold logs side by side. If Squidbrake ships a
headless mode that is cheaper than ours, revisit.

Documented limits of `defer` that the design must absorb (same source):
- It is honored only when Claude makes **a single tool call in the turn**. With parallel calls
  it is "ignored with a warning and the tool proceeds through the normal permission flow". In a
  `-p` worker that flow is the allowlist, so the call is denied exactly as today. That is fail
  closed (§2.7 F2).
- If you resume **with a prompt**, the deferred call is dropped. fworker always passes a prompt
  today (`fworker.py:2595`), so the resume path must pass none (§2.3 step 6). The docs allow
  `-p` without a prompt when "you resume a session with a deferred tool call".
- If the tool is unavailable on resume, you get `stop_reason: "tool_deferred_unavailable"` and
  the hook does not fire (F7).

### 2.2 What is holdable (closed list)

The hold hook (`harness/hold_gate.py`, new) defers **only** these classes. Everything else
returns no decision and falls through to today's flow.

| Class | Matches | Grant it materializes |
|---|---|---|
| H1 path-read | `Read`/`Grep`/`Glob`, or an allow-listed read-only Bash verb (`ls`, `cat`, `grep`, `stat`, `head`, `tail`), on a path outside the worker's add_dirs **and** not rejected by `capability_lint._deny_reason` or the guard credential deny | read-only bind and add-dir of the *directory* (§2.4) |
| H2 repo-read | a read of another managed repo (`repo_sweep.managed_repos()`, `harness/repo_sweep.py:92`) not covered by the ticket's grant | `needs:repo(<key>@<sha>)` broker grant (§2.5) |

**Not holdable, ever:** writes of any kind; anything under `DIR_DENY_EXACT`/`DIR_DENY_PREFIXES`
or a credential path; ssh to thor; any `never.*` row; Bash verbs that are not on the allowlist
(those are allowlist proposals made by PR, not runtime approvals); and anything root (that is
Pilot 2).

**Order independence.** The docs do not state precedence when several PreToolUse hooks return
different decisions (searched the hooks reference for "precedence" and "multiple hooks";
UNVERIFIED). So `hold_gate.py` does not rely on it. It first runs guard.sh's classifier and
`action_gate.py` in-process on the same payload, and defers only if both would allow. B1-T1
adds a probe that sends a guard-denied call and asserts the result is a deny, not a defer.

### 2.3 Flow: hold, approve, same-session resume

1. A worker issues a holdable call. `hold_gate.py` checks for an approval matching the call's
   fingerprint (§2.6). If there is none, it writes a `grant_requests` row (`state=pending`)
   and returns `defer`.
2. The CLI ends the turn with `stop_reason: tool_deferred`. fworker's stream parser
   (`fworker.py:3228` run loop) reads `deferred_tool_use` and `session_id`, then calls
   `wq await-grant <ticket> --req <id> --session <sid>`.
3. wq sets ticket `status=awaiting_grant` and releases the lease, so no slot is held. It stores
   `resume_session` and `grant_req` on the row. fworker exits with a new
   `EXIT_AWAITING_GRANT` code: **uncharged** (it is not an attempt failure) and no refill kick.
   It sends an ntfy carrying `wq grant approve|deny <req>`.
4. An approver acts: the orchestrator under full autonomy, or Frank. The command is
   `wq grant approve <req>` (or `deny`, with a reason). An approval is **one-shot**, bound to
   the fingerprint, and has a TTL (default 24 h). If the identity issuing it is the ticket's own
   worker session, it is refused (`wq whoami` resolves the session; worker sessions carry
   `fworker-wq<N>` holder ids).
5. Approve sets the ticket back to `open` with a `resume_session`. Deny sets it to `blocked`
   with `block_reason="grant denied: <reason>"`, and the worker never sees the call run.
6. The dispatcher claims the ticket (new lease) and spawns fworker with
   `--resume <sid>` and **an empty prompt**. The spawn materializes the approved grant: the
   add-dir and `BindReadOnlyPaths` from §2.4 for H1, or a broker grant line for H2.
7. On resume the CLI re-fires PreToolUse for the pending call. `hold_gate.py` finds the
   approval, **consumes** it (atomic `UPDATE ... SET consumed_at=now WHERE consumed_at IS NULL
   AND fingerprint=?`, one row or deny) and returns `allow`. The tool runs and the session
   continues with its full context.

A materialized grant lives for the life of **that unit** (one lease). Later attempts or
re-claims do not inherit it. If the same path is needed again after the lease ends, that is a
new request.

### 2.4 Path access: a real OS mechanism, not just a hook allow

The ticket's key constraint is that a hook `allow` does not grant OS path access. Today's
facts:

- Workers run as `lumen-job-fworker-wq<N>-<epoch>.service`, a system transient unit with
  `--uid=lumen`, created by `sudo -n systemd-run` in `/home/lumen/bin/lumen-run:69,93-112`.
  The only properties set are slice, OOM, PATH, Memory/CPU/Tasks limits. **There is no
  filesystem confinement.** Today's path wall is Claude Code's working-directory restriction
  (`--add-dir`, `fworker.py:2604`) plus guard.sh. Neither is an OS boundary, and neither can be
  widened by a hook allow.
- Therefore a path grant must change the **spawn**, which is why it is materialized at step 6
  rather than inside the hook.

Mechanism for H1:

1. **Claude layer.** `build_claude_cmd` appends `--add-dir <granted_dir>` for each approved,
   unconsumed-at-spawn H1 grant on the resume spawn.
2. **OS layer.** `lumen-run` gains `--bind-ro <path>` (repeatable). It renders
   `--property=BindReadOnlyPaths=<path>` with **no `-` prefix**. Per the local `man
   systemd.exec` ("BindPaths=, BindReadOnlyPaths=": a `-` prefix means the mount is ignored
   when its source is missing), dropping the prefix means a missing or renamed source **fails
   the unit start**. The result is that the granted path is read-only inside the worker's mount
   namespace, whatever tool or allowlist bug tries to write it.
3. **Teardown with the lease.** The man page says these bind mounts "are specific to the unit,
   and are not visible in the host's mount table". The namespace dies when the unit stops, and
   it stops on done/release/block, on lease reap (`wq reap`), and on wall-clock kill. Nothing
   has to be unmounted, so nothing can be leaked by a crashed teardown.
4. **Canonicalization before approval.** The request stores `realpath()` of the target. The
   approval binds that canonical path. At spawn, lumen-run re-resolves it and refuses on
   mismatch, which covers a symlink swapped between approval and spawn. The bound unit is the
   *directory* the request named, never a parent of it, and never a path that
   `capability_lint._deny_reason` rejects (re-run at spawn).

**Honest limit.** On today's unconfined unit, the bind adds two things: Claude-level reach and
OS-enforced read-only on the granted path. It does **not** OS-deny everything else, because uid
lumen can already read its home. Making the base view deny-by-default is a separate
hardening step, **B1-T6**, kept out of the pilot because it changes every worker:
`ProtectHome=tmpfs`, then `BindPaths=` worktree/run dir and `BindReadOnlyPaths=` add_dirs/bin,
plus `InaccessiblePaths=` for credential prefixes. The man page notes that binds under `/home`
need `ProtectHome=tmpfs` rather than `yes` (systemd.exec, near the BindPaths entry). Once
B1-T6 lands, the H1 bind becomes the *only* way a granted path appears in the worker's view.

### 2.5 Read-only repo broker for verify workers (H2 and the default verify path)

Problem: `verify-pass`/`verify-probe` workers today get a repo's **primary tree** as an add_dir
(`fworker.py:2501-2502`). That tree is mutable, may contain untracked `.env`/state files, and
may have moved since the PR shipped.

Broker: a new `/home/lumen/bin/repo-read`, allow-listed for the verify archetypes only.

- `repo-read ls|show|grep|log <repo-key> <sha> [path|pattern]` maps to `git -C <root> ls-tree`,
  `show <sha>:<path>`, `grep <pattern> <sha> -- <path>`, and `log -n50 <sha>`.
- **Canonical root.** `<repo-key>` must be a key of `managed_repos()`. The root is that entry's
  `realpath`, never a worker-supplied path, and the worker cannot name an arbitrary directory.
- **Immutable commit.** `<sha>` must be a full 40-hex object id that `git cat-file -e
  <sha>^{commit}` accepts. Branch names, `HEAD` and abbreviations are refused, because a ref is
  mutable and an abbreviation is ambiguous.
- **Only git objects are read**, never the working tree. Untracked and ignored files (`.env`,
  `context/`, `*.db`) are unreachable by construction. Output is piped through
  `masking.PATTERNS` before it is printed.
- **Grant.** `needs:repo(<key>@<sha>)`. It is derived at filing time by
  `judgment.file_verify_pass` from the shipped PR's merge sha, which extends wq#4681 §5.1's
  needs derivation. The broker checks the per-ticket grant file (same `FOREMAN_GRANT_FILE`/SHA
  pattern as fleet-exec) and denies a key@sha it was not granted. An ungranted read becomes an
  H2 hold, not an error.
- Read-only by construction: no write verb exists, and git is run with `GIT_OPTIONAL_LOCKS=0`
  and `-c core.hooksPath=/dev/null` so even reads trigger no hooks or index writes.

### 2.6 Request fingerprint

`fp = sha256(canonical_json({ticket, session_id, tool_use_id, tool_name, input_canonical,
class, target_realpath}))`. `input_canonical` is the hook's `tool_input` with keys sorted,
paths realpath'd and whitespace normalized in Bash commands.

`tool_use_id` binds the approval to *the one pending call*. On resume the CLI re-fires the
**same** call, so the fingerprint is reproducible, and an approval cannot be replayed onto a
different call or session. `deferred_tool_use.id` from the result frame is cross-checked
against the stored `tool_use_id` at step 2. If they differ, fworker records
`HOLD_FRAME_MISMATCH` and treats the attempt as today's CAPABILITY_DENIED.

wq schema (new table in the wq sqlite, not a reuse of `wq hold`, which is a soft intent marker
per wq --help "wq#1461"):
`grant_requests(id, ticket_id, session_id, tool_use_id, class, target, fingerprint UNIQUE,
state[pending|approved|denied|expired|consumed], requested_at, decided_by, decided_at, reason,
expires_at, consumed_at, consumed_lease)`. New ticket status: `awaiting_grant`. The dispatcher
never claims it, and `wq reap` expires its stale requests.

### 2.7 Pilot 1 fail-closed story

| # | Failure | Result |
|---|---|---|
| F1 | hold_gate.py crashes, times out, or wq db is unreadable | no `defer` is emitted. The non-blocking hook error leaves the allowlist flow in charge, so the call is **denied** as today (CAPABILITY_DENIED) |
| F2 | `defer` ignored (parallel tool calls) | normal permission flow, so **denied**. hold_gate also adds `permissionDecisionReason` text telling the model to re-issue that read alone |
| F3 | a lower layer denies | hold_gate pre-runs guard and action_gate and returns **deny**. Never held |
| F4 | approval missing, expired, consumed, or fingerprint differs | `defer` again on a no-prompt resume. If the request is already denied or expired: **deny** and the ticket goes `blocked` |
| F5 | two resumes race for one approval | the atomic consume lets exactly one win. The other gets **deny** |
| F6 | bind source missing or re-resolved realpath differs at spawn | unit start **fails** (no `-` prefix) or lumen-run refuses. The grant expires, the ticket goes `blocked: grant target changed` |
| F7 | session pruned (30-day sweep) or `tool_deferred_unavailable` | fworker records `HOLD_RESUME_LOST`, clears `resume_session` and returns the ticket to `open` for a fresh attempt. The approval is expired, not reused |
| F8 | approver is the requesting worker | `wq grant approve` refuses (`self-approval`) |
| F9 | resume spawned with a prompt by mistake | the CLI drops the deferred call. A B1-T2 test asserts the resume argv has no prompt, and fworker refuses to spawn a resume with one |
| F10 | lease reaped mid-run | the unit stops and the bind namespace dies with it (§2.4.3). The grant is not carried forward |

## 3. Pilot 2: ops-apply daemon for root operations

### 3.1 Why not report 3's sudo approval plugin

Report 3's mechanism is a sudo Python approval plugin that blocks inside `check()`. **It is not
piloted**, as the ticket's orchestrator flag says:
1. it puts the approval wait inside the setuid sudo process, so a held sudo is a hung
   privileged process holding the worker's tty/lease;
2. sudo approves an **argv**, so the agent still writes the command. There is no typed
   parameter or recipe boundary, and argv allowlists are bypassable (the `-c`/`sh` class
   `fleet-exec-policy.yaml:198` exists to stop);
3. it requires granting fworker a sudo rule at all, while `worker-settings.json` denies
   `sudo *`, and this design keeps that deny.

What follows is report 1+2's position: **the agent never gets root. It submits parameters to a
fixed recipe, and a root service that does not trust the agent revalidates everything at apply
time.**

### 3.2 Topology (pilot: one op on one node)

```
fworker (uid lumen, unit lumen-job-fworker-wqN-*, Lumen)
  | ops-apply submit <manifest.json>        (allow-listed client, no root)
  v
/run/ops-apply/gate.sock  -- socket-activated ops-apply-gate.service (root, Lumen)
  | validates (§3.4) and signs the manifest digest with a root-only key
  v  ssh, dedicated key, authorized_keys: restrict,command="/usr/local/sbin/ops-apply-node"
ops-apply-node (target node) -> /run/ops-apply/node.sock -- ops-apply-node.service (root)
  | re-validates (§3.4), runs the recipe, post-checks, rolls back
```

**Pilot node: `bes`**, recipe `cloudflared.ingress.upsert`. Why bes: its cloudflared carries an
ingress on an existing tunnel (ISSUE-LOG row 742, wq#2608), and `cloudflared` is **not** in
bes's `never.restart` row, whereas it is for lumen and sobek (`fleet-exec-policy.yaml:28-40`).
The pilot never targets lumen, sobek, maat or thor.

**B2-T0 precondition:** a `verify-probe` read of bes's `/etc/cloudflared/config.yml` (via
fleet-exec run, read-only) must show that the tunnel fronts neither Vault nor the Commander
path. If it fronts either, fall back to `anubis` (own tunnel, wq#2607) under the same check. If
neither passes, B2-T0 blocks with the finding. Instinct 6 keeps Vault, cloudflared and the
Commander path out of unattended change; a non-crown-jewel tunnel changed by a detached
systemd service with one-shot approval and auto-rollback is the attended, detached form that
instinct asks for.

### 3.3 Recipe contract

A recipe is a **root-owned file in a fixed catalog**, `/etc/ops-apply/recipes/<id>.v<N>.py`.
`catalog.json` pins `{id, version, sha256, nodes[], unit, param_schema}`. The agent supplies
**parameters only**. There is no field anywhere in the manifest that is executed, templated
into a shell, or used as a path.

Manifest (what the worker submits):
```json
{"recipe": "cloudflared.ingress.upsert", "recipe_version": 1, "node": "bes",
 "ticket": 4xxx, "params": {"action": "upsert", "hostname": "x.example.tld",
 "port": 8123}, "reason": "<= 200 chars, logged only",
 "digest": "sha256:<canonical_json(manifest minus digest)>"}
```

`cloudflared.ingress.upsert` v1:
- **Param schema** (JSON Schema, `additionalProperties: false`). `action` is an enum of
  `upsert` or `remove`. `hostname` is an FQDN regex and must sit under a zone listed in the
  node's catalog entry. `port` is an integer from 1024 to 65535; the recipe itself renders
  `service: http://127.0.0.1:<port>`, so the agent never writes a URL. An optional `path`
  matches `^/[A-Za-z0-9/_.-]{0,64}$`. Nothing else is accepted.
- **Target** is fixed in the catalog (`/etc/cloudflared/config.yml`, unit `cloudflared`). It is
  never a parameter.
- **Steps:**
  1. Read the target and keep its sha256.
  2. Parse it as YAML and apply the op to `ingress[]` in memory.
  3. **File-type-aware diff validation.** The YAML is parsed and checked by structure, not as
     a text diff:
     - every top-level key except `ingress` is semantically equal before and after
       (`tunnel`, `credentials-file`, `originRequest` and so on cannot change);
     - exactly one ingress rule is added, changed or removed;
     - the final rule is still the hostname-less catch-all `service: http_status:404`;
     - there are no duplicate `hostname`+`path` pairs;
     - the changed rule's service is a loopback URL.
  4. Write the candidate to `RuntimeDirectory` and run
     `cloudflared tunnel --config <candidate> ingress validate`. Then run
     `cloudflared tunnel --config <candidate> ingress rule https://<hostname><path>`, which must
     resolve to the new rule. Both commands are documented at
     https://developers.cloudflare.com/tunnel/features/locally-managed-tunnels/configuration-file/
     (fetched 2026-10-28: "Validate ingress rules ... cloudflared tunnel ingress validate").
  5. Back up the live file to `/var/lib/ops-apply/backups/<req>/` with its sha256, owner and
     mode.
  6. Re-check that the live sha256 still equals the one from step 1. If it changed, abort
     (TOCTOU).
  7. Atomic `rename()` of the candidate, with owner and mode preserved.
  8. `systemctl restart cloudflared`. The daemon is itself a systemd service, so the restart is
     detached from any worker.
  9. **Post-check** within 60 s: `systemctl is-active cloudflared`, then
     `cloudflared tunnel ingress rule` against the live file, then an HTTPS GET to the hostname
     must not return Cloudflare error 1033/502. `remove` expects 404.
  10. On any post-check failure: restore the backup, restart, re-run the post-check against the
      **old** expectations, and report `ROLLED_BACK`. If the rollback post-check also fails,
      report `ROLLBACK_FAILED`, send an ntfy at pri 5, and freeze the recipe on that node
      (catalog `frozen: true` is written by the daemon). Only a human or orchestrator un-freezes
      it, by PR.
- **Inverse.** Every apply records its inverse manifest (upsert of a new host maps to remove;
  upsert of an existing host maps to upsert with the old service). `ops-apply revert <req>` is
  a normal submit of that inverse and goes through the same gates.
- **Argv rendered by the recipe.** It is a fixed list. Before every `exec`, it is checked
  against `never.sudo_argv_deny` (§3.4 V9).

### 3.4 Apply-time revalidation (gate does V1-V10; node re-does V3-V10)

Checks run in order. The first failure refuses with its code, and nothing is staged on a
refusal.

| # | Check | How | Code |
|---|---|---|---|
| V1 | identity | `SO_PEERCRED` pid, then `/proc/<pid>/cgroup` must sit under `lumen-workers.slice/lumen-job-fworker-wq<N>-*.service`. The kernel assigns the ticket id, so the manifest's `ticket` must equal N | `OPS_IDENTITY` |
| V2 | lease | wq: ticket N is `claimed`, in the `ops-apply` lane (wq#4688 profile), lease unexpired, holder = that unit | `OPS_LEASE` |
| V3 | host | `manifest.node` = this node's pinned hostname and machine-id. Node not in `never.nodes`. `THOR_HARDLOCK` re-asserted in code | `OPS_HOST` |
| V4 | op allowlist | recipe id+version in `catalog.json` for this node, not `frozen` | `OPS_NOT_ALLOWED` |
| V5 | param schema | JSON Schema, `additionalProperties: false` | `OPS_PARAMS` |
| V6 | policy | root-owned copy of `fleet-exec-policy.yaml` at `/etc/ops-apply/policy.yaml`, pinned by sha in catalog.json and installed from foreman main by the B2 install bundle. The lumen-writable file in the repo is never read. The recipe's `unit` must not match `never.restart[node]` | `OPS_POLICY` |
| V7 | approval | a one-shot approval for **this digest**, unexpired, unconsumed. The approver's peer cgroup is **not** under `lumen-workers.slice` (a worker cannot approve itself or another worker). Consumed atomically | `OPS_APPROVAL` |
| V8 | signature | gate: recipe file sha256 equals the catalog pin. Node: the gate's `ssh-keygen -Y verify -n ops-apply@lumen` over the digest, against `/etc/ops-apply/allowed_signers`. The signing key is root-only 0600 in `/etc/ops-apply/`, so uid lumen has no OS read path to it | `OPS_SIGNATURE` |
| V9 | `never.sudo_argv_deny` | every argv the recipe will exec is rendered first and checked: basename of argv[0], plus the space-joined prefix for the `tailscale up*`/`systemctl mask*` rows. Re-checked immediately before each `exec` | `OPS_ARGV_DENY` |
| V10 | digest | recompute canonical JSON sha256; it must equal `manifest.digest`, which must equal the approved digest | `OPS_DIGEST` |

Approval path: the worker submits, the gate runs V1-V6, V9 and V10, and the request goes
`STAGED` in `/var/lib/ops-apply/queue/wq<N>/<req>.json` (root-owned; the worker gets only the
req id back). An ntfy goes out. The approver runs `ops-apply approve <req>` from a non-worker
cgroup, after which the gate runs V7 and V8, signs, and forwards to the node. The worker polls
`ops-apply status <req>` and records the result in its report. The [verify] pass that judgment
files afterwards re-probes the live effect.

### 3.5 Unit hardening

`ops-apply-gate.socket` / `ops-apply-node.socket`: `ListenStream=/run/ops-apply/<name>.sock`,
`SocketMode=0660`, `SocketGroup=ops-apply-clients` (lumen on the gate; the forced-command ssh
user on the node), `Accept=no`.

`ops-apply-*.service`, `Type=simple`, root:
```
NoNewPrivileges=yes
CapabilityBoundingSet=CAP_CHOWN CAP_FOWNER CAP_DAC_OVERRIDE   # node; gate: (empty)
AmbientCapabilities=
ProtectSystem=strict
ReadWritePaths=/var/lib/ops-apply /etc/cloudflared           # node; gate: /var/lib/ops-apply
ProtectHome=yes
PrivateTmp=yes
RuntimeDirectory=ops-apply-work
StateDirectory=ops-apply
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
SystemCallFilter=@system-service
SystemCallErrorNumber=EPERM
LockPersonality=yes
MemoryDenyWriteExecute=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
```

The gate needs no capabilities. Root is used only to own the signing key and the queue. The
node's `systemctl restart` goes over D-Bus as uid 0, which is unaffected by the bounding set.
B2-T2 must still prove that by test on the node's systemd before claiming it. Any hardening
directive that breaks `cloudflared ingress validate` is relaxed **per directive, with the test
output cited**, never wholesale.

OPA/Rego: optional later, as a pure-function policy step inside V6. Not in the pilot.

### 3.6 Pilot 2 fail-closed story

| # | Failure | Result |
|---|---|---|
| G1 | socket or service down, or ssh to node fails | `submit`/forward returns an error, nothing changes. The worker blocks its ticket with `ops-apply unavailable`. There is **no fallback to any other root path** (sudo stays denied) |
| G2 | any V-check fails | refusal code. The request is not staged, or it is marked `REFUSED`. Nothing is applied |
| G3 | policy copy unreadable or sha mismatch | the whole daemon refuses every request (`OPS_POLICY`), same as fleet-exec's `policy_unreadable` rule |
| G4 | approval never arrives | `STAGED` expires after a TTL (default 24 h) and becomes `EXPIRED`. The ticket goes blocked |
| G5 | live file changed between read and rename | step 6 aborts with `OPS_TOCTOU` |
| G6 | post-check fails | auto-rollback (step 10). If that fails, the recipe is frozen and a pri-5 ntfy goes out |
| G7 | daemon crashes mid-apply | on start, the service scans `queue/*` for `APPLYING` records. For each one it compares the live sha with the backup and pre-apply sha, restores the backup if the file is neither pre nor intended state, and then marks the request `INTERRUPTED`. It never resumes an apply |
| G8 | a worker forges cgroup/identity | not possible from uid lumen (the kernel assigns cgroups). A forged `ticket` field fails the V1 equality |
| G9 | a worker approves its own request | V7 rejects any approver peer inside `lumen-workers.slice` |
| G10 | recipe file edited | the V8 sha pin fails |

### 3.7 Relation to wq#4688 (ops-apply lane profile)

wq#4688 is the **lane**: who may be dispatched to do an ops change, with precheck, backup cite
and critic gate. This daemon is the **only mechanism** that lane gets for root. 4688's
"`fleet-exec restart` + `git apply`" path stays for non-root staged diffs. V2 requires the
ticket to be in that lane, so B2's live enable depends on 4688. 4688 remains soak-blocked until
2026-11-03 (ISSUE-LOG row 1605); B2's build and test tickets do not need the soak, but B2-T3
(live enable) does.

## 4. Success measure

- Pilot 1: of the synthesis's 14 capability-denied tickets, replayed via B1-T0, count those that
  close with `awaiting_grant` → approve → resume, with no orchestrator hand-work and **zero**
  holds of a class §2.2 says is not holdable. Track hold-to-approve latency and the deny rate.
  If more than 30% of holds are denied, the holdable classes are too wide.
- Pilot 2: for the one recipe on one node, show three applies and one forced rollback (bad
  port, so the post-check fails) with exact refusal codes. Then extend the catalog to the 11
  root tickets one recipe at a time, each by PR.

## 5. Rollback

- Pilot 1: remove the `hold_gate.py` hook entry, and the CLI falls back to today's deny. Any
  tickets in `awaiting_grant` are `wq release`d. `lumen-run --bind-ro` becomes unused.
- Pilot 2: `systemctl disable --now ops-apply-*.socket`, which must run via a detached
  systemd-run on the node (the instinct-6 shape). Then delete the forced-command key line. Every
  applied change has a backup and an inverse manifest.

## 6. Build tickets

All are filed with a dep on wq#4758 (this doc on main), plus the deps listed.

| wq | tag | title | deps |
|---|---|---|---|
| 4759 | B1-T0 | replay harness: classify the synthesis's 14 cap-denied + 11 root tickets against §2.2 / recipe catalog (dry-run) | - |
| 4760 | B1-T1 | hold_gate.py PreToolUse hook: H1/H2, in-process guard+action_gate pre-check, fingerprint, defer/allow/deny | - |
| 4761 | B1-T2 | wq `grant_requests`, `awaiting_grant`, `wq await-grant` / `wq grant approve\|deny\|list` | - |
| 4765 | B1-T3 | fworker tool_deferred parse, EXIT_AWAITING_GRANT, empty-prompt resume, lumen-run `--bind-ro` | 4760, 4761 |
| 4762 | B1-T4 | repo-read broker + `needs:repo()` derivation in file_verify_pass | - |
| 4766 | B1-T5 | Pilot-1 live run on verify-probe lane, 1 week, vs Squidbrake observe log | 4765, 4762 |
| 4768 | B1-T6 | worker unit base confinement (ProtectHome=tmpfs, binds, InaccessiblePaths) | 4766 |
| 4763 | B2-T0 | pick pilot node: bes (else anubis), not Vault/Commander path | - |
| 4764 | B2-T1 | ops-apply gate+node daemons, catalog, `cloudflared.ingress.upsert` v1, V1-V10, hardening (no install) | - |
| 4767 | B2-T2 | install gate+node via detached systemd-run; each V-code refuses once; forced rollback | 4763, 4764 |
| 4769 | B2-T3 | Pilot-2 live enable: 3 applies + 1 forced rollback | 4767, 4688 |
