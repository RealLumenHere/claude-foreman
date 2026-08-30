# R5 — External Practice Gap Analysis (what does good actually look like?)
Ticket wq#926 · session web-research-3e971e · 2026-08-27 ~16:50 PDT
Method: web research (sources cited + graded A/B/C at each claim), reconciled against R2
(foreman/reports/R2_watchdog_consolidation.md incl. its §10 verification pass), R1, R3, R4,
and LIVE fleet state re-verified this session (unit greps, sentinel config, wq#966-974
status). Grades: A = primary docs / Google SRE book / man pages; B = practitioner
engineering blogs; C = forum/individual homelab opinion.

## 0. TL;DR — the honest comparison
The fleet's post-R2 shape (one poller, one prober, one actuator, one page path, a responder)
**matches mature practice** — all nine R2 migration tickets (wq#966-974) verified DONE, so
the 26-watcher problem this campaign was written against has largely been consolidated.
What remains off-standard, plainly:
1. **Alert volume/actionability is catastrophically off the industry budget.** Google's SRE
   Workbook standard: 2-3 actionable incidents per on-call shift maximum; every page
   actionable [A: sre.google/workbook/on-call; sre.google/sre-book/monitoring-distributed-systems].
   Our measured 7d: >1,500 raw alert events, the two loudest TRUE positives unanswered, aq
   backlog 211 (R2 §5, re-confirmed unchanged in R2 §10.3). We are ~2 orders of magnitude
   over budget. wq#972 (digest topics) treats sources; the standing hygiene PROCESS
   (audit/delete rule) does not exist yet — G4 below.
2. **No end-to-end dead man's switch on the alert pipeline.** Industry standard: one
   always-firing heartbeat routed through the FULL alert path to an INDEPENDENT external
   service that alarms on silence (Prometheus "Watchdog" alert -> healthchecks.io /
   Dead Man's Snitch; kube-prometheus ships this by default) [A: PromLabs training docs;
   B: multiple independent implementations]. Our meta-monitoring is all intra-house:
   lumen-sentinel-watch watches Gatus, bes-awareness is a reciprocal Lumen<->Bes watchdog —
   but page delivery rides self-hosted ntfy on Lumen, so **Lumen-down or ntfy-path-down =
   total silence that looks like health**. Sentinel config even documents it
   (config.yaml:188 "if ntfy is down this alert can't deliver"). G1 below — top gap.
3. **Progress-watchdog coverage is partial and the heartbeat is not progress-tied.**
   The standard answer to "up but not making progress" is exactly two mechanisms we half-use.

## 1. What standard looks like for a ~15-node fleet (RQ1)
- **Topology**: one metrics pipeline (Prometheus/VictoriaMetrics + node_exporter +
  Alertmanager + Grafana), one black-box/uptime layer (Gatus or Uptime Kuma), one
  cron/heartbeat layer (Healthchecks-style), one notification transport. 3-5 components
  with single ownership per failure class — not per-incident watchers.
  [B: bigiron.cc 2026 homelab observability stack; agtechlab.dev; C: r/homelab consensus
  visible across guides]. **Fleet verdict: AT PARITY post-R2.** VM+Grafana+node_exporter
  (Bes) + Gatus + sentinel probes + self-healer + aq/ntfy maps 1:1 onto this. The R2 §6
  design independently reinvented the standard shape; external practice corroborates it.
- **"How many watchers is normal"**: the number is not the metric — OWNERSHIP is. Mature
  practice = one owner per failure class + a routing layer that dedups/groups. 26 bespoke
  watchers was the anti-pattern; ~8 with clear roles is normal. [A: SRE book "muting for
  machines", 1:1 alert/incident ratio target; B: SoundCloud backstage].
- **Liveness vs readiness vs progress**: Kubernetes codifies it — liveness probe = "alive
  but unable to make progress -> restart"; readiness = "don't route traffic"; startup =
  "still booting, hold the other two" [A: kubernetes.io/docs/concepts/workloads/pods/probes].
  The non-k8s equivalent for us is §3.

## 2. Mature-team practice: drift, interim fixes, runbooks, fatigue, bloat (RQ2)
- **Config drift**: declarative desired-state in git, applied idempotently; drift detected
  by diff-against-git, not by memory. NixOS makes drift impossible by construction; Ansible
  idempotency is the pragmatic floor [B: NixOS homelab guides, illustris.tech; A: GitOps
  definition]. **Fleet verdict: BEHIND.** R3 proved hand-installed units happen
  (dispatch-deadman units were in no repo). systemd-in-repo convention is partial;
  lumen-repo-sweep covers repos, not /etc/systemd/system. Full NixOS migration NOT
  recommended (rebuild cost across heterogeneous e-waste fleet is real; benefit is
  reproducibility we partly get from provisioning scripts) — the proportionate step is G3.
- **Interim fixes**: label + ticket the permanent fix + review date; retire on completion.
  [B: squareops alert-rule review-date practice generalizes]. **Fleet verdict: AT PARITY**
  — R3 executed exactly this (retired deadman/night-watch/chain-runner, labelled the rest).
- **Runbooks**: every page-level alert carries a runbook link as a REQUIRED field
  [B: oneuptime, incident.io, devops.com — unanimous]. **Fleet verdict: PARTIAL.**
  aq_escalate (wq#965) names the runbook in its seeded P0 ticket — correct and new. Rank-
  and-file probe alerts don't carry one. Folded into G4 hygiene fields.
- **Alert fatigue**: the three-layer model (page / ticket / log-only), the 90-day audit
  question ("acted on in 90 days? if not: delete or demote"), duration requirements on
  thresholds, grouping-into-one-incident [B: incident.io 2026 on-call guide; squareops;
  A: SRE book ch.6 "every page actionable"]. **Fleet verdict: BEHIND** (finding #1 in §0).
  aq already implements dedup-by-repeat-count (better than raw ntfy); what's missing is the
  audit RULE and silences-with-expiry (§4 G2).
- **Post-incident process bloat**: Google is explicit that postmortems and their action
  items have COST and are chosen deliberately; AIs get owners, due dates, bounded counts,
  burndown tracking [A: sre.google postmortem chapters + login_spring17 AI paper]. The
  incident->new-bespoke-watcher engine R2 §2 documented is the recognized anti-pattern; the
  intervention ledger (R4) is precisely the pager-load metric mature teams track — that
  choice is validated by external practice [A: SRE workbook "Anatomy of Pager Load"].

## 3. The class that costs Frank most: "reports healthy while doing nothing" (RQ3)
Standard mechanisms, in order of preference:
1. **OS-native progress watchdog**: systemd Type=notify + WatchdogSec= + the service sends
   WATCHDOG=1 tied to REAL work progress (loop ticks/queue advance), Restart=on-watchdog.
   If the heartbeat isn't tied to progress "systemd has nothing to act on" — a beat from a
   stuck process lies [A: freedesktop sd_notify(3), systemd.service(5); B: hostmycode,
   oneuptime]. **Fleet verdict: PARTIAL, and we independently rediscovered both the
   mechanism and its classic failure.** wq#392 shipped exactly this on lumen-agent + 3
   fleet workers + command-receiver; the 30-watchdog.conf comment records the canonical
   gotcha (event-loop block starved the beat -> false SIGABRT kills -> widened 45->150s
   instead of offloading the blocking call). NOT covered: messenger, memoryd, self-healer,
   LPP loop — the frozen-but-active incident (R2 §2 row 2) hit an uncovered unit. G5.
2. **Freshness/last-success metrics**: every job exports last_success_timestamp; ONE
   generic rule alerts on time()-ts > threshold; push jobs via Pushgateway/textfile
   collector [A: Prometheus docs pattern; B: pistack cron-monitoring comparison]. We do
   this bespoke per-watcher (probe freshness rows, vlogs forwarder) — works, not generic.
3. **Dead man's switch / heartbeat monitoring**: silence-is-failure, external + independent;
   test it quarterly — "an untested dead man's switch is worse than none" [B: oneuptime
   heartbeat guide; A-adjacent: PromLabs; kube-prometheus default Watchdog alert]. We
   reinvented per-service deadmen (dispatch-deadman, retired) but never built the ONE
   pipeline-level external check the standard actually prescribes. **This is the gap.**
4. Kubernetes liveness probes (§1) — same idea, k8s-shaped; not our platform.

## 4. Ranked recommendations -> tickets (seeded this turn)
- **G1 [P1, wq#1039]** End-to-end alert-path dead man's switch: an always-firing heartbeat
  traversing sentinel -> aq -> ntfy -> an INDEPENDENT off-fleet check (healthchecks.io free
  tier, or equivalent NOT hosted on the fleet) that pages Frank's phone directly on
  silence. Quarterly kill-test scheduled. Closes the only class with zero coverage:
  whole-pipeline/Lumen-down silence. Evidence grade A that this is the standard mechanism.
- **G2 [P2, wq#1040]** Silences-with-expiry + grouping semantics (Alertmanager-equivalent)
  for the aq/ntfy path: expiring silence beats forgotten suppress; group related rows into
  one notification. Options: vmalert+Alertmanager on Bes fronting ntfy, or native in aq.
  Kills the forgotten-silence anti-pattern and residual multi-voice noise.
- **G3 [P2, wq#1041]** Desired-state for /etc/systemd/system: units+drop-ins in git,
  idempotent apply, drift = diff-vs-git alert (extend the repo-sweep class to unit files).
  R3's hand-installed-unit finding is the local proof this class recurs.
- **G4 [P3, wq#1042]** Standing alert-hygiene process (not a one-shot): quarterly audit
  timer applying the 90-day rule to every aq source + required fields on page-class alerts
  (runbook link, layer, review date). Complements wq#972's one-time cleanup.
- **G5 [P3, wq#1043]** Extend the wq#392 watchdog pattern (Type=notify + WatchdogSec +
  progress-tied beat) to messenger, memoryd, self-healer, LPP live loop; and land the
  PROPER lumen-agent fix its own drop-in comment stages (offload _memory_context to
  executor, revert 150s toward 45s). Beat must advance only on real work progress.

## 5. Where we are BETTER than reference practice (for honesty both ways)
Bi-temporal state store + episodic precheck (no homelab equivalent found), the holddown
machine-readable stop/restore contract (wq#967 — external practice has maintenance windows
but rarely license-to-stop semantics), aq responder tier auto-seeding runbook'd P0 tickets
(wq#965 — most shops stop at paging a human), and the intervention ledger (R4) measuring
the operator-load metric most small fleets never instrument.

## 6. Source list (graded)
A: sre.google/sre-book/monitoring-distributed-systems · sre.google/workbook/on-call ·
sre.google/sre-book/postmortem-culture + workbook/postmortem-culture ·
sre.google/static/pdf/login_spring17_09_lunney.pdf (postmortem AIs) ·
kubernetes.io/docs/concepts/workloads/pods/probes · freedesktop.org sd_notify(3) /
systemd.service(5) · training.promlabs.com (watchdog alerts / meta-monitoring).
B: incident.io on-call best practices 2026 · devops.com on-call rotation 2026 ·
squareops alert-fatigue framework · oneuptime heartbeat/dead-man + systemd watchdog guides ·
blog.ediri.io Prometheus dead man's switch · soundcloud backstage SLO alerting ·
bigiron.cc 2026 homelab observability stack · agtechlab.dev observability VM ·
pistack.xyz cron-monitoring comparison · hostmycode systemd watchdog VPS ·
christran.io + homelabstarter.com NixOS homelab · illustris.tech nix+ansible.
C: r/homelab-style individual setups (rwaltr/home-ops, ifd3f/infra) — corroborative only;
no load-bearing claim rests on grade C.
