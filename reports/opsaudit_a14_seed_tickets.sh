#!/usr/bin/env bash
# opsaudit_a14_seed_tickets.sh — seed the execute-now section of the A14 quantum-leap plan.
#
# Companion to reports/opsaudit_a14_quantum_leap_plan.md §3/§4. This script creates ONLY the
# agent-executable tickets in that plan's §3. It never touches the §5 Frank items, never edits
# an existing ticket, and never closes anything.
#
# Dry-run by default: prints every `wq add` it would run and creates nothing. Pass --apply to act.
# NOT idempotent against a prior --apply run: `wq add` always creates. Use --check first — it
# reports which of these titles already exist on the board so a re-run does not duplicate them.
#
#   ./opsaudit_a14_seed_tickets.sh                    # the whole plan, dry
#   ./opsaudit_a14_seed_tickets.sh --check            # what is already on the board
#   ./opsaudit_a14_seed_tickets.sh --phase 1 --phase 2 --apply
#   ./opsaudit_a14_seed_tickets.sh --apply            # seed everything
#
# Phases (see the plan for the ordering argument):
#   0 land what is built   1 make the law consistent   2 turn on enforcement
#   3 spend headroom       4 fix worker success        5 lanes + maintenance window
#   6 evidence machinery   7 prove it moved
#
set -uo pipefail

WQ=/home/lumen/bin/wq
PLAN=reports/opsaudit_a14_quantum_leap_plan.md
APPLY=0
CHECK=0
PHASES=""

usage() { sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0; }

while [ $# -gt 0 ]; do
  case "$1" in
    --apply) APPLY=1 ;;
    --check) CHECK=1 ;;
    --phase) shift; PHASES="$PHASES $1" ;;
    -h|--help) usage ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
  esac
  shift
done

declare -A IDS=()
SEEDED=0 SKIPPED=0

want_phase() {
  [ -z "$PHASES" ] && return 0
  case " $PHASES " in *" $1 "*) return 0 ;; *) return 1 ;; esac
}

# resolve symbolic dep keys (T7,T10) to the ids captured earlier in this run
resolve_deps() {
  local out="" k
  for k in $(echo "$1" | tr ',' ' '); do
    [ -z "$k" ] && continue
    if [ -n "${IDS[$k]:-}" ]; then out="$out,${IDS[$k]}"
    else out="$out,<$k>"; fi
  done
  echo "${out#,}"
}

# t KEY PHASE LANE PRI ARCHETYPE EFFORT DEPKEYS TITLE DETAIL
t() {
  local key=$1 phase=$2 lane=$3 pri=$4 arch=$5 effort=$6 depkeys=$7 title=$8 detail=$9
  want_phase "$phase" || { SKIPPED=$((SKIPPED+1)); return 0; }

  if [ "$CHECK" = 1 ]; then
    if "$WQ" list 2>/dev/null | grep -Fq "${title:0:48}"; then
      echo "EXISTS  $key  $title"
    else
      echo "absent  $key  $title"
    fi
    return 0
  fi

  local deps; deps=$(resolve_deps "$depkeys")
  local -a cmd=("$WQ" add --lane "$lane" --pri "$pri" --archetype "$arch"
                --effort "$effort" --title "$title" --detail "$detail")
  [ -n "$deps" ] && cmd+=(--deps "$deps")

  if [ "$APPLY" = 1 ]; then
    local out; out=$("${cmd[@]}" 2>&1)
    local id; id=$(echo "$out" | grep -oE '[0-9]+' | head -1)
    if [ -n "$id" ]; then
      IDS[$key]=$id
      echo "[$key] seeded as wq#$id — $title"
      SEEDED=$((SEEDED+1))
    else
      echo "[$key] FAILED — $out" >&2
    fi
  else
    printf '[%s phase %s]\n  ' "$key" "$phase"
    printf '%q ' "${cmd[@]}"
    printf '\n\n'
  fi
}

echo "=== A14 seed plan (${PLAN}) — $([ "$APPLY" = 1 ] && echo APPLY || { [ "$CHECK" = 1 ] && echo CHECK || echo 'DRY RUN — nothing will be created'; }) ==="
echo

# ---------------------------------------------------------------- phase 0
t T23 0 build 3 mechanical-edit medium "" \
"[ops-audit] A14-T23 Merge the four landed-but-unmerged ops-audit PRs and enable dispatch_config_lint" \
"A14 plan §3 phase 0. Four ops-audit PRs are open while their infrastructure is already live on the box: #52 (A3 node_exporter deploy script + Grafana dashboard), #53 (A13 flow-metrics exporter + frozen baseline), #86 (A9 evidence gate), #92 (wq#845 dispatch_config_lint). Verified live during A14: node_exporter answers on 100.117.66.102:9100 with 28 lumen_queue_* series, and /home/lumen/bin/wq already carries the --no-artifact gate. The repo record trails the running fleet. SCOPE: review and merge all four, then enable dispatch_config_lint.py in lumen-foreman-health (wq#845 shipped it DISABLED) and confirm one health pass runs it. DONE WHEN: the four PRs are merged, dispatch_config_lint runs on the health timer, and one pass output is quoted in the closing notes."

# ---------------------------------------------------------------- phase 1
t T1 1 build 1 mechanical-edit high "" \
"[ops-audit] A14-T1 Sweep the retired reboot/approval rules from all 6 stale copies" \
"A14 plan §3 phase 1; source A7 (reports/opsaudit_a7_rule_corpus.md) VERDICT + §6 step 1. Frank granted full autonomy 2026-08-22 and it was written into instincts.yaml:96-101 in exactly ONE file. Every other copy still instructs agents to halt: change-safety.md:49-54 (G4 HALT) and :78-83 (never reboot), session-start.md:40-41, UNIFIED_DISPATCH.md:96-101, manifest.yaml:35 (reboot: red), and instincts.yaml:17 — which contradicts instincts.yaml:97 EIGHTY LINES LATER IN THE SAME FILE (confirmed still live 2026-08-23). SCOPE: (a) delete instincts.yaml:17 outright, superseded by :97; (b) point the other five copies at instincts.yaml:97; (c) manifest.yaml becomes reboot_thor: red, reboot: green; (d) add A7 §4.8's two RETIRE lines to RETIRED_RULES.md so dispatch-lint catches recurrence. CONSTRAINT: the Thor carve-out survives VERBATIM — Thor is the one hard rule. Do NOT carry the wq#383 CVE argument into any rewrite; A2, A7 and A11 all verified wq#383 closed 2026-08-01 and the argument is void. DONE WHEN: dispatch-lint goes red on the new RETIRE lines, the copies are fixed, and it goes clean again — quote both runs."

t T2 1 build 1 mechanical-edit high "" \
"[ops-audit] A14-T2 Reword R2 to per-token-billing across 3 files + manifest capability rename" \
"A14 plan §3 phase 1; source A7 R2 (the clearest reword in the corpus). Three live statements are mutually inconsistent: change-safety.md:81 'Never call Anthropic APIs' (RED), instincts.yaml:51 'the API platform / API key is for Claude Code which we do not use', and the state store's anthropic_subscription.auth_method 'OAuth via claude-code CLI on Max subscription'. instincts.yaml:51 is factually FALSE about this fleet: /home/lumen/.npm-global/bin/claude is @anthropic-ai/claude-code, and fworker.py:628-630 pops ANTHROPIC_API_KEY before spawning. Agents read the RED wording as 'avoid Claude' and re-route engineering to weaker rungs on false cost grounds; Frank has corrected this repeatedly. SCOPE: apply A7 R2's drop-in text to change-safety.md:81, rename the manifest.yaml:41 capability anthropic_api -> anthropic_token_billing (KEEP red), fix session-start.md:41's DO-NOT row, and correct instincts.yaml:51. CONSTRAINT: the billing protection must survive verbatim — no ANTHROPIC_API_KEY in any service, no anthropic/* model entry with api_key in LiteLLM, no API credit top-up. DONE WHEN: all four edits are in and no file still says 'never call Anthropic APIs' unqualified."

t T3 1 build 2 mechanical-edit medium "" \
"[ops-audit] A14-T3 Fix instincts search->recall and strike the drained persona headline" \
"A14 plan §3 phase 1; source A7 §3 C10 and C12. TWO one-line fixes. (1) instincts.yaml:26 tells agents to run 'lumen_episode.py search <term>'; the binary rejects it — 'invalid choice: search (choose from backfill, recall, precheck, commit)'. A standing instinct that fails on execution. Correct verb is recall. (2) UNIFIED_DISPATCH.md:14-22 leads with 'CURRENT HEADLINE PRIORITY (Frank, 2026-07-16): PERSONA EVOLUTION CHAIN' while :37 of the same file records the fable lane as drained 2026-08-14. Every agent reading UNIFIED leads with finished work. SCOPE: the two edits, nothing else. DONE WHEN: lumen_episode.py recall runs from the instinct as written, and UNIFIED_DISPATCH.md's headline names live work or none."

t T4 1 build 1 novel-design high "T1" \
"[ops-audit] A14-T4 Replace G4 with evidence-not-permission text (A6 §6, no approvals.yaml)" \
"A14 plan §3 phase 1 and §1 C-A; sources A6 §6 and instincts.yaml:96-98. A6 designed a risk-tiered class table (Class S/B/A/RED) plus an approvals.yaml Frank would own. That permission half is SUPERSEDED: Frank's 2026-08-22 grant is wider than Class S, and building approvals.yaml would re-create as a data structure the gate instincts.yaml:98 forbids. The EVIDENCE half survives and is what G4 should now say. SCOPE: replace change-safety.md's G4 with A6 §6's drop-in text, with its Class S / Class A framing replaced by 'evidence required; if the evidence cannot be produced, do not act'. Keep verbatim: RED tier untouched; a conclusion may NEVER share a batch with the check that establishes it (fq#592, then fq#601 the same night); caps and alarms in the path must have been seen to fire on a planted positive (fq#10, fq#561). CONSTRAINT: do NOT create approvals.yaml. Do NOT invent a new tier. Hold the playbook under its documented ~2K-token budget (change-safety.md:98). DONE WHEN: G4 reads as evidence preconditions, no file references approvals.yaml, and the RED tier diffs clean."

t T5 1 build 3 mechanical-edit low "" \
"[ops-audit] A14-T5 Restate R5's reason as the G0 host guard, not implied capacity" \
"A14 plan §3 phase 1 and §1 C-E. session-start.md:41's DO-NOT row bans 'build on non-Lumen nodes' with no stated reason and NO TRACEABLE ORIGIN (A7 §5 searched episodes, corpus grep and the ticket board — nothing). A5 has since MEASURED the assumed reason and found it false: Lumen runs at ~15% memory and ~13% CPU with 2 fworkers and 32 containers, PSI zero across CPU, memory and IO. The rule survives on a different and better argument, which A7 supplies: Lumen is the only host with the G0 host guard ([ \"\$(hostname)\" = lumen ] || exit 9) baked into every playbook, so builds elsewhere would run outside every existing safety gate. SCOPE: one line — restate the rule's reason in place so the next reader knows what it protects. Do NOT retire the rule; A5 measured it and the answer was keep. DONE WHEN: the rule names the host guard as its reason."

# ---------------------------------------------------------------- phase 2
t T6 2 build 1 standard-engineering high "" \
"[ops-audit] A14-T6 Schedule dispatch-lint --universal as an unprivileged --user timer" \
"A14 plan §3 phase 2 and §1 C-C. F7's enforcement gap is STILL OPEN, verified 2026-08-23: /home/lumen/bin/dispatch-lint gates the check behind --universal (line 199) and the installed /etc/systemd/system/lumen-dispatch-lint.service ExecStart is still bare dispatch-lint (line 18). A8 fixed all seven REQUIRE violations and the heartbeat-TTL disagreement, added a --universal preflight to wq-lane-init, and STAGED /home/lumen/bin/lumen-dispatch-lint.service.staged — uninstallable because /etc/systemd/system is root-owned and sudo is on the house deny-list. SCOPE: take the path A3 already proved for node_exporter — deploy a systemd --user timer owned by lumen that runs 'dispatch-lint --universal' daily, needing no root. Keep the staged root unit in place as the fallback. Do NOT flip --universal to default-on in this ticket (see plan §6 N5): the timer must run clean for a week first, and A8 fixed the corpus so it starts green. DONE WHEN: 'systemctl --user list-timers' shows the unit, one manual run exits 0 on a clean corpus and nonzero on a deliberately-broken copy, and both outputs are quoted."

# ---------------------------------------------------------------- phase 3
t T7 3 build 1 standard-engineering high "" \
"[ops-audit] A14-T7 PSI-based tight_mode trigger in the foreman dispatcher" \
"A14 plan §3 phase 3; source A5 §5. The dispatcher's only tight_mode trigger today is governor_pressure (foreman_dispatcher.py:368) — it guards the subscription budget and does not know the box exists. A5 measured Lumen with PSI at zero across CPU, memory and IO, and recommends a contention trigger before concurrency rises. SCOPE: read /proc/pressure/memory at candidate-scan and apply A5's thresholds — memory some avg60 > 5% -> tight_mode (max_spawns=1, min_pri=1); > 15% -> hard stop, no new spawns until clear; memory full avg60 > 0% -> hard stop + ntfy on lumen-fleet. The pressure-guard timer already reads /proc/pressure/memory; A5 suggests a JSON state file the dispatcher checks rather than a second reader. CONSTRAINT: fail-open on a missing/unparseable file is WRONG here — fail to the safe side (treat unreadable as normal only if you log it loudly; never treat it as permission to spawn more than the ceiling). DONE WHEN: a planted PSI value above each threshold is shown to flip the pass (A6 P5: a cap nobody has watched trip is a comment), and the transcript is quoted."

t T8 3 build 2 mechanical-edit medium "T7" \
"[ops-audit] A14-T8 Raise cloud_ceiling 3 -> 5" \
"A14 plan §3 phase 3 and §1 C-D. GATED ON T7 — the PSI brake lands first. A5 §4: at 5 concurrent fworkers Lumen carries ~12 GB total RSS with 47 GB available and 8.8 HT cores per (API-bound) worker; 5x8G MemoryMax = 40G under 48G available; PSI risk zero. Spec F4: the fleet runs at 14% of weekly budget, so the ceiling is set for a plan we do not have. Honest counterweight from A1: at a measured 30% standard-engineering success rate, two more slots buy ~0.6 more successes and ~1.4 more failures per pass — still net positive at this budget utilisation, but this is NOT a substitute for the worker-success work in phase 4. SCOPE: cloud_ceiling 3 -> 5 in foreman/dispatcher/config.json. Do NOT go to 7 (plan §6 N7). DONE WHEN: two dispatcher passes have run at ceiling 5 and PSI is quoted from during the second. ROLLBACK: set it back to 3; takes effect on the next 20-minute pass."

t T9 3 fleet 2 standard-engineering medium "" \
"[ops-audit] A14-T9 Stop and disable the unused lumen-rerank.service (329 MB, 0 callers)" \
"A14 plan §3 phase 3; source A5 §6 P1. lumen-rerank.service holds 329 MB RSS for a rerank model with ZERO call sites in lumen_recall2.py (confirmed by the wq#569 audit). Verified still 'active' 2026-08-23. CONSTRAINT, and it is the point of this ticket: this is a systemctl stop|disable, which harness/guard.sh:88 denies to fworker sessions by design and which A6 deliberately excluded from every standing class — fq#45 cost 62 hours of fleet downtime and it was an unpaired stop. So: run this from a Commander/interactive path, NOT from an fworker; write the paired re-enable command down BEFORE stopping; and re-verify 0 call sites at execution time rather than trusting wq#569. DONE WHEN: the unit is stopped and disabled, the paired re-enable command is recorded in the ticket notes, free RAM before/after is quoted, and nothing that greps for the rerank endpoint has started erroring."

# ---------------------------------------------------------------- phase 4
t T10 4 build 1 rca-investigation xhigh "" \
"[ops-audit] A14-T10 Count real assistant turns, not hook frames" \
"A14 plan §3 phase 4 — THE binding constraint. fworker.py:110-113 already names this as the blocker that forced ceilings to a placeholder 600. A1 §2 measured the damage: 66 attempts were killed by fworker's own TURN_BACKSTOP_BUFFER=5 supervisor kill at EXACTLY max_turns+5, every single time, never earlier and never later; turns/max_turns at kill ranged 1.042-1.200, i.e. the CLI ran 4-20% past its own ceiling (not double, as the old code comment guessed); and even on clean self-stop the CLI's counter overshoots the requested ceiling by up to 27%. Until the count is real, every ceiling number is measured against a miscount and T11 cannot be trusted. SCOPE: audit the turn-accounting path end to end — what the CLI reports, what the hook stream contains, what fworker counts — and make fworker count real assistant turns. Quote the before/after count on the same replayed session. DONE WHEN: a replayed attempt yields a turn count that matches the assistant turns actually taken, with the discrepancy against the old counter quantified."

t T11 4 build 1 mechanical-edit high "T10" \
"[ops-audit] A14-T11 Set ARCHETYPE_MAX_TURNS to A1's measured table" \
"A14 plan §3 phase 4. GATED ON T10 — do not set ceilings against a miscount. Today every archetype sits at 600 (fworker.py:114-121), lifted wholesale as a stopgap. A1 §6 sized the real table off measured SUCCESS turns_used with headroom: mechanical-edit 70 (max observed 56), novel-design 480 (369), probe-build 240 (187, low confidence n=2), rca-investigation 210 (162), research-synthesis 190 (146), standard-engineering 370 (286, n=38), verify-pass 200 (155). 'research' stays >=40 and gets re-measured — 0/1 successes is too few to distinguish a broken archetype from an unlucky sample. SCOPE: apply the table. FALSIFIABLE PREDICTION to record in the notes (A1's own): standard-engineering FALSE_COMPLETE drops below its current 23% attempt-level rate, but only WEAKLY — A1 measured the KILLED-then-FALSE_COMPLETE mechanism on just 6 of 22 FALSE_COMPLETE tickets, so ceilings reach only that minority. Falsified if unchanged or higher after 20 more standard-engineering attempts. ROLLBACK: restore 600 in the dict literal and re-dispatch."

t T12 4 build 1 standard-engineering high "T10" \
"[ops-audit] A14-T12 Escalate attempt-2's ceiling x1.5 after a TURNS or KILLED attempt 1" \
"A14 plan §3 phase 4; source A1 §4. MAX_ATTEMPTS=2 exists, but 76/76 (100%) of within-dispatch retries reused the IDENTICAL max_turns on attempt 2, and only 3/76 (4%) recovered under it. Across all attempts, 36/44 (82%) of later successes ran under a DIFFERENT ceiling than the attempt before them. A1's conclusion: the measured recovery lever is raising max_turns before the retry, not retrying per se. SCOPE: when attempt 1 fails via TURNS or KILLED/turns_exceeded, dispatch attempt 2 at 1.5x attempt 1's ceiling; leave FALSE_COMPLETE retries alone (A1 §3 found turn exhaustion is not their main driver — 6/22). Keep MAX_ATTEMPTS at 2. FALSIFIABLE PREDICTION for the notes: escalated attempt-2 success exceeds the 4% same-ceiling baseline; falsified if the next 20 escalated retries succeed at <=4%. DONE WHEN: the escalation is in the retry path and one real escalated retry is shown in the attempt records."

# ---------------------------------------------------------------- phase 5
t T13 5 build 1 standard-engineering high "" \
"[ops-audit] A14-T13 wq-lane-init must register new lanes in the dispatcher config" \
"A14 plan §3 phase 5; source A10 §3/§4a — the root cause of F2. wq-lane-init creates a <LANE>_DISPATCH.md, a board file, and runs dispatch-lint. It does NOT add the lane to foreman/dispatcher/config.json, does not validate that it exists there, and prints no warning. foreman_dispatcher.py:82 only scans open tickets in config-enabled lanes, and wq add accepts any lane name, so tickets accumulate silently — the build lane was absent entirely and 27 open build tickets were never dispatcher-visible. A10 measured 24 open tickets across 7 lanes still invisible. SCOPE: on lane creation, add a config.json entry with enabled:false and print the exact enable command, so the operator makes an EXPLICIT choice at creation time instead of the lane silently not existing. Pair with wq#845's dispatch_config_lint, which detects the drift after the fact (enable it via T23). DONE WHEN: a throwaway lane created by wq-lane-init appears in config.json as disabled with the enable command printed, and the throwaway is cleaned up."

t T14 5 build 2 mechanical-edit medium "T13" \
"[ops-audit] A14-T14 Enable the fleet/memory/pokemon lanes; fold fleet-spend and ops" \
"A14 plan §3 phase 5; source A10 §4c/§4d. Open tickets invisible to the dispatcher today: fleet 7, pokemon 8, memory 3, research 3, fleet-spend 1, ops 1, quest-repair-research 1. SCOPE: enable fleet, memory and pokemon (same worker class, same archetypes as build); fold fleet-spend into fleet and ops into build (single-ticket lanes); leave research disabled (article-research uses a Frank-pasted dispatch, not foreman) and quest-repair-research disabled (hardware RE, needs human judgment); set fleet-fires requires_approval back to true — the code already enforces an approval FILE regardless of the flag, so the config is merely misleading today. HONEST COUNTERWEIGHT: this makes ~18 more tickets dispatchable at A1's measured 30% success rate, so it is sequenced AFTER phase 4 for a reason. PREDICTION: throughput in the newly-enabled lanes goes from ~0 dispatcher-driven completions to nonzero. ROLLBACK: each lane is one JSON boolean."

t T15 5 fleet 2 standard-engineering high "" \
"[ops-audit] A14-T15 Build fleet-maint-assemble (A11 phase 1)" \
"A14 plan §3 phase 5; source reports/opsaudit_a11_maintenance_window.md §4 phase 1 (on branch docs/wq761-maintenance-window — READ IT FIRST; wq#761 is marked INFRA_CRASH but its report landed). A11 measured that security apt already lands unattended (apt_security=0 on all 13 reachable nodes) and that what does NOT land is everything unattended-upgrades will not touch: application and container versions, firmware, the non-security apt tail, tailscale drift on two nodes, and the reboots that make kernel updates real. SCOPE: build fleet-maint-assemble as A11 specifies — runs unattended at T-24h, GitHub-authenticated gh_latest lookups, a completeness gate that EXITS NON-ZERO rather than reporting a partial scan as clean, per-node advisory classification, a batch JSON artifact, and one user-lane ticket carrying the batch. CONSTRAINT: fwupd is EXCLUDED by default (A11 §6) — list firmware, never apply it. A KEV-listed or actively-exploited advisory is flagged SEC-URGENT and opens its own ticket immediately; the window must never become a reason to defer. DONE WHEN: one real assembly run produces a batch JSON and the completeness gate is shown to fail on a deliberately-unreachable host."

t T16 5 fleet 2 standard-engineering high "T15" \
"[ops-audit] A14-T16 Build fleet-maint-run (A11 phase 3) — A6's Class B, done properly" \
"A14 plan §3 phase 5; source A11 §4 phase 3/3b and §5 ordering law, plus A6 §3 B1. This is the one place the campaign builds batching: ONE approval covers a named target set for a named window, and the verification NEVER batches — every target runs its own G0-G3 and G5-G7. A6 P2's source is why: fq#32's GPU-B power gating was correct on Wadjet and drove pci_channel_io_perm_failure plus overheating on Isis and Hathor, because amdgpu binds both GPUs there. Generalising a verified change to a second box is a NEW action with its own G1, never a continuation. SCOPE: ordered rolling execution per A11's §5 ordering law, stop-on-first-failure, auto-rollback per A6 §5's trigger table (unit not active at T+60s and T+300s, or NRestarts incremented since G5 -> supervisor executes the paired restore), and phase-3b RECORD that always runs, even on a stop. NOTE: A6's precondition runner does not exist yet (T18); until it does, phase 3 runs those gates as its own narrower code and says so. When T18 lands, call it and delete the local copies. DONE WHEN: a dry-run against the T15 batch prints the full per-target gate sequence and the stop path is demonstrated."

t T17 5 fleet 2 standard-engineering medium "" \
"[ops-audit] A14-T17 Fix fleet-update-check's silent-failure bug" \
"A14 plan §3 phase 5; source A11 §3c — the assembler has a silent-failure bug AND IT FIRED DURING A11's OWN MEASUREMENT PASS. SCOPE, exactly as A11 states it: (a) read /home/lumen/.secrets/github-lumen.token so authenticated gh_latest lookups stop failing anonymously; (b) never render a question mark as data — an unknown must be distinguishable from a known-current version, because today 'could not check' and 'no update' print the same; (c) fix the llama.cpp tag parser. This is the F9 SILENT_GAP class that A6 measured as the single largest bucket in the failure queue (15 of 66), so treat 'reports success when it could not check' as the actual defect, not a cosmetic one. DONE WHEN: a run against a deliberately-unreachable endpoint reports an ERROR rather than 'up to date', and the authenticated path is shown returning a real tag for gitea and llama.cpp."

# ---------------------------------------------------------------- phase 6
t T18 6 build 1 novel-design xhigh "" \
"[ops-audit] A14-T18 Precondition runner as a separate process (A6 P0/P1/P3)" \
"A14 plan §3 phase 6; source A6 §4. Under full autonomy these preconditions matter MORE, not less: the halt that used to be an accidental brake is gone. Each precondition is produced by a SEPARATE PROCESS whose non-zero exit aborts the run; none may be self-reported by the acting agent. P0 — no conclusion in the same batch as its check. Source: fq#592 (the pipeline printed 'MISMATCH — do not reseed' and reseeded anyway through unconditional command sequencing) and fq#601 THE SAME NIGHT (an episode commit and an ntfy claiming BYTE-EQUAL batched with the parity check that 403'd). P1 — restore-proven backup for THIS target in THIS run, to the wq#566 standard: restore to a scratch path, hash-match against live, and LOAD it if it is loadable. Not 'a backup job succeeded': fq#22 had /home/lumen/pokemon in 0 of 393 restic snapshots while the jobs ran green for months. P3 — a rollback command exercised in this run; dry-run counts, untested does not (fq#23: a unit left as .service.new and assumed to be collecting data for 3 days). SCOPE: build the runner. CONSTRAINT: it ships with its own planted-positive test (T20) or it does not ship — an unverified precondition runner reproduces fq#592."

t T19 6 build 1 standard-engineering xhigh "T18" \
"[ops-audit] A14-T19 Supervisor-owned paired restore in fworker (A6 P4)" \
"A14 plan §3 phase 6; source A6 §4 P4 and §5. A6 calls this 'the single largest build dependency in this proposal and it does not exist today'. The reason is fq#614: headless workers die silently mid-ticket — twice in one day — leaving claims and slocks dangling with nothing alerting. So the acting agent may not be alive to roll back, and a rollback the agent owns is not a rollback. SCOPE: the paired restore is owned by FWORKER, not by the agent — registered before G5, cleared on a clean G7, executed by the supervisor on session death or watch-window failure. Wire A6 §5's trigger table: unit not active at T+60s AND T+300s, or NRestarts incremented since G5 -> supervisor executes the restore (fq#23, fq#45); session dies with an unretired registration -> supervisor executes and files an aq alert (fq#614, fq#562); disk% or INODE% crossed since G5 -> halt the class for the window (fq#49 — 22.6M orphaned MinIO stage files took root to 81% disk and 87% inodes; inode% is the one that gets missed). DONE WHEN: a deliberately-killed worker session is shown to have its restore executed by the supervisor, with the event stream quoted."

t T20 6 build 1 standard-engineering high "T18" \
"[ops-audit] A14-T20 Planted-positive tests for every cap and alarm in the path (A6 P5)" \
"A14 plan §3 phase 6; source A6 §4 P5. 'A cap nobody has watched trip is a comment.' fq#10: DAILY_POST_CAP=1 was present in the code and ineffective, and 25 posts shipped against Frank's explicit REPEATED stop. fq#561: a recall-bench drift alarm was installed and enabled and has never been shown to fire, with no baseline and no planted trap. SCOPE: enumerate every cap, threshold and alarm in the dispatch/worker path — governor pressure, the new PSI thresholds (T7), TURN_BACKSTOP_BUFFER, MAX_ATTEMPTS, the wq evidence gate (T23/A9), dispatch-lint's exit codes (T6), the precondition runner's abort (T18) — and give each a planted positive that proves the abort path fires. A7's own method is the model: it did not trust dispatch-lint's clean default output, ran it with --universal, got 8 findings, and that is how the default-off gap was found. A sweep that has never gone red has not been shown to work. DONE WHEN: each cap has a recorded planted-positive run, and any cap that did NOT fire is filed as its own defect rather than quietly fixed in this ticket."

# ---------------------------------------------------------------- phase 7
t T21 7 build 2 research-synthesis high "" \
"[ops-audit] A14-T21 Per-phase dated re-baselines against A13's frozen artifact" \
"A14 plan §3 phase 7 and §1 C-G. A13 froze reports/opsaudit_a13_baseline_20260821.json (sha256 3846b1f8a6d1..., cutoff 2026-08-21 18:06:42) and its discipline is right: NEVER edit that file in place — other reports already cite its sha. SCOPE: re-baseline into a NEW dated artifact at the end of each A14 phase, not once at the end of the campaign; thirteen changes landing in overlapping waves against one 'after' snapshot is unattributable and would repeat the fail-open shadow-run defect A13 was written to avoid. Compare like with like: A13's frozen 22% attempt-level success (149 attempts, frozen) versus a like-for-like recompute, NOT against A1's live 30% recompute over a larger dataset. Watch lumen_queue_reopened_total — the wq#841 structural series that starts 2026-08-23 — rise after A9's evidence gate takes hold, then flatten within a month; do NOT compare against the retracted 1.74% rework marker, which erased itself. NOTE the already-measured movement: time_parked_on_frank 74 -> 30 and blocked 52 -> 14 as of 2026-08-23, with blocked_age max_d still 42.1 — the count moved, the tail did not."

t T22 7 build 2 research-synthesis xhigh "" \
"[ops-audit] A14-T22 Re-run A12 (external practice) — the campaign's one missing dependency" \
"A14 plan §5. wq#762 (A12 CONSULT external practice — what a 15-node agent fleet should have that we do not) produced NO report: the ticket is still claimed with notes 'INFRA_CRASH: KILLED after 2 attempts', and no opsaudit_a12_* artifact exists on any branch. Consequence, and it is why this is worth re-running: every recommendation in A1-A14 is INTERNALLY DERIVED — the campaign measured this fleet against itself. The plan is well-evidenced about whether our things work and silent about whether we are missing a category of thing entirely. SCOPE: A12's original scope, dispatched under the phase-4 ceilings and with an archetype whose measured success rate justifies it (A1: research-synthesis 16%, the worst in the table — consider splitting the work rather than re-running it whole). Deliver an external-practice comparison that names what a fleet this size normally has that we do not, and mark each item adopt / reject / already-have with reasoning. CONSTRAINT: do not re-litigate A1-A13's internal findings; this ticket's whole value is the outside view."

# ---------------------------------------------------------------- housekeeping
t T24 5 fleet 3 standard-engineering medium "" \
"[ops-audit] A14-T24 Pin container image digests for the :latest tag set" \
"A14 plan §4; source A11 §8. Several containers run the :latest tag — miniflux, actual-budget, ntfy, glance. 'Upgrade miniflux' and 'recreate miniflux' are therefore the SAME operation, which means an unrelated recreate silently upgrades it and a maintenance window cannot tell a deliberate upgrade from an accidental one. SCOPE: pin image digests for the latest-tag set so upgrading becomes an explicit act, and record the pinned digests where fleet-maint-assemble (T15) can diff against them. DONE WHEN: each named container references a digest, one recreate is shown NOT to change the running image, and the pinned set is recorded."

t T25 1 build 3 mechanical-edit low "" \
"[ops-audit] A14-T25 Correct HANDOFF.md §3 item 1 — wq#383 closed 2026-08-01" \
"A14 plan §1 C-F; source A11 §9 and A7 §0, independently confirmed by A2 §1. The campaign spec, HANDOFF.md and several downstream documents cite wq#383 as 'FRANK DECISION PENDING since 2026-07-19 — 33 days of known-CVE exposure'. It was executed 2026-07-31 and closed 2026-08-01 (done_at), TWENTY DAYS BEFORE the audit was written; its notes read 'gitea 1.26.4 + key rotation on Anubis; tailscale fleet complete; cloudflared 2026.7.3. All endpoints verified.' The current version of that exposure is wq#464 (gitea 1.26.4 -> 1.27.1, vaultwarden behind), 11 days old. SCOPE: correct HANDOFF.md §3 item 1 and any other live document repeating the 33-day claim. DONE WHEN: no live document still cites wq#383 as pending."

echo
if [ "$CHECK" = 1 ]; then
  echo "=== check complete ==="
elif [ "$APPLY" = 1 ]; then
  echo "=== seeded $SEEDED ticket(s) ==="
  echo "Symbolic deps resolved within this run only. If you seeded phases separately,"
  echo "cross-phase deps printed as <TKEY> and need 'wq edit <id> --deps <real-id>'."
else
  echo "=== DRY RUN — nothing created. Re-run with --apply to seed. ==="
  echo "Deps shown as <TKEY> resolve to real ticket ids at apply time."
fi
[ -n "$PHASES" ] && echo "(phase filter active:$PHASES — $SKIPPED ticket(s) skipped)"
exit 0
