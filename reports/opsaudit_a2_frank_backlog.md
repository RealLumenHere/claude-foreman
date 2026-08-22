# A2 — The Frank-decision backlog: one decision sheet

Ticket wq#752. Campaign spec: `/home/lumen/instructions/FLEET_OPS_AUDIT_2026-08-21.md` (F1).
Measured 2026-08-22 against `/home/lumen/agents/work-queue/queue.db`. Nothing was closed,
unblocked or edited by this pass — the sheet and the script are the whole deliverable.

**How to use this.** Read §2 top to bottom and answer each line yes/no (or pick the letter).
Every line carries a recommended default, so silence on a line means "the default stands".
Then run `reports/opsaudit_a2_apply.sh` (§5) with the flags matching your answers. §3 and §4
are things you do **not** need to read to unblock the fleet — they are what the script clears.

---

## 0. Method

Population = every `open`/`blocked`/`claimed` ticket that is either in the Frank-only `user`
lane, or `blocked`, or carries a gate/Frank/approval/decision marker:

```sql
select id from tasks where status in ('open','blocked','claimed')
and (lane='user' or status='blocked' or gate!=''
     or lower(notes) like '%frank%' or lower(notes) like '%approval%'
     or lower(notes) like '%decision%' or lower(notes) like '%gate%'
     or lower(detail) like '%frank decision%');
```

**77 items** (F1 measured 74 one day earlier; the delta is new tickets, not drift).
Every one was read — title, detail, notes, deps — and assigned exactly one class.
Downstream fan-out was computed as the transitive closure over the `deps` field.

---

## 1. Findings, before the sheet

**F1's headline is right, but its size is wrong: only 30 of 77 are actually on Frank.**

| class | what it is | count | share |
|---|---|---:|---:|
| **(a)** genuinely Frank-only — spend, physical hardware, risk acceptance, business | **30** | 39% |
| **(b)** answerable from existing evidence by an agent today | 9 | 12% |
| **(c)** stale or moot — the question has already been answered or overtaken | 10 | 13% |
| **(d)** should never have been gated on Frank at all | 28 | 36% |

Three corrections to the audit's own evidence, each verified rather than assumed:

1. **wq#383 is closed.** F1 cites it as "FRANK DECISION PENDING since 2026-07-19 — 33 days of
   known-CVE exposure". It was executed 2026-07-31 and closed 2026-08-01 (`done_at`), twenty
   days before the audit was written. Gitea 1.26.4 + Tailscale fleet + cloudflared all landed.
   The flagship example of the F1 bottleneck had already drained itself. The *current* version
   of that exposure is wq#464 (gitea 1.26.4 → 1.27.1, vaultwarden behind) and it is 11 days old,
   not 33.
2. **8 of the 44 `blocked` tickets are worker failures, not Frank gates** — wq#358, #428, #663,
   #678, #679, #688, #690, #707 all carry `FALSE_COMPLETE`/`KILLED` after 2-4 attempts. Counting
   them as "parked on Frank" inflates F1 and hides them from A1, where they belong. One of them
   is wq#663 — the Frank-gated decision UI in Mission Control. *The tool that would fix F1 is
   itself stuck behind the worker-success problem, not behind Frank.*
3. **wq#653's premise died the day it was filed.** It reports `lumen/minerva` missing from
   `agent-scope.conf`, blocking every minerva PR. Line 66 of
   `/home/lumen/infra/github/agent-scope.conf` reads
   `ALLOW lumen/minerva … Frank-approved 2026-08-16` — the same day the ticket was created.
   Nobody re-checked, so it has sat 6 days as a Frank gate with nothing behind it.

**The pattern behind class (d).** Escalation is not distributed randomly. It clusters:

- **12 proposal-build REJECT-backs** (§3). An agent was told to build a Frank-approved Minerva
  proposal, researched it, found the premise dead — dead API, negative result in the cited
  paper, or the thing already exists — wrote the disproof, and blocked waiting for Frank to
  agree with evidence he cannot check faster than the agent already did. 144 ticket-days parked.
- **Pure build tickets filed into the `user` lane** (wq#420, #702, #657). These carry no
  decision at all. wq#420 even says "prompt ready at …". They were mis-laned, and `wq claim`
  refuses the `user` lane by design, so they became unreachable rather than unassigned.
- **Reversible single-unit changes gated anyway** (wq#349 pip-install on a bench host, wq#462
  retire a systemd unit that targets a NIC dead since 08-02 and fails daily). This is R3 in the
  spec: no threshold defines "complex", so agents gate defensively and the work becomes backlog.
- **Gates that were satisfied and never re-read** (wq#124's NVMe purchase closed as wq#123;
  wq#325's dep wq#324 closed 07-18; wq#653 above). Nothing in the queue re-evaluates a gate
  once it is set. A gate is written once and read never.

**The cost is concentrated, not spread.** Of the 30 genuine items, three carry risk that
compounds every day and the rest do not: wq#556 (120+ hours of campaign state with zero backup
coverage), wq#418 (12 nodes holding staged kernel/libc plus Lumen's Secure Boot dbx CVE
revocations), wq#305 (fleet credential rotation, staged and one command from executing). Those
three are the sheet. The other 27 are cheap to answer and cost little to defer.

---

## 2. THE SHEET — 30 decisions, ordered by cost of delay

One line each. **Rec** is the recommended default. Answer Y/N (or the letter) down the page.

### Tier 1 — compounding risk, answer today (9)

| # | age | the decision | cost per week parked | rec |
|---|---:|---|---|:--:|
| **556** | 8d | Back up `/home/lumen/pokemon` — checkpoints 95M + events 27M + saves 6M (skip roms 2.4M, replaceable)? | 120+h of campaign progress in **zero of 393 restic snapshots**; one disk event is total loss | **Y** |
| **418** | 14d | Schedule the reboot window: 12 nodes with staged kernel/libc, maat last at lag=0, Lumen with you present for `fwupdmgr` dbx | 12 nodes running known-vulnerable kernels; dbx CVE-2026-8863 unrevoked on Lumen | **Y** |
| **305** | 35d | GO on fleet password rotation — staged, one command, `bin/rotate-fleet-pw.sh.staged-wq305` | Fleet-wide shared credentials unrotated 35d; NOPASSWD already done so blast radius is understood | **Y** |
| **464** | 11d | Approve the W33 update pass — gitea 1.26.4→1.27.1, vaultwarden behind (reboot half folds into #418) | Current CVE exposure; this is what wq#383 turned into | **Y** |
| **360** | 34d | Rewire day: every smart plug has a loose socket (already caused the Bes outage). Hathor cord stays **out** | Random unplanned node loss; each incident costs more than the rewire | **Y** |
| **390** | 33d | ARM `plug-recover` (auto AC-recovery), peripheral-first, vault pair + Lumen last — mechanism built and validated on p13 + Nephthys | Every AC event needs a human; the validated mechanism sits at ARMED=0 | **Y** |
| **526** | 8d | Mint a **classic** PAT on RealLumenHere + accept invite 328977328 (fine-grained provably cannot work — verified 404/empty) | Blocks the entire LPP GitHub path; literally the one step no agent can do | **Y** |
| **463** | 12d | Drill vault failover **and failback** against Sobek (the drill fences Maat — needs your window) | Failback has never run against Sobek and two variants exist with no canonical marked; it only fails when it matters | **Y** |
| **325** | 35d | Swap vault promote-target Wadjet→Bes, re-arm, drill, release Wadjet — **its stated blocker wq#324 closed 07-18** | Stale-blocked 5 weeks on a satisfied precondition; HA monitor still points at the old target | **Y** |

### Tier 2 — physical / spend, answer at the bench (10)

| # | age | the decision | cost per week parked | rec |
|---|---:|---|---|:--:|
| **362** | 34d | Bench power-up for hathor root-cause (wedge-on-boot + 231W runaway + AFTERG3 disarm) | A quarantined node and an un-diagnosed runaway-power failure mode | **Y** |
| **120** | 41d | Boot hapi-1 blade — unblocks the whole BC-250 line | Blocks #266, #364 and the 2-bit fleet rollout gate; oldest item on the board | **Y** |
| **354** | 35d | Go for the burn-in gate: 3 warm + 3 cold cycles per Mac (Isis, Nephthys) — Nephthys AFTERG3 regression already found | Power fixes keep regressing on later boots and nothing catches it | **Y** |
| **337** | 35d | Physical prep: per-Mac auto-boot verify + plug soak assignments (Nephthys done remotely; Isis, others remain) | Blocks power-cycle enrollment; #352 plug mapping downstream | **Y** |
| **350** | 35d | Bench-validate the 24-port switch before any fleet cutover (zero known history, `TEST_PLAN.md` + `switch-port-test` ready) | An unvalidated core switch is the single worst thing to cut over blind | **Y** |
| **391** | 33d | Buy GL.iNet Comet GL-RM1 IP-KVM + ATX board, ~$116 | Every wedge like hathor needs physical presence; first use pays for itself | **Y** |
| **11** | 47d | Nest UART recon at the bench (unit is powered) — unblocks #12, #13 | Oldest ticket on the board; procedure pre-staged and turnkey | **Y** |
| **12** | 47d | Reload the installed PWA once on your phone to finish the #18 messaging cutover | Still paying for messaging you have already replaced | **Y** |
| **339** | 35d | Resubscribe phone to the renamed high-entropy ntfy topics **before** the 48h dual-publish window ends (#316 is the same task — script closes it) | Old topics are guessable and leak infra telemetry; miss the window and you lose alerts | **Y** |
| **550** | 8d | apis is dead: **(A)** scrap it and move the ideaCam/`snap` pipeline to apep, or (B) repair | Already PARKED so it raises no alerts — the parts-photography pipeline is the only real loss | **A** |

### Tier 3 — policy and one-word answers (11)

| # | age | the decision | cost per week parked | rec |
|---|---:|---|---|:--:|
| **703** | 2d | Add `Skill` to the foreman worker allow-list, with a per-archetype skill floor in FOREMAN_RESEARCH §15? Widens worker surface; guard.sh still applies | 30 pstack skills live and unreachable by every worker lane | **Y** |
| **738** | 0d | Governor reserve floors: re-denominate on the 7-day window (the 5-hour window produced no utilization reading in 39 transcripts across 4 days) | Reserve floors currently guard a window that never binds — this sizes your own headroom | **Y** |
| **634** | 6d | pxpipe canary — route Claude traffic through a third-party proxy via `ANTHROPIC_BASE_URL` for 59-70% context savings | Confabulation on exact strings is **unmeasured on Sonnet-5** and our workers do git ops on hashes; OAuth passthrough to a third party | **N** |
| **501** | 9d | GitHub repo disposition, 4 items: rom-transfer-tmp **delete**, lumen-agent **delete**, lumen-content **archive**, (4th per ticket) | Least professional-looking public repo stays public | **as listed** |
| **409** | 33d | Disable the Watch8 call stack (dialer/incallui/contacts)? Only if you never take calls on the watch | Nothing; pure preference. Notification mirroring is unaffected either way | **your call** |
| **331** | 35d | M3 gate: **(A)** redesign to fact-seeded contexts or **(B)** demote to telemetry-only and drop gate 2 — current entailment check punishes hedging and rewards confident fabrication (A=0.186, B=0.025, both at floor) | Blocks the harness rev3 rerun (<10 min, <$1 once answered) | **B** |
| **266** | 36d | Confirm the hapi-1 quant eval gate stands as written once the blade boots (verdict_agree ≥0.9, leaks=0, rationale ≥3.5) | Rides on #120; no separate cost | **Y** |
| **364** | 34d | BC-250 SPI dump at the bench (read-only, x2 + diff) once the blade is wired | Feeds #363 core-unlock feasibility; read-only, no brick risk | **Y** |
| **558** | 8d | Remove the v0.1.0 release + tag from claude-command-center (private, never public, no exposure) | Cosmetic only — consistency with the winforge decision | **Y** |
| **485** | 9d | Designate the BGB interop box: Mirror or PBox | Blocks one unticked interop box on LPP multiplayer | **Mirror** |
| **664** | 5d | Nothing to decide — bring Thor online. It has been Tailscale-offline all session and the notification-log fix needs the device | Blocks a UI annoyance only | **—** |

---

## 3. Batch A — 12 REJECT-backs, one answer

Twelve tickets where an agent was told to build a Frank-approved Minerva proposal, did the
research-before-build step, and found the premise dead. Each already carries the disproof in
its notes. They have been parked 12 days each — **144 ticket-days for one word**.

| # | proposal | why the agent rejected it |
|---|---|---|
| 439 | DeepSeek-V3.2 endpoints | strictly dominated — v4-flash is cheaper *and* newer; "V3.2-Speciale" doesn't exist |
| 445 | Oracle Agent Memory schema | Memory V3 already is this; substrate is Oracle AI Database (vendor lock) |
| 446 | persona-vector bias auditing | needs activation hooks on loaded weights; LiteLLM exposes no hidden states |
| 447 | structural prior injection | the cited paper is a **negative result** — collapses under distribution shift |
| 448 | self-evolving A/B harness | DGM v2 already is this, running daily |
| 450 | falsifiable commitment planning | already exists across minerva_factory + skillhex |
| 451 | WorkSurface-Bench routing eval | category mismatch — benchmarks document routing, not model routing |
| 453 | Know Your Source integration | it's a static news-credibility dataset, no API, wrong domain |
| 454 | continuous consolidation | `lumen-research-index.timer` already does it daily, idempotent |
| 455 | speculative decoding pilot | draft model vocab-incompatible; empirically net-negative on Qwen3.6-35B-A3B |
| 457 | multi-agent debate consensus | third agreement mechanism before the second was ever enabled |
| 458 | contrastive reflection loop | duplicate of DGM v2 + skillhex |

> **Decide once:** accept all 12 rejections and close them? — **Rec: Y**
> (Any you want built anyway, name the number and the script skips it via `--keep`.)

The real fix is upstream and belongs to A6/A7: a proposal that fails research-before-build
should close itself with the disproof attached. Asking you to counter-sign twelve disproofs you
would have to re-derive to check is escalation theatre.

---

## 4. Not yours — what the script clears without you (47)

### (c) stale or moot — 10, close or retarget

| # | age | why it is moot | action |
|---|---:|---|---|
| 653 | 6d | `ALLOW lumen/minerva` is on line 66 of agent-scope.conf, approved 2026-08-16 — same day filed | close |
| 693 | 2d | notes say research complete, `docs/ROOTSPEC.md` written; blocks #694, #695 | close |
| 328 | 35d | children #341-347 all deployed; power.lumennode.uk is live and CF-gated | close |
| 343 | 35d | superseded — TOU cost landed query-time in Grafana per #344 | close |
| 316 | 35d | escrow half done 07-17; remaining half is verbatim wq#339 | close |
| 397 | 33d | superseded by wq#567 decision C (accept contended p95) on 2026-08-14 | close |
| 108 | 41d | WATCH whose next check was due 2026-07-22, 31 days overdue; scout feeds cover it | close |
| 567 | 8d | Frank chose C on 2026-08-14 — this is hardware-deferred, not Frank-blocked | retarget |
| 160 | 40d | decision already approved 2026-07-16; only the ~$15-30 order remains | retarget |
| 765 | 0d | its item 1 ("a decision sheet for the 74 parked items") is this document | retarget |

### (d) should never have been gated — 28, un-gate

- **12 REJECT-backs** (§3) — close on your one answer.
- **8 worker failures wearing a `blocked` badge** — #358, #428, #663, #678, #679, #688, #690,
  #707. Not decisions. Requeue under A1's recalibrated ceilings; #428 is an EPIC parent that
  should never have been dispatchable at all.
- **7 mis-laned or over-gated** — #306 (blocked by #305, not by you), #349 (pip install on a
  bench host), #420 (build ticket in the `user` lane, prompt already written), #603 (TURNS
  failure, already requeued), #657 (a `--merge-method` flag on lumen-pr), #702 (pstack build
  work in the `user` lane), #764 (campaign dependency).
- **#363** — a WATCH with defined triggers, never a gate.

### (b) answerable by an agent today — 9, dispatch

#124 (its NVMe gate closed as wq#123), #191 (half the judge bench already PASSED, the rest is
measurable), #351 (its own text offers "retarget to a Mac" — no exception needed), #402 (blocked
on a soak nobody started), #462 (retire a unit targeting a NIC dead since 08-02, failing daily),
#510 + #511 (re-blocked pending someone re-reading a disproof), #554 (an env-var default),
#600 (measurement done, premise confirmed, corpus already plug-keyed 13/13).

---

## 5. The script

`reports/opsaudit_a2_apply.sh` — **dry-run by default**, prints every `wq` command it would run
and changes nothing without `--apply`. Idempotent: re-running it after a partial run converges.

```bash
# see everything, change nothing
reports/opsaudit_a2_apply.sh

# the common case: you said Y to Batch A and want (c) and (d) cleared
reports/opsaudit_a2_apply.sh --apply --close-moot --ungate --accept-rejects

# keep two proposals alive despite the rejection
reports/opsaudit_a2_apply.sh --apply --accept-rejects --keep 455,457
```

Flags map one-to-one onto §3 and §4: `--close-moot` (c), `--ungate` (d), `--accept-rejects`
(§3), `--dispatch-b` (b), `--requeue-failures` (the 8 worker failures). No flag touches any of
the 30 items in §2 — those stay yours.

---

## 6. What this hands to A6 and A7

Three structural findings, offered as input rather than conclusions:

1. **A gate is written once and read never.** Four items here were blocked on preconditions that
   had already been satisfied — one of them for five weeks, one within hours of being filed.
   Whatever A6 proposes, a gate needs a re-evaluation path or it becomes a permanent park.
2. **Escalation is a *lane* problem as much as a rule problem.** `wq claim` refuses the `user`
   lane by design, so a build ticket mis-filed there is not merely delayed — it is unreachable.
   Three of the 28 (d) items are exactly this. That is a validation bug, not a policy question.
3. **36% of the backlog was never a decision.** If risk-tiered standing approval (A6) had existed,
   it would not have touched the 30 real items — those are spend, hardware, and risk acceptance,
   which stay Frank's under any model. The win is the other 47, and most of that win comes from
   two cheap rules: *research-before-build failures close themselves with the disproof*, and
   *reversible + backed-up + single-unit blast radius = fix it* (spec R3).

The 30 items in §2 are the irreducible core. That is roughly one page, once.
