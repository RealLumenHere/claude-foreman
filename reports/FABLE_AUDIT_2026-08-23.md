# FABLE AUDIT — 2026-08-23 (session fable-audit-1c6ce5)
## Scope: 142 tickets done since 2026-08-22 16:00Z + known-broken list B1-B10 + cross-cutting checks
Method: all 142 done-ticket notes read in full (bulk dump), ~20 deep-verified live (grade A),
remainder graded on artifact evidence in notes (grade B) — the 06:42Z verify sweep + the
[verify] ticket lattice already covered most; this audit verified the verifiers on samples.

## HEADLINE COUNTS
- Audited: 142 done tickets. PASS (real deliverable): 135.
- Needed remediation: 5 → remediated in-session: 5 (wq#772/633/849 PR-landing + tree restore;
  repo-visibility spam; junk tickets). Reopened from crash-blocked: 2 (wq#869, #879).
- Needing research → build tickets created: 4 (wq#898, #899, #900, #901) + 1 Frank-hands
  user ticket (wq#902). Orchestrator ntfy'd on each at creation time.
- CRITICAL findings: 1 (LiteLLM auth outage — pre-known, wq#896, in-flight at audit close).
- HIGH findings: 3. Regressions found: 1 (primary-tree branch hijack, recurring class).

## CRITICAL
C1. LiteLLM master-key 401 outage (B1, wq#896). Live at audit start AND close. A foreman
    fworker (sonnet-5) claimed #896 at 15:00Z, one minute before this audit registered;
    lease live, worker active — NOT poached per coordination rules. Audit verified: master
    key still 401 at 15:15Z, gateway alive (200), spend risk low per #896's measurements.
    Root class recorded on wq#719: the SPEND-1 migration enumerated callers from config,
    not live traffic, and missed sentinel probes + the master key itself.
    RESIDUAL: if the fworker false-completes, #896 must be re-driven — see OUTSTANDING.

## HIGH
H1. wq#772 closed done with PR#105 UNMERGED — the dispatcher was still reading the live
    working tree (the exact defect the ticket exists to fix), and worse, the PRIMARY
    /home/lumen/foreman tree was checked out on research/wq633-849-headroom-judge: the
    dispatcher/self-heal/health timers were running from a worker's feature branch.
    REMEDIATED: PR#113 + PR#105 squash-merged (Gitea API, under slock foreman-repo),
    primary restored to main@3907ef3, zero tracked diffs. Deployed-behavior check =
    config_sha line on the next dispatch pass (see could-not-verify if absent).
    CLASS ticket wq#900 (guard against branch-switching in primary trees; second same-day
    instance was /home/lumen/bin on feat/lumen-guardian, wq#867).
H2. Half-landed PR backlog: ~28 open PRs on claude-foreman + others while their tickets
    are done; some are live-code drift (PR#80 dispatcher_health deployed-but-unmerged =
    lost on any clean checkout; PR#112, #107, #92, #33). Some are stale refs to squashed
    content (pattern confirmed on #733). CLASS: wq#759 evidence gate shape-checks PR URLs
    but not merge state. Ticket wq#899 with the full list and per-PR disposition.
H3. Isis single-slot saturation starves the model watchdog: qwen36-local reported "down"
    since ~06:28Z while actively serving 686 generates/10h (LPP brain + local-first rung).
    Audit reproduced the probe timeout and DISPROVED the "hang" reading via live decode
    timing (narrative-kill). False-down alerts for 9h. Design ticket wq#898 (parallelism
    vs probe timeout vs probe model — RAM tradeoffs, no improvisation). Also found: a
    client emitting malformed POST /api/generate/api/show (404) — caller TBD in #898.

## MEDIUM / RESOLVED IN SESSION
M1. Questfree exposure (B8): frankstupak/questfree made PRIVATE by this audit per the
    brief's explicit instruction; sentinel auto-resolved it (cleared=1). Recorded tension:
    the sentinel's own post-winforge doctrine says visibility is Frank-only — the audit
    brief's newer instruction was followed; reversible in one click if wrong.
    RealLumenHere/questfree: cannot be changed from the fleet (no RealLumenHere credential
    exists on Lumen) → user ticket wq#902 (one click for Frank). Alert loop STOPPED:
    sentinel notify() converted to edge-triggered (skip re-ntfy while an open aq row
    tracks the violation; Class D never-publish still re-alerts every run). Patched with
    backup, py_compile, live-run verified ("edge-gated (open aq row)" observed).
M2. B10 storage-integrity FAIL 11:35–13:07Z: SLA sweep on unactioned aq rows for parked
    nodes (apis/hathor/nephthys/wadjet). Fixed by wq#883 (roster park + aq#800 resolved),
    audit re-verified sla=0 critical=False live. #883's premise ("nephthys never
    deployed") is factually wrong but the fix is correct regardless — noted on ticket.
M3. wq#886 hook was inert: pre-commit gate shipped but core.hooksPath never set on
    /home/lumen/bin. REMEDIATED (one-line config, verified). wq-drift-guard timer active.
M4. B2 mcp-pressure-guard: NOT dead — timer-fired oneshot running green every minute
    (inactive-after-clean-exit is normal). Brief claim stale / fixed by wq#829.
M5. B7 throttling: confirmed removed (commit 890afe3; floors 0/0/0 with do-not-restore
    note; cloud_ceiling=3 the only cap). AUTO-4 pacing code present but floors zeroed;
    #802 verify's PACING_NTFY_PRUNE_H=72 cap nit stands (cosmetic at shipped defaults).
    No re-introduced throttling found.
M6. B9 ntfy OOB backup path 429s while primary=200 → design ticket wq#901 (LOW/MED).
M7. Queue hygiene: junk tickets #877/#878 rejected; crash-blocked contradiction
    escalations #869 (CLAIM_ERROR corroboration vacuous) and #879 reopened to open —
    real defects were dying in blocked with only INFRA_CRASH auto-requeued (D4 residue).

## OTHER VERIFICATIONS
- wq#556 pokemon backups: PASS on grade-B evidence (snapshot 835be41b today, md5
  restore-verify in notes). B-list item closed.
- wq#830/831: #831 verify closed with EMPTY notes (evidence-gate miss, flagged); audit
  re-verified #830 directly — d14677e deployed, auto mapping gone. PASS.
- wq#857 VictoriaLogs: LIVE on Bes (9428=200, forwarders active) despite close-note
  claiming staged-only — deployed out-of-band post-staging. Contradiction tracked #879.
- wq#837's contradiction (evidence gate silently breaking fworker auto-close) already
  fixed on main (3e09a28 "raise on refused wq done instead of silent swallow").
- D5 commit identity: still ALL "Lumen Industries" (8/8 recent) — known-open, unchanged.
- Config drift (C): dispatcher/config.json + floors.json committed==deployed at audit
  time; litellm_config budget-enforcement comment-out owned by #896 worker to re-verify.
- Model routing: post-#779 haiku launches confirmed by #806's verify (3 real launches).

## TESTS RUN (selection)
Live master-key 401 repro ×3; Isis bounded-generate repro + journal decode-timing
disproof; sentinel patched-run live (edge-gate + auto-resolve observed); PR#105
mergeability + post-merge tree clean; foreman main ff + zero tracked diffs; wq
reopened_count/evidence-gate presence on live binary; questfree visibility via GitHub
API before/after; bes:9428 health; probe-storage-integrity current PASS.

## OUTSTANDING RISK / COULD NOT VERIFY
1. wq#896 outcome — worker still active at close; master key still 401 (expected until
   it lands). IF #896 shows done without probe-synthetic-llm PASS + per-caller key
   audit, treat as FALSE_COMPLETE and re-drive immediately.
2. RESOLVED post-report: dispatch pass fdisp-20260823T151715Z logged
   config_sha=3907ef3a... (the audit-landed main commit) — PR#105 is live and the
   dispatcher reads committed HEAD. Verified.
3. The 122 tickets accepted at grade B were not re-executed end-to-end; sampling found
   1 empty-notes closure (#831) and 1 unmerged-PR closure (#772) — extrapolated risk is
   what wq#899 exists to burn down.
4. restic pokemon accepted on #556's same-day restore-verified evidence, not an
   independent restic run (repo creds not re-derived to avoid credential printing).

## FRANK'S HANDS (queued, not gates)
- wq#902: privatize RealLumenHere/questfree (credential only he holds), or declare it
  intentionally public and it gets allowlisted.

Session actions log: PRs merged 113,105 (claude-foreman); foreman primary → main@3907ef3;
sentinel edge-trigger patch (+.bak); /home/lumen/bin hooksPath set; tickets 898-902
seeded; 869/879 reopened; 877/878 rejected; audit records appended to 12 tickets.
Services touched: repo-visibility-sentinel (oneshot, ran clean twice) — nothing left
stopped; Thor untouched.
