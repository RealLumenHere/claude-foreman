# A11 — The maintenance window: patching that actually lands

Ticket: wq#761 (build lane, ops-audit campaign)
Agent: claude-web-a11a12-f7247e · 2026-08-22 PT / 2026-08-23 UTC
Constraint honoured: **nothing was rebooted, upgraded, installed or enabled.** Every command below
is read-only or a proposal. Frank's reboot-permission rule is untouched.

---

## 0. The answer, before the argument

The fleet does not have a patching problem. It has a **patch-visibility problem and a
one-item-at-a-time approval problem**, and those are different diseases with different cures.

- Security patches already land automatically. `apt_security = 0` on all 13 reachable nodes,
  because `unattended-upgrades` is enabled and running. Nobody has to approve those and nobody does.
- What does not land is everything `unattended-upgrades` deliberately will not touch: **application
  and container versions, firmware, the non-security apt tail, tailscale on the two nodes that
  drifted, and the reboots that make kernel updates real.**
- The rule that blocks those is not "never reboot without permission". It is G4 — *one mutation
  per approval* — applied to a class of work that is inherently N mutations. A 14-item batch
  becomes 14 conversations, so it becomes zero conversations.

So the window is not a new safety rule. It is **the delivery vehicle for A6's Class B**: one
approval, one declared window, N independent per-target verifications. A6 designed the class and
explicitly did not build it. This is the build.

**Recommended cadence: monthly, second Saturday, 20:00 PT, Frank present for the first five
minutes.** Argued in section 6.

---

## 1. The ticket's own premise is stale — corrected

The ticket, and campaign spec F1, cite wq#383 as the flagship: *"Gitea 1.26.2->1.26.4 nine-CVE
batch, Tailscale 1.98.4->1.98.9 including a Serve DoS, cloudflared bump — pending 33 days."*

**wq#383 was executed 2026-07-31 and closed 2026-08-01.** Its notes read: *gitea 1.26.4 + key
rotation on Anubis; tailscale fleet complete (Lumen 1.98.10 last); cloudflared 2026.7.3. All
endpoints verified.* A2 and A7 both caught this independently; this report is the third
confirmation and the first to check what the fleet actually runs today.

This matters for more than bookkeeping. The stale claim is still live in `HANDOFF.md` section 3
item 1 ("FRANK DECISION PENDING — wq#383"), which is read at the start of every session. **A2 and
A7 both corrected the report; nobody corrected the file the next agent reads.** That is the
supersession failure A7 names, reproducing itself inside the campaign that diagnosed it.

The premise being wrong does not weaken the ticket — it sharpens it. wq#383 proves the fleet *can*
execute a rolling patch batch competently. It took a full attended session to do it, and three
weeks later the drift is back. **That is the argument for the window: not that patching never
happens, but that it costs a session every time and therefore happens about once a quarter.**

---

## 2. What already exists — do not rebuild any of this

| capability | artifact | state |
|---|---|---|
| Security apt patching | `unattended-upgrades` (`APT::Periodic::Unattended-Upgrade "1"`) | **live fleet-wide**, `apt_security=0` on 13/13 reachable |
| Fleet update/drift reporter | `/home/lumen/bin/fleet-update-check` (wq#419) | **built, validated, timer NEVER installed** |
| Its timer + needrestart drop-in | `/home/lumen/infra/staged-wq419/` | **staged since 2026-08-10, not installed** |
| The wedge-class killer | needrestart list-only conf on 10 nodes (wq#464) | live — this is why no node is stuck today |
| Detached execution that survives gateway reap | `/home/lumen/bin/lumen-run` | live, mandated |
| Per-node health telemetry | node_exporter on 13 nodes -> VictoriaMetrics on Bes (wq#753) | **live, 13/13 up=1, 472 metric names** |
| Backups + restore proof | restic, 5 timers; wq#566 restore-proven standard | live |
| Approval class for exactly this work | A6 section 3 row **B1** | **designed, not built** |
| Ticket/evidence rails | `wq`, `slock`, closure evidence gate (wq#759) | live |

The window needs **one new script and one new systemd timer**. Everything else is assembly.

---

## 3. What the measurement actually says (2026-08-23 05:46-05:58 UTC, read-only)

### 3a. Per-node debt

| node | apt security | apt other | reboot debt (d) | fwupd pending | tailscale |
|---|---|---|---|---|---|
| lumen | 0 | 33 | 1.9 | 5 | 1.102.2 |
| anubis | 0 | 17 | 1.0 | **23** | 1.102.2 |
| seshat | 0 | 18 | 1.7 | 6 | 1.102.2 |
| maat | 0 | 16 | 1.0 | 1 | 1.102.2 |
| isis | 0 | 19 | 1.0 | - | 1.102.2 |
| bes | 0 | 16 | 2.0 | - | 1.102.2 |
| sobek | 0 | 18 | 1.0 | - | 1.102.2 |
| thoth | 0 | 16 | 1.0 | - | 1.102.2 |
| apep | 0 | 11 | **4.0** | - | 1.102.2 |
| sia | 0 | 22 | 1.0 | - | **1.98.10** |
| gameboy | 0 | **127** | - | - | **1.98.10** |
| hathor | UNREACHABLE (tailnet-offline since ~2026-07-17, plug cut 2026-08-11) | | | | |
| wadjet | UNREACHABLE (bench, wq#362) | | | | |

Reboot debt is **1-4 days, not months**. `unattended-upgrades` reinstalls the flag daily, so the
number measures *flag age*, not *missed kernels*. Worth saying plainly: **the reboot rule is not
the expensive one.**

### 3b. Application drift (queried live, this session)

| component | running | upstream latest | where | note |
|---|---|---|---|---|
| tailscale | 1.98.10 | **1.102.3** | sia, gameboy | TS-2026-010, TS-2026-011 land in the 1.10x line |
| tailscale | 1.102.2 | 1.102.3 | 11 nodes | patch bump |
| gitea | 1.26.4 | **1.27.2** | anubis | minor-line move; hosts every fleet repo |
| vaultwarden | 1.36.0 | **1.37.2** | maat | **crown jewel — vault primary** |
| miniflux | 2.3.2 | 2.3.3 | sia | patch |
| cloudflared | 2026.7.3 | **2026.8.2** | lumen | Bes already on 2026.8.2 |
| ollama | 0.32.6 | 0.32.15 | lumen, isis | not security-driven |

Two Tailscale advisories are relevant to the 1.98.10 pair. **TS-2026-010** (Tailscale SSH passes
environment variables to child processes via inherited file descriptors) is materially reduced
here — this fleet set `RunSSH=false` on Anubis, Nephthys and the Mac triplet and uses real sshd
over the tailnet. **TS-2026-011** (host-scoped IPv4 destinations accepted at points acting on an
unmapped 4via6 address) does not depend on Tailscale SSH. Severity is per-node, not per-fleet, and
the assembler must say which is which rather than printing a version delta and calling it a CVE.

### 3c. The assembler has a silent-failure bug, and it fired today

The 05:46 UTC run — launched by the wq#761 session that infra-crashed before this one — rendered
this, and it looks like a report:

```
- cloudflared: have `2026.7.3` / latest `?`
- gitea:       have `None`     / latest `?`
- vaultwarden: have `None`     / latest `?`
```

Every upstream lookup returned `?` and every app version returned `None`. Re-running the same
functions from the same box twelve minutes later returned `1.27.2`, `1.37.2`, `2.3.3`, `1.102.3`
and `gitea version 1.26.4` — all of them, first try. The probes are fine. What failed is the
GitHub API budget: `gh_latest` is **unauthenticated at 60 requests/hour**, 7 repos per run, and
three crashed wq#761 attempts inside one hour exhausted it.
`/home/lumen/.secrets/github-lumen.token` has existed since 2026-07-09 and the script never
reads it.

**This is the exact pathology `claude_memory.md` records from wq#575: a fail-open path makes a
broken integration look like a working one.** A run where every upstream lookup failed is
byte-shaped like a healthy run where nothing is behind. Had the first window's batch been
assembled from the 05:46 report, it would have been an empty batch, approved as such, and four
behind-components would have survived another month with a green tick next to them.

A second, quieter instance: `gh_latest("ggml-org/llama.cpp")` returns `0.2.0`. llama.cpp releases
are `bNNNN` build tags. That is not a stale value, it is a wrong one, printed with the same
confidence as the correct ones.

**Non-negotiable design consequence, and the reason this section exists: the assembler must exit
non-zero and refuse to produce a batch when any probe fails. A batch is an approval artifact. An
approval artifact may not contain a `?`.**

---

## 4. Design

Three phases. The only human step is phase 2, and it is one message.

### Phase 1 — ASSEMBLE (unattended, T-24h, `fleet-maint-assemble`)

Wraps `fleet-update-check`; does not replace it.

1. Run `fleet-update-check` with GitHub auth and `--no-ticket`.
2. **Completeness gate.** Any `?`, any `None` for a component that has a known-good probe, any node
   that is neither reachable nor on the declared-absent list -> **exit 2, ntfy `lumen-alerts`, no
   batch file written.** No batch is better than a batch with a hole in it (section 3c).
3. Classify each item into a lane:
   - **SEC** — named advisory affecting a version we run, per-node.
   - **APP** — behind upstream, no advisory.
   - **APT** — the non-security apt tail.
   - **FW** — fwupd; excluded from every window by default (section 6).
   - **REBOOT** — nodes whose flag is set after the above land.
4. Per item emit: target node, current -> target version, restart-or-reboot, rollback command,
   backup precondition, and **the health probe that proves it worked** — the specific one, not
   "check it's up".
5. Order the batch (section 5) and estimate per-item and total time from the last window's
   measured durations.
6. Write /home/lumen/maint/windows/batch-YYYY-MM-DD.json plus a human .md, and open **one**
   `user`-lane wq ticket: "[maint] Window YYYY-MM-DD — N items, approve or amend".

### Phase 2 — APPROVE (Frank, one message)

Frank replies APPROVE, APPROVE MINUS <ids>, or SKIP. That reply is the Class B approval: it covers
the enumerated batch on the enumerated nodes for that window only, and expires when the window
closes. Nothing carries to the next window. Silence remains not-approval — **the window never runs
unattended.**

### Phase 3 — EXECUTE (`fleet-maint-run`, attended start, unattended finish)

Launched via `lumen-run` as a transient system unit so a Commander flap cannot kill it mid-node.
Per item, in order:

| gate | check | on failure |
|---|---|---|
| G0 | batch file sha256 matches the approved ticket | abort whole run |
| G1 | pre-state captured: version, unit state, NRestarts | abort item |
| G2 | **restore-proven** backup for this target, from this run (A6 P1) | abort item |
| G3 | rollback command dry-run exercised (A6 P3) | abort item |
| G4 | paired restore registered with the runner before mutation (A6 P4) | abort item |
| - | **apply** | |
| G5 | health probe passes at T+60s **and** T+300s (A6 section 5) | auto-rollback, stop run |
| G6 | node_exporter up{node=...} recovered; no new failed units | auto-rollback, stop run |
| G7 | `lumen_state.py ingest` new version, clear the paired restore | - |

**Stop-on-first-failure is absolute.** A failed item stops the run, rolls that item back, notifies,
and leaves every later item untouched and unapproved. It never continues to "the safe ones". A6 P2
is the reason: applying a verified change to a second box is a new action with its own G1, never a
continuation of the first.

### Phase 3b — RECORD (same script, always runs, even on a stop)

`lumen_state.py ingest` per changed version -> `state-regen` -> `lumen_episode.py commit` ->
`reports/maint_window_YYYY-MM-DD.md` -> close the wq ticket with the report path so the wq#759
evidence gate is satisfied -> ntfy summary.

---

## 5. Ordering law

Fixed, encoded in the runner, not left to the assembler's judgement:

1. **Leaf nodes first** — gameboy, apep, thoth, seshat, sia. Nothing depends on them; if the runner
   is broken, it breaks where the blast radius is a kiosk.
2. **Service nodes** — anubis (gitea), bes (VM/Grafana/HA), isis.
3. **Sobek**, the vault *standby*, before Ma'at. Patching the standby first is the rehearsal.
4. **Ma'at last among remote nodes**, and only with pg_stat_replication lag = 0 verified in the
   same minute. Vaultwarden and Postgres are separate items with separate gates.
5. **Lumen last, always.** It runs the runner, the Commander path, LiteLLM and every agent service.
6. **Reboots run after every package item on that node**, one node at a time, never two nodes with
   a dependency between them in the same window.

Two hard preconditions before item 1:

- **No live agent session on the mesh.** `wq list` shows no claimed ticket on a live lease, and the
  foreman dispatcher timer is stopped for the window duration — that is a stop, so it carries a
  paired restore by the standing rule and is restored in phase 3b.
- **Vault HA healthy**: Ma'at in_recovery=f, at least one standby streaming, lag 0.

Unreachable nodes (hathor, wadjet today) are **declared absent in the batch, not silently skipped.**
An absent node is a line item with the word ABSENT on it.

---

## 6. Cadence

**Monthly, second Saturday, 20:00 PT. Assembly fires the Friday before at 20:00 UTC. Frank present
for approval and the first node; the run finishes without him.**

Why monthly, not weekly: measured drift is roughly one minor version per component per 6-10 weeks
(gitea 1.26.4 -> 1.27.2 in about three weeks is the fast end; miniflux 2.3.2 -> 2.3.3 over months
is the slow end). A weekly window is a weekly interruption to move one patch version, which is how
windows get skipped and then abandoned. Monthly caps worst-case exposure for a non-security app CVE
at about 30 days, while security apt patching — the thing with real exposure — is already same-day
via `unattended-upgrades` and is not waiting on this window at all.

Why Saturday evening: no agent sessions on the mesh by convention, and Frank is at the bench.

**Out-of-band path, deliberately kept.** A KEV-listed or actively-exploited advisory against a
component we run does not wait for the second Saturday. The assembler flags it SEC-URGENT, opens
its own ticket immediately, and it goes through ordinary per-action approval. **Do not let the
existence of a window become a reason to defer.** That failure mode is the one thing that would
make this design worse than no design.

**fwupd is excluded by default.** Anubis has 23 pending firmware updates. Firmware is the one class
where rollback is frequently impossible and a bad flash is a physical trip. It gets a bench session
with Frank at the machine, not a scripted rolling run. Listing it in the report is the point;
applying it is not.

---

## 7. THE FIRST WINDOW — exact pre-assembled batch

Target: **2026-09-12** (second Saturday), or the next Saturday Frank wants. Assembled from live
probes 2026-08-23 05:46-05:58 UTC. Re-assembly at T-24h supersedes this; it is printed here so
Frank can see the shape of a batch before approving the machinery that produces one.

Estimated attended time: **about 6 minutes** (approve, then watch node 1). Estimated total run:
**about 35 minutes.**

| # | lane | node | item | current -> target | apply | rollback | health probe |
|---|---|---|---|---|---|---|---|
| 1 | APP | gameboy | tailscale | 1.98.10 -> 1.102.3 | service restart | apt pin previous, restart | tailscale status peer count; kiosk display still up |
| 2 | APT | gameboy | apt tail (127) | - | none | snapshot | systemctl --failed empty |
| 3 | APP | sia | tailscale | 1.98.10 -> 1.102.3 | service restart | as above | tailnet reachable from lumen |
| 4 | APP | sia | miniflux | 2.3.2 -> 2.3.3 | container recreate | previous image tag | /healthcheck 200; feed count unchanged |
| 5 | APT | apep, thoth, seshat, isis, sobek, bes, anubis | apt tail (11-19 each) | - | per-node | snapshot | systemctl --failed empty |
| 6 | APP | anubis | gitea | 1.26.4 -> 1.27.2 | service restart | nightly dump + previous binary | git ls-remote on 4 fleet repos; /api/v1/version |
| 7 | APP | 11 nodes | tailscale | 1.102.2 -> 1.102.3 | rolling, one at a time | as above | per-node up{} in VictoriaMetrics |
| 8 | APP | **maat** | vaultwarden | 1.36.0 -> 1.37.2 | container recreate | previous image + per-DB copy | vault.lumennode.uk 302; one cipher read via API |
| 9 | APP | lumen | cloudflared | 2026.7.3 -> 2026.8.2 | **detached only** | previous binary | every CF hostname 200/302 |
| 10 | BOOT | nodes flagged after 1-9 | kernel activation | - | **explicit per-node consent** | - | full service inventory + up{} |
| - | FW | anubis (23), seshat (6), lumen (5), maat (1) | firmware | - | - | - | **DEFERRED — bench session, section 6** |

Item-level notes that change how they must be run:

- **Item 8 is the crown jewel.** Ma'at is the vault Postgres primary. Vaultwarden's container is a
  separate item from Postgres, and Postgres is not in this batch at all. G2 for item 8 means a
  *restore-proven* copy, not a dump that exited 0 — fq#22's lesson. If the standby is not streaming
  with lag 0 at the moment item 8 is reached, item 8 does not run.
- **Item 9 must be detached.** Commander rides the cloudflared it would be reloading; a foreground
  run gets SIGKILLed mid-script. systemd-run or lumen-run, per the standing rule.
- **Item 6 rotates nothing.** wq#383 already rotated Gitea's SECRET_KEY and INTERNAL_TOKEN on
  2026-07-31. Do not repeat it out of pattern-matching to the old ticket text.
- **Item 10 is the only item that needs live consent.** Everything above it is reversible without a
  boot. Frank approving the batch is not approving the machine restarts; the runner asks again, per
  node, and takes silence as no. **The permission rule survives the window intact — that is the
  point of the design, not an exception to it.**
- hathor and wadjet: ABSENT. They will be a full window behind when they return, and the assembler
  must say so on their first reachable run rather than treating them as new.

---

## 8. What this does NOT fix

- **A6's precondition runner does not exist.** G2/G3/G4 above are A6's P1/P3/P4, and A6 says plainly
  that the evidence runner is unbuilt. Until it exists, phase 3 runs those gates as *its own* code
  over its own items — narrower and honest, but it means the window ships its own verification
  rather than sharing the fleet's. When the runner is built, phase 3 should call it and delete the
  local copies.
- **Container image digest pinning.** Several containers run the `:latest` tag (miniflux,
  actual-budget, ntfy, glance). "Upgrade miniflux" and "recreate miniflux" are the same operation
  today, which means an unrelated recreate silently upgrades it. Out of scope here; ticketed.
- **hathor and wadjet.** No amount of window design patches an unreachable box.
- **The window does not make Frank optional.** It makes him cheap: one approval message and five
  minutes, once a month, instead of fourteen conversations spread over a quarter.

---

## 9. Build tickets seeded

| ticket | what |
|---|---|
| assembler | fleet-maint-assemble — GitHub-authenticated gh_latest, completeness gate that exits non-zero, per-node advisory classification, batch JSON, one user-lane ticket |
| runner | fleet-maint-run — ordered rolling execution, G0-G7, stop-on-first-failure, auto-rollback, phase-3b recording |
| fix | fleet-update-check: read /home/lumen/.secrets/github-lumen.token; never render a question mark as data; fix the llama.cpp tag parser |
| install | Install the wq#419 staged timer so drift is visible weekly between windows (Frank-gated: one install + one enable) |
| hygiene | Pin container image digests for the latest-tag set so upgrading becomes an explicit act |
| doc | Correct HANDOFF.md section 3 item 1 — wq#383 closed 2026-08-01 |

---

## 10. Sources

- Campaign spec: /home/lumen/instructions/FLEET_OPS_AUDIT_2026-08-21.md, F1 and R4
- A2 opsaudit_a2_frank_backlog.md; A6 opsaudit_a6_approval_model.md (class table B1, P1-P5,
  auto-rollback triggers); A7 opsaudit_a7_rule_corpus.md
- wq#419 reports/419_fleet_update_check.md; wq#464; wq#383 (closed 2026-08-01)
- Live probes 2026-08-23 05:46-05:58 UTC: fleet-update-check state.json, gh_latest re-runs, per-node
  version queries, VictoriaMetrics up-series for job node_exporter_fleet on Bes
- Tailscale changelog and security bulletins: TS-2026-010, TS-2026-011
- claude_memory.md: MEMORY V4 P3 fail-open lesson (wq#575); long-jobs rule (2026-07-05); safe
  switchover and detached-cloudflared rule
