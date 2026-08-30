# R1 — instincts.yaml efficacy audit

**Ticket:** wq#922 · **Agent:** r1-instincts-84ae8f (claude.ai web session) · **Date:** 2026-08-26 (Pacific)
**Subject:** /home/lumen/instincts.yaml — 57 rules, 23,064 bytes, 5,309 tokens (tiktoken cl100k)
**Window:** last 72h (2026-08-23 19:55Z → 2026-08-26 19:55Z) — episodes, queue.db, reports, worker transcripts
**Prior art:** reports/opsaudit_a7_rule_corpus.md (wq#757, A7) — corpus-wide supersession audit. This report
does not repeat A7; it tests instincts.yaml specifically and lands the finding A7 missed.

---

## VERDICT

**The question "does it work" has a mechanical answer before it has a behavioral one: most of the
file is never delivered to anyone.**

1. `claude_context.sh:23` injects `head -c 4096` of a 23,064-byte file. That is the first ~22 of
   57 rules. **The other 35 — every rule added 2026-08-20 onward, including the post-mortem
   evidence rules, NO FAST SLOPPY PATCHES, Frank primacy, findings delivery, the FULL AUTONOMY
   grant, and THOR IS THE ONLY HARD RULE — are silently truncated out of every --core session.**
   The cut lands mid-rule, so what IS injected ends in corrupt YAML.
2. Foreman workers get **zero** instinct text. `grep -c 'PRODUCT-LIVENESS\|NO FAST SLOPPY'
   fworker-wq944-a1.jsonl` = 0; no reference to instincts.yaml anywhere in the fworker/router
   prompt path. A7's corpus map already recorded "enforced by: nothing (context injection only)" —
   and the injection itself only half-happens.
3. Consequence measured in the window: the retired reboot-gate rule (line ~17) sits INSIDE the 4KB
   cut and its retirement (line ~97) sits OUTSIDE it. Sessions are literally served the dead law
   and not its replacement. That is the most likely mechanism behind finding R5 below: 5+ new
   Frank-gates created on 2026-08-23, the day after the grant.

Where behavior in the window matches a rule, the cause is almost always something else that is
actually delivered — audit prompt text, the verify-ticket archetype, dispatch instructions, or the
FALSE_COMPLETE harness. The rules that "work" work because they were copied into the delivery
path; the file itself is decoration for 35/57 entries and a stale-law server for the rest.

---

## 1. Token cost per session

| Measure | Value |
|---|---|
| Full file | 23,064 B = **5,309 tokens** (cl100k) |
| Actually injected via --core | first 4,096 B ≈ **950 tokens**, ending mid-rule |
| Injected into fworker sessions | **0 tokens** |

Cost if the full file were loaded, priced per SESSION_ECONOMICS.md (wq#734): first load = 5.3K
input-equivalents (cache write 2× → 10.6K weighted once), then carried at cache-read 0.1× ≈ 531
input-equivalents per subsequent turn. Against SESSION_ECONOMICS' finding that a median build
session ≈ 1% of the weekly window, a fully-injected instincts file is a rounding error per
session (<1% of a median session's weighted spend) — **cost is not the problem; delivery and
composition are.** The 4KB guard is solving a problem the economics say barely exists, and paying
for it with the autonomy grant.

## 2. Five-rule empirical test (72h evidence)

| Rule | Inside 4KB cut? | Verdict | Evidence |
|---|---|---|---|
| NEVER REPORT A COMPLETED INVESTIGATION BY TITLE | NO — never injected | **UNTESTABLE from disk / adherence coincidental** | Chat-side behavior isn't on disk. Proxy: verify-archetype tickets in-window do open artifacts and report contents (wq#936 notes: full cold re-review, adversarial fuzz results, not a title). But that behavior is driven by the verify-ticket prompt archetype, which IS delivered — not by this rule, which isn't. |
| A FINDING THAT NAMES A DEFECT BECOMES A TICKET, SAME TURN | NO | **FOLLOWED (by other means)** | wq#948 key-leak found 00:39, remediation #949 seeded 00:58, verify #950 00:59 (same hour). wq#944 seeded from the #909 finding. Episodes: "wq#864 seeded (rename path defect)", "wq#842-847 seeded", "wq#887-895 seeded". Driver: audit prompts (FLEET_OPS_AUDIT, this campaign's own dispatch text: "Every defect -> ticket same turn") — the rule text reached workers via prompt copies, not via instincts.yaml. |
| NEVER TOUCH THOR | NO (the hard-rule form). The related injected rule is only the stress7 tripwire | **FOLLOWED** | wq#664 Thor-bell work (13:08Z 08-23) was diagnosis + code landing in claude-foreman on Lumen (notify_sinks.py, D-Bus read path); no Thor service restart, reboot, or interrupt appears in episodes, notes, or reports in-window. Protection in force = prompt copies ("Never touch Thor" is in the campaign dispatch and R-section prompts), not the file. |
| TEST TO FAILURE BEFORE DECLARING DONE | NO | **MIXED — followed by strong workers, violated by weak ones, net saved by external enforcement** | Followed: wq#936 (161/161 + adversarial fuzz of every fail-safe path), dispatch_config_lint "tested to failure", rdv-soak 8/8, wq#595-class "PROVEN RED twice". Violated in-window: wq#913 FALSE_COMPLETE ×2, wq#325 ×3, wq#362 ×2, wq#600 ×2 — workers declared done without the thing being done; the harness FALSE_COMPLETE detector (external) caught them. A rule whose adherence depends on an external catcher is not functioning as an instinct. |
| NO FRANK-GATES (grant 2026-08-22) | NO — the entire autonomy block is beyond the cut | **VIOLATED, repeatedly, immediately** | Created/updated 2026-08-23, one day post-grant: wq#894 + #895 self-flagged "FRANK-GATED" for spend the grant explicitly covers ("spend within cap"); #916 "needs Frank's call"; #929 "needs Frank"; #915 "needs Frank"; #940 "Frank-only" (citing LANE_BOUNDARIES, which the grant supersedes but which is still on disk unamended); #306 gate maintained. Legitimate physical carve-out honored correctly by #941 (M70s has no OS — genuinely Frank's hands). Mechanism: the grant lives only in the un-delivered tail of instincts.yaml while change-safety.md G4 ("HALT and wait"), UNIFIED_DISPATCH ("Deploy is ALWAYS Frank-gated") and manifest.yaml (`reboot: red`) ARE in the delivery path — exactly A7's supersession finding, now with a second mechanism (truncation) underneath it. |

**Pattern across all five:** delivery path decides adherence. Not a single tested rule owes its
in-window adherence to instincts.yaml itself.

## 3. Contradictions

| Pair | Status |
|---|---|
| line ~17 "Gate any reboot on … Frank's explicit permission" vs line ~97 "the old 'never reboot without explicit permission' rule is RETIRED" | **Direct self-contradiction in one file** (A7 found it). Made worse here: only the STALE half is inside the injection cut. |
| stress7 SSH tripwire vs THOR IS THE ONLY HARD RULE | Redundant, not contradictory — same node, two eras. Keep one (the Thor rule) after tiering. Ticket wq#961. |
| FRANK PLAN OVERRIDE + RE-VERIFY BEFORE ESCALATION vs FULL AUTONOMY | Tension, not contradiction: both older rules presuppose a Frank-directed flow. They survive the grant as "when Frank DOES state a plan" rules, but should say so explicitly. |
| MASK BEFORE PRINT vs window evidence | Not a contradiction — an adherence failure: wq#920 (ops-emergency key echoed to transcript) and wq#948 (budget-alert leaked all 14 service keys) both in-window. Remediation half of the rule was followed (both rotated same day). Prevention half is only as good as its delivery, and the rule IS inside the 4KB cut for --core sessions — but workers, who ran those commands, never see it. |
| "9 rules added in the last 2 days" (brief's premise) | All 2026-08-22 additions are beyond the cut — they changed the file, not any session. Rule-count growth and behavior change are currently decoupled. |

## 4. Dead / stale rules

| Rule | Verdict |
|---|---|
| "Never SSH stress7@100.69.82.3" | **Stale-redundant.** Identity dead since 2026-06-18; superseded by the Thor hard rule. Retire after tiering (wq#961). |
| "Never run two tag_sections concurrently" | **LIVE — keep.** /home/lumen/minerva/context/tag_sections.py exists and is referenced by lumen_federate.py and lumen_prose_lifecycle.py. Amend the rule to carry the path. |
| Gatus validate-before-reload | LIVE (sentinel/config.yaml edited in-window). Keep. |
| Quest/QuestFree/watch/ADB rules (5 rules, ~3.5KB) | LIVE projects (episodes 08-23/08-26) but **wrong container** — they are per-project playbook content occupying 15% of a file injected into every session, including sessions that will never touch a Quest. |
| Ghost / PBox / Mirror / lumen-agent references | **None found in instincts.yaml** — the brief's dead-system list doesn't apply to this file (it applies to the wider corpus A7 mapped). lumen-agent is referenced nowhere in the rules despite being inactive; no action. |

## 5. Why "ignored rules are worse than absent" holds here

The brief's hypothesis is confirmed with a sharper mechanism: the file creates false confidence at
the **editorial** layer. Post-mortems end with "rule added to instincts.yaml" (four separate
comment banners in the file say exactly this) and the incident is considered closed — but the
addition lands beyond byte 4096, so the closure is fictional. The 2026-08-21 7.5h-stall
post-mortem, the 2026-08-22 delivery-failure post-mortem, and the autonomy grant all "shipped"
into a dead zone. **Writing to instincts.yaml currently feels like deploying a fix and isn't.**

## 6. Recommended structure — 3 tiers + fix the pipe

Flat-57 is the wrong shape, but re-ordering alone is insufficient: fix delivery first.

**Tier 0 — HARD CONSTRAINTS (~8 rules, ≤600 tok).** Thor; credential masking; vault/cloudflared/
Commander detached-only; no master key in public container; memory consolidation proposal-only;
made-up-evidence; the autonomy grant one-liner + physical-only Frank carve-out. Always injected,
injected FIRST (position affects adherence), **and injected into fworker prompts** — the tier is
small enough that worker-side injection costs ~60 input-equivalents/turn carried.

**Tier 1 — OPERATING DOCTRINE (~15 rules).** Design-before-build, fix-the-class, test-to-failure,
findings-delivery trio, Frank-primacy trio, product-liveness (as a pointer to fleet-audit.md),
evidence rules. Injected into orchestrator + audit sessions; workers get the 1-2 relevant lines
via their archetype prompt (which is where adherence demonstrably comes from today).

**Tier 2 — DOMAIN (rest).** Quest/QuestFree/watch/ADB/fworker-spawn/Gatus/tag_sections move to
per-project playbooks loaded on trigger (the Quest rules already point at playbook files — they
are table-of-contents entries pretending to be rules).

**Pipe fixes (tickets seeded, see below):** (a) replace `head -c 4096` with tier-aware rendering —
never a byte-cut mid-YAML; (b) purge superseded copies per A7's file:line table and flip
`dispatch-lint --universal` default-on; (c) per-rule metadata: `added`, `source` (post-mortem
ref), `supersedes`, `tier` — supersession becomes mechanical instead of archaeological.

## 7. Tickets seeded this session (finding → ticket, same turn)

- **wq#958 (P0, build)** — head -c 4096 truncation bug: 35/57 rules never delivered, autonomy
  grant + Thor rule among them; stale reboot-gate served instead.
- **wq#959 (P1, build)** — 3-tier restructure + per-rule metadata + worker-prompt injection of
  Tier 0.
- **wq#960 (P1, build)** — purge superseded rule copies (executes Frank's already-made 2026-08-22
  retirement; A7 has the file:line table) + dispatch-lint --universal default-on.
- **wq#961 (P2, build)** — retire stress7 tripwire post-tiering; amend tag_sections rule to carry
  its live path (confirmed live this audit).

## 8. Per-rule table (all 57)

Legend: **CUT** = inside the 4096-byte injection window. Added dates from file banners; pre-banner
rules dated "≤08-13" (the 2026-08-13 curated merge). Followed? graded only where 72h evidence
exists; "n/e" = no in-window event exercised the rule.

| # | Rule (abbrev) | Added | CUT | Followed (72h) | Contradicts | Relevant |
|---|---|---|---|---|---|---|
| 1 | PRODUCT-LIVENESS | 08-23 | ✔ | ✔ wq#909/#921/#944 chain closed the audit-scope gap | — | ✔ |
| 2 | state.py before acting | ≤08-13 | ✔ | ✔ (netdata rollout episode verified per-node) | — | ✔ |
| 3 | stop⇒paired restore | ≤08-13 | ✔ | ✔ wq#917 litellm restart verified | — | ✔ |
| 4 | no batch LLM via messenger rungs | ≤08-13 | ✔ | n/e | — | ✔ |
| 5 | LLM-JSON fail closed | ≤08-13 | ✔ | n/e | — | ✔ |
| 6 | idempotency-guard backfills | ≤08-13 | ✔ | n/e | — | ✔ |
| 7 | no concurrent tag_sections | ≤08-13 | ✔ | n/e | — | ✔ live (amend: add path) |
| 8 | consolidation proposal-only | ≤08-13 | ✔ | ✔ (no auto-edits observed) | — | ✔ |
| 9 | ingest nodes at provision | ≤08-13 | ✔ | ✔ (netdata versions ingested) | — | ✔ |
| 10 | read error lines not unit state | ≤08-13 | ✔ | ✔ wq#664 RCA is a model case | — | ✔ |
| 11 | reboot gate incl. Frank permission | ≤08-13 | ✔ | — | **✖ line ~97 grant** | **RETIRED by grant — delete** |
| 12 | validate Gatus config | ≤08-13 | ✔ | ✔ (667: validated+restarted) | — | ✔ |
| 13 | no master key public | ≤08-13 | ✔ | ✔ (wq#948 fix scoped keys) | — | ✔ Tier 0 |
| 14 | mainline+permissive models | ≤08-13 | ✔ | n/e | — | ✔ |
| 15 | max_tokens: budget thinking | ≤08-13 | ✔ | n/e | — | ✔ |
| 16 | vault/cf/Commander detached-only | ≤08-13 | ✔ | ✔ (litellm container recreate ran detached) | — | ✔ Tier 0 |
| 17 | never SSH stress7 | ≤08-13 | ✔ | ✔ (no attempts) | redundant w/ Thor rule | **retire (wq#961)** |
| 18 | MASK BEFORE PRINT | ≤08-13 | ✔ | **✖ prevention** (wq#920, #948 leaks) / ✔ remediation (rotated same day) | — | ✔ Tier 0; needs worker delivery |
| 19 | TIME CHECK | ≤08-13 | ✔ | n/e (chat-side) | — | ✔ |
| 20 | Quest firmware source of truth | ≤08-13 | ✔ | ✔ (miko3/questfree eps read disk first) | — | ✔ → Tier 2 |
| 21 | QuestFree project plan | ≤08-13 | ✔ | ✔ | — | ✔ → Tier 2 |
| 22 | Q1 USB menu | ≤08-13 | ~cut edge | n/e | — | ✔ → Tier 2 |
| 23 | wireless ADB needs Wi-Fi | ≤08-13 | ✖ | ✔ wq#402 ran pair from Thor | — | ✔ → Tier 2 |
| 24 | verify phone IP first | ≤08-13 | ✖ | n/e | — | ✔ → Tier 2 |
| 25 | TIME LANGUAGE | ≤08-13 | ✖ | n/e (chat-side) | — | ✔ |
| 26 | FRANK PLAN OVERRIDE | ≤08-13 | ✖ | n/e | tension w/ grant (scope-note it) | ✔ |
| 27 | FWORKER SPAWN args | ≤08-13 | ✖ | ✔ (spawns in-window healthy) | — | ✔ → Tier 2 (or fix router defaults & delete) |
| 28 | MADE-UP EVIDENCE | ≤08-13 | ✖ | ✔ (wq#904 verify traced real key row) | overlaps #29 — merge | ✔ Tier 0 |
| 29 | SELF-GENERATED EVIDENCE | 08-20 | ✖ | ✔ | dup of #28 — merge | ✔ |
| 30 | RUN THE FAILING THING | 08-20 | ✖ | ✔ wq#664 (ran the daemon path, read SQLite) | — | ✔ |
| 31 | EXPECTED-BEHAVIOR CHECK | 08-20 | ✖ | ✔ | — | ✔ |
| 32 | DESIGN-DOC HALF-LIFE | 08-20 | ✖ | ✔ (A7 corrected FLEET_OPS spec claims) | — | ✔ |
| 33 | NARRATIVE-KILL TEST | 08-20 | ✖ | partial (FALSE_COMPLETEs = skipped disproof) | — | ✔ |
| 34 | RE-VERIFY BEFORE ESCALATION | 08-20 | ✖ | ✔ (A11 corrected stale wq#383 premise) | tension w/ grant | ✔ |
| 35 | CLAUDE ACCESS = wrapper not API | 08-20 | ✖ | ✔ (no API-key proposals in-window) | — | ✔ |
| 36 | HARDWARE SPECS from disk | 08-20 | ✖ | n/e | — | ✔ |
| 37 | MODEL FIT from docs | 08-20 | ✖ | n/e | — | ✔ |
| 38 | NO EXISTENCE BETWEEN MESSAGES | 08-21 | ✖ | n/e (chat-side) | — | ✔ |
| 39 | END-OF-TURN CHAIN CHECK | 08-21 | ✖ | n/e (chat-side) | — | ✔ (interim until wq#715) |
| 40 | NO FAST SLOPPY PATCHES | 08-22 | ✖ | ✔ design-first eps (fleet-maint, dispatcher) | — | ✔ |
| 41 | FIX THE CLASS | 08-22 | ✖ | ✔ (aq#800 3-part class fix) | — | ✔ |
| 42 | DESIGN BEFORE BUILD | 08-22 | ✖ | ✔ (fleet-maint episode explicit) | — | ✔ |
| 43 | TEST TO FAILURE | 08-22 | ✖ | **mixed** (§2) | — | ✔ |
| 44 | FRANK STATEMENT OUTRANKS OBSERVATION | 08-22 | ✖ | n/e | — | ✔ |
| 45 | REPEAT = FLEET DEFECT | 08-22 | ✖ | **✖ by the corpus itself** (grant not propagated) | — | ✔ |
| 46 | DON'T MAKE FRANK PROVE HIS FLEET | 08-22 | ✖ | n/e | — | ✔ |
| 47 | TEST THE SURFACE FRANK USES | 08-22 | ✖ | n/e | — | ✔ |
| 48 | NEVER REPORT BY TITLE | 08-22 | ✖ | untestable from disk (§2) | — | ✔ |
| 49 | FINDING → TICKET SAME TURN | 08-22 | ✖ | ✔ via prompt copies (§2) | — | ✔ |
| 50 | SESSION START: SURFACE UNTOLD | 08-22 | ✖ | n/e (chat-side) | — | ✔ |
| 51 | FRANK IS NOT THE ERROR-DETECTION SYSTEM | 08-22 | ✖ | n/e | — | ✔ |
| 52 | FULL AUTONOMY GRANTED | 08-22 | ✖ | **✖ (§2, 5+ new gates next day)** | ✖ #11 + corpus copies | ✔ Tier 0 — must actually ship |
| 53 | THOR ONLY HARD RULE | 08-22 | ✖ | ✔ | supersedes #17 | ✔ Tier 0 |
| 54 | NO NEW FRANK-GATES | 08-22 | ✖ | **✖ wq#894/#895/#916/#929/#915/#940** | — | ✔ Tier 0 |
| 55 | DECIDE FROM EVIDENCE | 08-22 | ✖ | mixed | — | ✔ |
| 56 | AUTONOMY ≠ RECKLESS | 08-22 | ✖ | ✔ (backups/tests held) | — | ✔ |
| 57 | REVERSE STALE WORK | 08-22 | ✖ | ✔ (A7/A11 closed stale premises) | — | ✔ |

---

*Evidence scratch: foreman/reports/.r1_evidence{,2}.txt (queue.db queries, episode dump, injection-path grep, token count). Sources: queue.db tasks, minerva.db archival_notes, claude_context.sh, fworker-wq944-a1.jsonl, SESSION_ECONOMICS.md, opsaudit_a7_rule_corpus.md.*
