# A7 — SUBTRACT: the standing-rule corpus, audited rule by rule

**Ticket:** wq#757 · **Agent:** fworker-wq757 (claude-opus-5, xhigh) · **Date:** 2026-08-23 (Pacific)
**Campaign:** FLEET_OPS_AUDIT_2026-08-21.md §2 (R1–R7), §4 SUBTRACT step
**Status:** PROPOSAL ONLY. Nothing in the corpus was changed by this ticket. Every verdict below is a
recommendation for Frank; a rule Frank set stays until Frank retires it.

---

## VERDICT, before the argument

**The corpus does not have a "too many rules" problem. It has a *supersession* problem.**

On 2026-08-22 Frank granted full autonomy and it was written into `instincts.yaml` (6 rules,
lines 96–101). That grant explicitly retires the approval halt (R1) and the reboot rule (R4).
**It was written in exactly one file.** Every other copy of those rules is still on disk, still
loaded, and still instructing agents to halt:

| Where the retired rule still lives | Line | What it still says |
|---|---|---|
| `change-safety.md` G4 | 49–54 | "Then HALT and wait… Silence is NOT approval" |
| `change-safety.md` HARD RULES | 78–83 | "Never reboot any node" |
| `session-start.md` QUICK REFERENCE | 40–41 | "Escalate on: … reboot" · "DO NOT reboot w/o permission" |
| `UNIFIED_DISPATCH.md` Universal floor | 96–101 | "NO deploy, reboot, service restart… Deploy is ALWAYS Frank-gated" · "needs Frank: wq block" |
| `instincts.yaml` (self-contradiction) | 17 | "Gate any reboot on … **Frank's explicit permission**" |
| `manifest.yaml` capabilities | 35 | `reboot: red` — "full stop. No in-loop approval path. Frank in person only." |

`instincts.yaml:17` and `instincts.yaml:97` are **in the same file, 80 lines apart, and directly
contradict each other.** An agent that loads the corpus today gets both.

This is a P1 by Frank's own standing rule — *"REPEAT = FLEET DEFECT… find WHERE the wrong version
is written, fix EVERY copy"* (`instincts.yaml:78`). The autonomy grant did step 1 (say it) and
skipped step 2 (fix the law) — the precise failure `RETIRED_RULES.md:9-15` was written to prevent,
committed against the autonomy grant itself.

**So the highest-value subtraction is not deleting rules. It is deleting the stale COPIES of rules
Frank has already retired.** That is mechanical, evidence-backed, and needs no new judgment.

Second finding, independent: **the enforcement for half the corpus exists and is switched off.**
`dispatch-lint` has a fully-implemented `--universal` check (`/home/lumen/bin/dispatch-lint:160,199`)
that defaults to **off**. Run it and it reports 7 universal rules never stated and 1 stated
inconsistently — measured today, §4.7. The default run prints `8 file(s) clean` and says nothing.
F7's "enforced by nothing" is *nearly* right; the truth is worse — the enforcement was built,
merged, and left behind a default-off flag. Same pattern as F2 (lanes created, never enabled).

---

## 0. Corrections to the campaign spec

The audit's own evidence has drifted in two places. Recording both, because A7's job is evidence.

| Spec claim | Status | Measured |
|---|---|---|
| F1/R4: "wq#383 … FRANK DECISION PENDING since 2026-07-19 — **33 days of known-CVE exposure**" | **FALSE at time of writing** | `wq show 383`: `status=done`, `done_at=2026-08-01 05:38:47`. Notes: *"Executed 2026-07-31: gitea 1.26.4 + key rotation on Anubis; tailscale fleet complete; cloudflared 2026.7.3. All endpoints verified."* The ticket closed **20 days before** the 2026-08-21 audit claimed it was pending. R4's headline cost evidence does not hold. |
| F7: "`UNIVERSAL_RULES.md` … is enforced by nothing" | **Imprecise** | The checker exists (`dispatch-lint --universal`, wq#561) and works. It is opt-in and no caller passes the flag — `grep -rl dispatch-lint /home/lumen/bin /home/lumen/foreman` returns only two probe scripts and event logs. No timer, no CI, no hook. |

R4 still has a real case (§2, R4) — it just is not the CVE case. R7's case gets *stronger*, not
weaker: the fix is one flag and one caller, not a new tool.

---

## 1. Corpus map (measured, `wc -l`, 2026-08-23)

| File | Lines | Standing rules | Enforced by |
|---|---|---|---|
| `/home/lumen/instincts.yaml` | 101 | 56 | nothing (context injection only) |
| `/home/lumen/instructions/playbooks/change-safety.md` | 151 | 29 (G0–G7, 7 RED, 4 per-domain, 5 masking, 5 audit) | harness halt (loop v0) |
| `/home/lumen/instructions/playbooks/github-standards.md` | 296 | 12 hard bans + 14 sections | `lumen-git-guard` (§11b) |
| `/home/lumen/instructions/playbooks/session-start.md` | 106 | 12 | nothing |
| `/home/lumen/instructions/playbooks/ssh-access.md` | 54 | 7 + dead-identity list | nothing |
| `/home/lumen/instructions/playbooks/cloudflare-ops.md` | 50 | 5 traps + scope limit | nothing |
| `/home/lumen/instructions/playbooks/long-jobs.md` | 28 | 1 mandate + 3 | nothing |
| `/home/lumen/instructions/manifest.yaml` | 83 | 7 RED capabilities + shell classes | loop classifier |
| `/home/lumen/agents/work-queue/UNIVERSAL_RULES.md` | 36 | 5 (4 REQUIRE, 1 AGREE) | `dispatch-lint --universal` (**default off**) |
| `/home/lumen/agents/work-queue/RETIRED_RULES.md` | 40 | 8 RETIRE lines | `dispatch-lint` (default on) |
| `/home/lumen/agents/work-queue/UNIFIED_DISPATCH.md` | 115 | 6 floor + 4 per-lane | nothing |
| 5 sibling `*_DISPATCH.md` | 381 | divergent copies | `dispatch-lint` (retired rules only) |
| `/home/lumen/foreman/FOREMAN_DISPATCH.md` | 101 | 2 prefix-cache | nothing |
| `/home/lumen/claude_memory.md` | 5103 | prose, rules migrated out to playbooks | n/a |

Corpus total ≈ **1,460 live lines**, ~150 discrete standing rules. (The ticket's "~939 lines"
matches the narrower set: instincts + UNIVERSAL + RETIRED + UNIFIED + 6 playbooks = 977.)

---

## 2. The seven named rules

### R1 — "One mutation per approval. Silence is NOT approval." (`change-safety.md:49-54`)

| Field | Finding |
|---|---|
| **Verbatim** | "Compose ONE approval request via notify_frank… Then HALT and wait. Approval is per-action and non-transferable. Silence is NOT approval. In loop v0 the harness enforces this halt regardless." |
| **Origin** | change-safety playbook v1, 2026-07-09. Written for a fleet with no memory, no episode store, and no verified rollback. **Traceable.** |
| **Cause still exists?** | Partly. A6 (`reports/opsaudit_a6_approval_model.md`) measured 66 incidents: RULE_VIOLATION 3%, missing-precondition 44%, and *in every mutation incident on record Frank would have approved*. The control does not act on the failure mode it was built for. |
| **Cost today** | Was F1's cause (77 items parked, per wq#752). |
| **VERDICT** | **RETIRED ALREADY — corpus not updated.** Frank retired this 2026-08-22 (`instincts.yaml:96`). This ticket's action is not a decision, it is a sweep. |
| **Case FOR keeping** | A6 §1 found the "verified backup" precondition **does not hold**: fq#22 — `/home/lumen/pokemon` was in **0 of 393** restic snapshots while backup jobs ran green for months. G4's halt is currently the only thing standing between an agent and a destructive change with an imaginary backup. Removing the halt without shipping the evidence-runner removes a real control and replaces it with prose. |
| **PROPOSED** | Adopt A6 §6's G4 rewrite (evidence-in-place-of-permission) and A6 §9's ladder: class table on, **Class S off until the evidence-runner ships**. Do not hand-delete G4 in the meantime — replace it. |

### R2 — "Never call Anthropic APIs" (`change-safety.md:81`, `manifest.yaml:41` `anthropic_api: red`)

**This is the clearest RETIRE/REWORD in the corpus, and the corpus contains a third, wrong version.**

Three live statements, mutually inconsistent:

1. `change-safety.md:81` — "Never call Anthropic APIs." (RED = "full stop. No in-loop approval path. Frank in person only.")
2. `instincts.yaml:51` — "Claude is accessed via a WRAPPER AROUND THE CLAUDE DESKTOP APP running on Lumen… **The API platform / API key is for Claude Code which we do not use.**"
3. **State store**, `anthropic_subscription.auth_method` (since 2026-07-28) — "**OAuth via claude-code CLI on Max subscription; NOT API key.** ANTHROPIC_API_KEY in /home/lumen/agent/.env must never be inherited by agent workers."

Statement 2 is **factually false about this fleet**, measured:

- `/home/lumen/.npm-global/bin/claude` → `@anthropic-ai/claude-code/bin/claude.exe`. The fleet's worker binary *is* Claude Code.
- `foreman/harness/fworker.py:628-630` pops `ANTHROPIC_API_KEY` / `_BASE_URL` / `_AUTH_TOKEN` before spawning — so there is no per-token billing path.
- **This report was produced by a Claude Code worker spawned by that harness.** The rule saying "Claude Code which we do not use" was violated by the act of auditing it.

| Field | Finding |
|---|---|
| **Origin** | RED tier, 2026-07-09 manifest. Intent (per state store + audit §R2): *no per-token API billing*. **Traceable, and the intent is sound.** |
| **Cost today** | The wording says "Anthropic", not "per-token billing", so agents read it as "avoid Claude" and re-route engineering work to weaker local rungs on false cost grounds. Audit records Frank correcting this **repeatedly**; `instincts.yaml:48-50` header says the same ("Frank has corrected this REPEATEDLY"). A rule Frank must restate is a fleet defect by `instincts.yaml:78`. |
| **VERDICT** | **REWORD (R2 keeps its teeth, loses its misreading)** + **CORRECT `instincts.yaml:51`, which is wrong on the facts.** |
| **Case FOR keeping** | The billing protection is real and must survive verbatim: an agent that adds an `anthropic/` model with an `api_key` to LiteLLM creates uncapped per-token spend outside the Max plan. Reword must not weaken that. |
| **PROPOSED (drop-in for `change-safety.md:81`)** | `Never create per-token Anthropic API billing: no ANTHROPIC_API_KEY in any service, no anthropic/* model entry with api_key in LiteLLM, no top-up of API credits, no per-token cost math for Claude. Claude Code on the Max 20x subscription (OAuth, state store: anthropic_subscription) IS the sanctioned inference path and is not "an Anthropic API call" — use it freely. DeepSeek stays the separate paid engineering rung.` |
| **PROPOSED (`manifest.yaml:41`)** | rename capability `anthropic_api` → `anthropic_token_billing`; keep `red`. |

### R3 — "Never improvise complex OS fixes — write a research prompt instead" (`change-safety.md:82-83`)

| Field | Finding |
|---|---|
| **Origin** | change-safety v1 RED list, 2026-07-09. **No incident traceable** to this specific wording — flagged as undocumented per ticket instruction, not defended. |
| **Cause still exists?** | The underlying instinct does (don't improvise on a box you can't restore). The *threshold* never existed: "complex" is undefined anywhere in the corpus. |
| **Cost today** | Agents punt trivially-fixable things into the research lane. Note the interaction: this rule creates the backlog that F1 then measures as Frank-parked. |
| **VERDICT** | **SCOPE-DOWN** — replace the undefined adjective with a testable predicate. |
| **Case FOR keeping** | Under full autonomy this rule is now one of the few remaining brakes on an agent improvising on an unrecoverable box. It should get *sharper*, not removed. |
| **PROPOSED** | `Fix it yourself when all three hold: (a) reversible — you have a restore-proven backup of the exact artifact, to the wq#566 standard; (b) blast radius is one unit — one service on one node, no shared dependency; (c) you can state the expected post-state and check it. If any fails, write the research prompt. "Complex" is not a feeling; it is these three tests.` |

### R4 — "Never reboot without permission" + no maintenance window

| Field | Finding |
|---|---|
| **Lives in** | `change-safety.md:79`, `instincts.yaml:17`, `session-start.md:40-41`, `manifest.yaml:35` (`reboot: red`), `UNIFIED_DISPATCH.md:96`. **Five copies.** |
| **Origin** | Traceable and *recorded in its own retirement*: `instincts.yaml:97` — "it existed because things were deployed untested and took Lumen down; backups now exist." |
| **VERDICT** | **RETIRED ALREADY (2026-08-22) — 5 stale copies + 1 self-contradiction to sweep.** |
| **Correction** | The CVE-exposure argument (audit F1/R4, wq#383) is **void** — §0. Do not carry it into the rewrite; it will not survive review. |
| **Case FOR keeping** | The retirement is conditional on a fact — "Lumen has a backup for reboots… every other node either survives a reboot or has its own backup." fq#22 (0/393 snapshots) is direct evidence that fleet backup coverage has been confidently wrong before. The Thor carve-out (`instincts.yaml:97`) must survive verbatim and is the one hard rule left. |
| **PROPOSED** | Sweep all 5 copies to point at `instincts.yaml:97`. Delete `instincts.yaml:17` outright (superseded by :97, same file). Keep `manifest.yaml reboot: red` **only for Thor** — re-express as `reboot_thor: red`, `reboot: green`. A maintenance window is now unnecessary: the rule it worked around is gone. |

### R5 — "All builds run on Lumen" (`session-start.md:41`, DO-NOT row: "build on non-Lumen nodes")

| Field | Finding |
|---|---|
| **Verbatim** | `\| DO NOT \| reboot w/o permission · dd USBs (Etcher only) · Surface Pro refurb biz · Anthropic API in fleet · scrape Reddit · **build on non-Lumen nodes** \|` |
| **Origin** | **NO TRACEABLE ORIGIN.** Episode recall for build/node-contention returns unrelated results (wq#328, wq#74, LTR). No incident, no ticket, no dated note anywhere in the corpus explains it. **Flagged as undocumented, not defended** — per ticket instruction. |
| **Cause still exists?** | **Cannot be determined, and that is the finding.** F6 measured zero node CPU/RAM/VRAM/disk series for any of the 15 nodes in VictoriaMetrics. There is no data with which to evaluate either the rule or its removal. |
| **Cost today** | Every build, every worker and every MC service contend on one T5810 — asserted by the audit, **not measured**. Interacts with F4 (concurrency ceiling). |
| **VERDICT** | **KEEP FOR NOW — BLOCKED ON MEASUREMENT.** This is the one named rule I decline to recommend changing. Retiring it would be trading an unmeasured rule for an unmeasured risk. |
| **Case FOR keeping** | Lumen is the only host with the guard-host invariant (`[ "$(hostname)" = lumen ] || exit 9`, G0 + ssh-access rule 1) baked into every playbook. Builds elsewhere would run outside every existing safety gate. That is a real, structural reason — better than the contention argument. |
| **PROPOSED** | No text change until node_exporter lands (audit §3 item 1, retarget wq#744). Then re-open with data. Interim: change the rule's *stated reason* from implied capacity to the real one — the G0 host guard — so the next reader knows what it is protecting. |

### R6 — Research routing rule, 2026-04-19 (`session-start.md:53-68`)

| Field | Finding |
|---|---|
| **Origin** | **Traceable and documented in place**, unusually well: "a chat was auto-flagged 2026-04-18 — a false positive on the SHAPE of stacked content… not a policy violation." Single incident, 4 months ago. |
| **Cause still exists?** | Unverified. One data point, no recurrence recorded in 4 months. |
| **Cost today** | Hardware RE work (Screenpad, BC-250, Nest, Quest) routes to a weaker model by default and needs a second pass anyway. |
| **VERDICT** | **SCOPE-DOWN.** The rule's own text already names the narrow trigger (*stacked* content: credentials + infosec-adjacent + multi-host admin in one window); the operative sentence then over-fires on all RE-shaped work. |
| **Case FOR keeping** | The prompt-hygiene half is good practice independent of routing, and the hard line is non-negotiable. Also note the rule is *self-limiting already* — "Stays with Claude directly: normal discussion, design, code, commands, specific help with Frank's specific hardware" covers most real work. Part of R6's cost may be agents misreading it, not the rule. |
| **PROPOSED** | Keep verbatim: the hard line ("nothing that bypasses Claude's safety training or Anthropic policy, nothing actually illegal") and the prompt-hygiene paragraph. Re-scope the routing clause to the measured trigger: `Route to DeepSeek only when a single context window would STACK credentials + infosec-adjacent research + multi-host admin language — the 2026-04-18 shape. RE of hardware Frank owns, on its own, stays with Claude.` |

### R7 — Dispatch-file law is copy-pasted, not generated

| Field | Finding |
|---|---|
| **Origin** | wq#561 measured it: zero lines common to all six dispatches, longest identical block 4 lines, peak pairwise overlap 10%. |
| **Cause still exists?** | **Yes, and measured again today** — §4.7. |
| **VERDICT** | **PROMOTE-TO-ENFORCED.** The cheapest fix in this report: the checker is already written and tested. |
| **Case FOR the status quo** | wq#561 concluded correctly that generation is *not* the fix — the files diverged past the point where a shared substrate exists, and per-lane divergence is often legitimate (POKEMON's `lpp-restart` scoping is a good rule that must not be universalized). The REQUIRE/AGREE table is the right design precisely because it constrains without generating. Nothing here argues for one canonical file. |
| **PROPOSED** | (1) Flip `--universal` to default-on (`--no-universal` to opt out). (2) Add one caller: `dispatch-lint --universal` in the pre-dispatch path or a timer, so drift is caught without a human running it. (3) Resolve the heartbeat TTL disagreement in `UNIVERSAL_RULES.md` by naming the winning value (the `wq` default is 240; `session-start.md:79` documents 240 — **90 is the outlier**, and 3 of 6 files teach it). |

---

## 3. Contradictions — rules contradicted by another rule or by the state store

Each row is a live inconsistency an agent can encounter today. These need no judgment call about
whether a rule is *good*; they are defects regardless of the verdict on the underlying rule.

| # | Rule A | Rule B / source of truth | Nature |
|---|---|---|---|
| C1 | `instincts.yaml:17` "Gate any reboot on … Frank's explicit permission" | `instincts.yaml:97` "The old 'never reboot without explicit permission' rule is RETIRED" | **Same file, 80 lines apart.** Direct. |
| C2 | `change-safety.md:49-54` G4 HALT · `:79` never reboot | `instincts.yaml:96-101` FULL AUTONOMY | Playbook still halts on a retired gate |
| C3 | `UNIFIED_DISPATCH.md:96-97` "Deploy is ALWAYS Frank-gated" | `instincts.yaml:96` "Do not create Frank-gates" | Direct |
| C4 | `UNIFIED_DISPATCH.md:100` "needs Frank: `wq block`" | `instincts.yaml:98` "NO NEW FRANK-GATES" | Direct |
| C5 | `session-start.md:40` "Escalate on: … reboot, >$20 spend" | `instincts.yaml:96` "Do not ask permission for … spend within cap" | Direct |
| C6 | `instincts.yaml:51` "the API platform / API key is for Claude Code **which we do not use**" | State store `anthropic_subscription.auth_method`; `fworker.py:628`; `@anthropic-ai/claude-code` on disk | **Rule is factually false about the fleet's own architecture** |
| C7 | `change-safety.md:81` "Never call Anthropic APIs" (RED) | State store: Max-20x OAuth *is* the sanctioned path | Wording forbids the sanctioned path |
| C8 | `manifest.yaml:35` `reboot: red` — "Frank in person only" | `instincts.yaml:97` reboot freely except Thor | Classifier still hard-stops |
| C9 | `UNIFIED_DISPATCH.md:63` `--ttl 90` | `session-start.md:79` "Claims lease-expire (default 240m)" | Four-way TTL disagreement, `UNIVERSAL_RULES:36` |
| C10 | `instincts.yaml:26` "Search episodes with `lumen_episode.py search '<term>'`" | Binary: `invalid choice: 'search' (choose from backfill, recall, precheck, commit)` | **Rule instructs a command that does not exist.** Correct verb is `recall` |
| C11 | `UNIFIED_DISPATCH.md:28` "NEVER claim lane `user` — Frank-only" | `instincts.yaml:98` "NO NEW FRANK-GATES"; `dispatcher/config.json:43` `user.enabled=false` | The `user` lane is a Frank-gate as data structure; retained deliberately, but now unnamed by any live rule |
| C12 | `UNIFIED_DISPATCH.md:14-22` "CURRENT HEADLINE PRIORITY (Frank, 2026-07-16): PERSONA EVOLUTION CHAIN" | `UNIFIED_DISPATCH.md:37` "the `fable` lane … drained as of 2026-08-14" | Dispatch leads with a headline priority its own file says is finished |

**C10 and C12 deserve emphasis:** these are not philosophical. C10 is a standing instinct that
fails on execution; C12 makes every agent reading UNIFIED lead with drained work. Both are the
kind of rot `RETIRED_RULES.md` exists to catch and neither is in its table.

---

## 4. Full corpus inventory

Verdict key: **K** keep · **R** reword · **S** scope-down · **X** retire · **P** promote-to-enforced
· **!** contradiction (see §3) · **?** no traceable origin

### 4.1 `instincts.yaml` — 56 rules

| # | Rule (abbrev) | V | Note |
|---|---|---|---|
| 1 | Query `lumen_state.py current` before acting on any node | K | Foundation of R5's real justification |
| 2 | Automation that stops a service carries a paired restore | K | |
| 3 | Never route consolidation/extraction/batch LLM via messenger-chat/re rungs | K | |
| 4 | Unattended LLM-JSON: `json_object` + tolerant parse + one retry, fail closed | K | |
| 5 | Replay/backfill idempotency-guarded before first run | K | |
| 6 | Never run two `tag_sections` concurrently | K | |
| 7 | Memory consolidation stays proposal-only | K | |
| 8 | Ingest new nodes at provision time (inverse-apep) | K | |
| 9 | Health checks read error lines, not just unit state | K | |
| 10 | Gate any reboot on … Frank's explicit permission | **X !** | **C1** — superseded by rule 47, same file |
| 11 | Validate Gatus config before reload | K | |
| 12 | Never expose a master key in a public-facing container | K | |
| 13 | Prefer mainline + permissive-license models | K | |
| 14 | Never size `max_tokens` by answer length alone | K | |
| 15 | Vault/cloudflared/Commander change only via detached `systemd-run` | K | Survives autonomy — it is physics, not permission |
| 16 | Never SSH stress7@100.69.82.3 | K | Duplicated in `ssh-access.md:29` — acceptable |
| 17 | MASK BEFORE PRINT | K | Cost a live Gitea token 2026-08-13 |
| 18 | TIME CHECK: `user_time_v0` at session start | K | |
| 19 | QUEST FIRMWARE SOURCE OF TRUTH | **R !** | **C10** — `search` → `recall` |
| 20 | QUESTFREE PROJECT PLAN read at session start | K | |
| 21 | Q1 USB UPDATE MODE MENU — never ask Frank again | K | Exemplar: encodes a Frank-restatement into structure |
| 22 | WIRELESS ADB PAIR REQUIRES WI-FI ON HOST | K | |
| 23 | VERIFY PHONE IP BEFORE NETWORK OPS | K | |
| 24 | TIME LANGUAGE — no time-of-day framing without a clock call | K | |
| 25 | FRANK PLAN OVERRIDE | K | |
| 26 | FWORKER SPAWN: `--repo` + `--claude-bin` mandatory | **P** | Verified true today (`fworker.py:1458`). This is a *lint*, not an instinct — router.py should emit both or refuse |
| 27 | MADE-UP EVIDENCE | K | |
| 28 | SELF-GENERATED EVIDENCE | K | Near-duplicate of 27; merge candidate |
| 29 | RUN THE FAILING THING | K | |
| 30 | EXPECTED-BEHAVIOR CHECK | K | |
| 31 | DESIGN-DOC HALF-LIFE | K | This audit applied it to the audit — §0 |
| 32 | NARRATIVE-KILL TEST | K | |
| 33 | RE-VERIFY BEFORE ESCALATION | K | |
| 34 | CLAUDE ACCESS IS NOT VIA ANTHROPIC API KEY (wrapper/desktop-app claim) | **R !** | **C6** — factually false; see R2 |
| 35 | HARDWARE SPECS: never from memory | K | |
| 36 | MODEL FIT CLAIMS: read project docs | K | |
| 37 | NO EXISTENCE BETWEEN MESSAGES | K | |
| 38 | END-OF-TURN CHAIN CHECK | **S** | Its own text says "interim until wq#715 dispatcher ships". Dispatcher now ships and self-dispatches (lanes enabled, `config.json`). Re-scope to interactive sessions or retire on Frank's word — the rule names him as the only retirer |
| 39 | NO FAST SLOPPY PATCHES | K | |
| 40 | FIX THE CLASS, NEVER THE INSTANCE | K | The rule this whole report is an instance of |
| 41 | DESIGN BEFORE BUILD | K | |
| 42 | TEST TO FAILURE BEFORE DECLARING DONE | K | |
| 43 | FRANK'S STATEMENT OUTRANKS AN OBSERVATION | K | |
| 44 | REPEAT = FLEET DEFECT | K | **The rule that makes §0 a P1** |
| 45 | DO NOT MAKE FRANK PROVE HIS OWN FLEET | K | |
| 46 | WHEN FRANK SAYS SOMETHING IS BROKEN, THE THING HE IS TOUCHING IS BROKEN | K | |
| 47 | NEVER REPORT A COMPLETED INVESTIGATION BY TITLE | K | **Applies to this report — see §7** |
| 48 | A FINDING THAT NAMES A DEFECT MUST BECOME A TICKET, IMMEDIATELY | K | Applied: §7 |
| 49 | SESSION START: SURFACE WHAT FRANK HAS NOT BEEN TOLD | K | |
| 50 | FRANK IS NOT THE ERROR-DETECTION SYSTEM | K | |
| 51 | FULL AUTONOMY GRANTED 2026-08-22 | K | Authoritative. **Needs propagation, not change** |
| 52 | THOR IS THE ONLY HARD RULE | K | The one surviving absolute |
| 53 | NO NEW FRANK-GATES | K | |
| 54 | DECIDE FROM EVIDENCE, NOT FROM PERMISSION | K | |
| 55 | AUTONOMY DOES NOT MEAN RECKLESS | K | The precondition half of A6's finding |
| 56 | REVERSE STALE WORK, DO NOT EXECUTE IT | K | Directly relevant: §0's sweep must not re-apply dead rules |

**Instincts verdict: 50 keep, 3 reword, 2 retire/scope-down, 1 promote.** The instinct corpus is in
good shape. Its defect is not content — it is that rules 10 and 34 were never removed when
superseded, and rule 51 was never propagated outward.

### 4.2 `change-safety.md` — 29 rules

| Rule | V | Note |
|---|---|---|
| G0 host guard `hostname = lumen \|\| exit 9` | K | Also the real basis for R5 |
| G0 ssh: verify target IP against state store this cycle | K | |
| G1 verify current state read-only, per resource type | K | A6: this is the control that actually works |
| G1 "if the WHY cannot be determined → ORANGE (halt, ask)" | **S !** | Halt-and-ask conflicts with autonomy; the *don't-blind-edit* half is right. Reword to "→ do not edit; investigate or ticket" |
| G2 episode precheck; do not proceed on known-failure path | K | |
| G3 backup before mutation, verified non-empty | **R** | A6/fq#22: "non-empty" is exactly the check that passed for months on 0/393 snapshots. Raise to restore-proven (wq#566 standard) |
| G3 crown jewels: dump first, PGDATA + listen_addresses causes | K | |
| G4 approval HALT, silence is not approval | **X !** | **C2** — replace per A6 §6 |
| G5 one mutation per approval, no batching | **R** | Keep "no unapproved extras while in there" as *scope discipline*; the approval framing is dead |
| G5 long work only via `lumen-run` | K | |
| G5 cloudflared/Commander detached | K | |
| G5 "confirm the change cannot require a reboot" | **X !** | **C2/C8** — premise gone |
| G6 re-run G1 check; roll back on mismatch; never retry blind | K | Strongest rule in the playbook |
| G7 restore invariant + episode commit + state ingest | K | |
| HARD: never reboot any node | **X !** | **C2** |
| HARD: never `dd` for USB | K | |
| HARD: never spoof enp0s25 MAC | K | |
| HARD: never push/PR to third-party repos | K | |
| HARD: never expose a secret value | K | |
| HARD: never call Anthropic APIs | **R !** | **C7** — see R2 |
| HARD: never touch Surface Pro refurb business | K | Frank-stated, restated in `github-standards.md:0a` |
| HARD: never improvise complex OS fixes | **S ?** | R3 — no traceable origin |
| systemd drop-ins over vendor units | K | |
| `git_push_own` main-branch push is yellow | **R !** | Autonomy + `github-standards.md:2` (branch+PR on Class A) covers this better |
| `package_install`: never dist-upgrade under this playbook | **S** | "that is Frank-session work" — a Frank-gate by another name |
| state_ingest/episode_commit backup = sqlite `.backup` | K | |
| Versioning: edits to this playbook are themselves yellow | K | |
| SECRET MASKING (5 rules, wq#529) | K | Protocol-agnostic-or-not-at-all; cost a live token |
| AUDITS THAT CERTIFY THE TREE (5 rules, wq#529+540) | K | **Applied to this report:** positive control run — `dispatch-lint --universal` went red on a known defect before I trusted the clean default run |

### 4.3 `session-start.md` — 12 rules

| Rule | V | Note |
|---|---|---|
| Boot sequence `claude_context.sh --core`; trust live snapshot over prose | K | |
| litellm is Docker, not systemd | K | |
| Ghost is DELETED — no checks/backups | K | |
| HANDOFF system: point at live-verifiable state, never paraphrase-dump | K | |
| QUICK REF: on-call escalate on data-loss, >$20, reboot, downtime >15min | **R !** | **C5** |
| QUICK REF DO NOT: reboot w/o permission | **X !** | **C2** |
| QUICK REF DO NOT: dd USBs (Etcher only) | K | |
| QUICK REF DO NOT: Surface Pro refurb biz | K | |
| QUICK REF DO NOT: Anthropic API in fleet | **R !** | **C7** |
| QUICK REF DO NOT: scrape Reddit | K | |
| QUICK REF DO NOT: build on non-Lumen nodes | **K ?** | R5 — no origin; blocked on F6 telemetry |
| MCP cost tiers; enable only what the task needs | K | |
| RESEARCH ROUTING RULE (2026-04-19) | **S** | R6 |
| PARALLEL-SESSION COORDINATION: `wq claim` + `slock` only | K | |
| ENFORCED (wq#317): claim acquires slocks in-transaction; `wq preflight` | K | Model example of promote-to-enforced |
| ORCHESTRATOR RULE (2026-08-12): sessions stay low-context, decompose to tickets | K | |
| GITHUB STANDARDS pointer | K | |

### 4.4 `ssh-access.md` — 7 rules + identity lists · all **KEEP**

Host guard first · one command per connection with explicit timeout · remote mutations carry full
G0–G7 with backups on target · Windows targets single short commands · **never edit sshd_config /
authorized_keys / Tailscale ACLs (lockout risk)** · long remote jobs via `systemd-run` on target ·
crown-jewel targets read-only unless vault work · auth failure → do not retry other keys ·
dead identities (stress7, pbox) · sentinel probe key restricted.

One note: "that is Frank-session work" (rule 5) is a Frank-gate. **It should stay** — lockout risk
on the fleet's own access path is the textbook case where autonomy's "physical action nobody else
can perform" carve-out applies. Recommend re-labelling it as a *lockout-risk* rule rather than a
*permission* rule, so it does not read as the retired gate class.

### 4.5 `cloudflare-ops.md` — 5 traps + scope limit · all **KEEP**

Loop rides the tunnel → always detached restart · no `ExecReload`, config edit needs restart ·
**ACCESS BEFORE INGRESS** · worker redeploys on mcpw path are lockout-risk, verify failover first ·
DNS verify with `dig` against 1.1.1.1 · G6 re-verify one unrelated hostname (a YAML typo takes down
all 21). Every one names a real outage. Scope limit ("zone settings, Access policy, token rotation
are Frank-session work") — **keep**, same lockout-risk reasoning as 4.4.

### 4.6 `long-jobs.md` — 4 rules · all **KEEP**

`lumen-run` mandate (gateway reaps the process group; `nohup`/`setsid`/`disown` do not survive) ·
restart deadman confirms PID advance, not `/health` · cloudflared/Commander detached · track by unit
name, never PID. Marked compression-fragile in-file; do not paraphrase in any sweep.

### 4.7 `UNIVERSAL_RULES.md` — 5 rules · **PROMOTE ALL**

Measured today, `dispatch-lint --universal`:

```
UNIVERSAL RULES NEVER STATED (7) — wq#561:
  ✗ ARTICLE_RESEARCH_DISPATCH.md  [no-add-all] [precheck] [progress-board]
  ✗ FABLE_QUEUE_DISPATCH.md       [no-add-all] [precheck] [progress-board]
  ✗ UNIFIED_DISPATCH.md           [no-add-all]
UNIVERSAL RULES STATED INCONSISTENTLY (1) — wq#561:
  ✗ [heartbeat-ttl]  '240': ARTICLE_RESEARCH, FABLE_QUEUE   '90': GITHUB, UNIFIED
```

Default run output, same moment: `dispatch-lint: 8 file(s) clean against 8 retired rule(s)`.

| Rule | V | Note |
|---|---|---|
| REQUIRE register-session (wq#502) | P | Only rule currently satisfied everywhere |
| REQUIRE no-add-all | P | **3 files missing** — and `git add -A` is `github-standards.md` hard ban #8. A hard ban absent from 3 of 6 dispatches |
| REQUIRE precheck | P | 2 files missing — this is the G2 change-safety gate |
| REQUIRE progress-board | P | 2 files missing |
| AGREE heartbeat-ttl | P **!** | **C9** — resolve to 240 (`wq` default; `session-start.md:79`); 90 is the outlier |

### 4.8 `RETIRED_RULES.md` — 8 rules · **KEEP ALL, ADD 2**

All 8 verified still correctly retired; `dispatch-lint` reports 8 files clean, 2 citations exempted
(both legitimate — `POKEMON_DISPATCH.md:42,118` name a retired rule in order to retire it).

**This file is the corpus's best-designed artifact** and the model for the §0 sweep: it states the
two-action convention (say it on the board *and* fix the law), explains its own format bug (`::` not
`|`, because the first rule's regex contained an alternation), and prints its exemptions so an
auditor sees them.

**Proposed additions** — the two rules retired in conversation that never got a RETIRE line:

```
RETIRE :: reboot-needs-permission :: [Nn]ever reboot.{0,40}(without|w/o) (explicit )?permission :: ALL :: Superseded 2026-08-22 (instincts.yaml:97): reboot any node freely EXCEPT Thor (100.112.151.100), which is never restarted without asking. Backups now exist; the rule existed because untested deploys took Lumen down.
RETIRE :: frank-approval-halt :: (Silence is NOT approval|Deploy is ALWAYS Frank-gated|HALT and wait) :: ALL :: Superseded 2026-08-22 (instincts.yaml:96-101): full autonomy. Decide, act, record the reasoning, tell Frank after. Preconditions (restore-proven backup, verified state, test-to-failure) apply MORE, not less.
```

### 4.9 `UNIFIED_DISPATCH.md` — 10 rules

| Rule | V | Note |
|---|---|---|
| Headline priority: persona evolution chain (2026-07-16) | **X !** | **C12** — same file says fable lane drained 2026-08-14 |
| Lane priority order; never claim `user` | **R !** | **C11** |
| Register before any claim (wq#502) | K | |
| HARD STOP: `wq claim` errors → do no unclaimed work (wq#380) | K | Caused duplicate restic checks fighting repo locks |
| Never pass a ticket id as agent name | K | |
| Read ticket fully before acting; precheck; heartbeat | K | TTL value → C9 |
| Report to `reports/<id>_<slug>.md`, verdict first, CONFIRMED vs INFERRED | K | |
| Universal floor: STAGE only, no deploy/reboot/restart; Deploy ALWAYS Frank-gated | **X !** | **C3** |
| Universal floor: no Anthropic spend on SUBcalls via LiteLLM | **K** | This one is *correctly* worded — it targets spend, not "Anthropic". Use as the model for R2's rewrite |
| Stuck/risky/needs Frank → `wq block` | **X !** | **C4** |
| GitHub law pointer (2026-08-12) | K | |

### 4.10 `github-standards.md` — scope fence + 12 hard bans · **KEEP ALL**

The scope fence (§0a) is the corpus's best-documented rule: it records the winforge incident
verbatim, names the standard itself as the thing that was wrong, and states the recovery (~8h
exposure, 0 stars/forks, release deleted, repo private 14:53Z). Hard bans 1–12 all keep; ban #8
(`git add -A`) is the one that should also be enforced via §4.7. Enforced locally by
`lumen-git-guard` (§11b) — one of only three enforced rule sets in the corpus.

### 4.11 `manifest.yaml` — 7 RED capabilities

| Capability | V | Note |
|---|---|---|
| `reboot: red` | **X !** | **C8** — retain as `reboot_thor: red` |
| `dd_usb: red` | K | |
| `mac_spoof: red` | K | |
| `git_push_thirdparty: red` | K | |
| `surface_pro_business: red` | K | |
| `secret_exposure: red` | K | |
| `anthropic_api: red` | **R !** | **C7** — rename `anthropic_token_billing` |

`shell_classes.red_tokens` `[reboot, shutdown, poweroff, halt, telinit, dd, mkfs, parted, fdisk,
wipefs, macchanger, blkdiscard]` — **keep all except `reboot`/`shutdown`/`poweroff`/`halt`**, which
now need a Thor-scoped check rather than a blanket token deny.

### 4.12 `FOREMAN_DISPATCH.md` — 2 rules · **KEEP BOTH**

Freeze mid-session (never toggle MCP/settings/CLAUDE.md while a worker is in flight) · static
first, volatile last (largest shared prefix before per-run divergence). Both measured in
`research/PREFIX_STABILITY.md` (wq#630). Cache economics, not safety — but obeyed by this session.

---

## 5. Rules with no traceable origin

Per the ticket: flagged, not defended.

| Rule | Where | What I searched |
|---|---|---|
| "build on non-Lumen nodes" (R5) | `session-start.md:41` | episode `recall` (build/contention), corpus grep, ticket board. Nothing. The best justification I can construct for it — the G0 host guard — appears nowhere in the rule's own text |
| "Never improvise complex OS fixes" (R3) | `change-safety.md:82` | corpus grep, episode recall. No incident; no definition of "complex" anywhere |
| "scrape Reddit" ban | `session-start.md:41` | No dated origin. Plausibly ToS-driven; unrecorded |
| "never route consolidation/extraction/batch LLM via messenger-chat/re rungs" | `instincts.yaml:9` | Arrived via the 2026-08-13 curated instinct merge; the originating candidate is not on disk |
| G5 "confirm the change cannot trigger or require a reboot" | `change-safety.md:62` | Derives from the reboot rule, which is retired. Orphaned premise |

These five are the corpus's genuine unknowns. Four of five are low-cost to keep. R5 is the
expensive one, and it is the one blocked on F6.

---

## 6. What to do, in order

Sequenced so each step is verifiable before the next, and so nothing depends on a judgment Frank
has not already made.

**Step 1 — Sweep (no new decisions; Frank already made these 2026-08-22).**
Add the two RETIRE lines from §4.8 to `RETIRED_RULES.md`, then fix the copies they catch: C1–C5,
C8. Verification is mechanical: `dispatch-lint` goes from clean to red, you fix, it goes clean
again. This is `instincts.yaml:44` (REPEAT = FLEET DEFECT) applied to the autonomy grant itself.

**Step 2 — Flip the switch that already exists (R7).**
`--universal` default-on + one caller. Closes 8 measured defects (§4.7). Smallest diff in this
report with the largest enforcement gain.

**Step 3 — Fix the two rules that are factually wrong.**
`instincts.yaml:51` (C6, contradicts the state store and this session's own existence) and the R2
rewrite across `change-safety.md:81` / `manifest.yaml:41` / `session-start.md:41`. Model the wording
on `UNIFIED_DISPATCH.md:98`, which already gets it right.
Also C10: `search` → `recall` in `instincts.yaml:26`. One word.

**Step 4 — Sharpen, do not delete (R3, R6).**
Threshold text for R3; trigger re-scope for R6. Both keep their hard lines verbatim.

**Step 5 — Adopt A6's G4 replacement (R1), Class S OFF.**
Per A6 §9. The evidence-runner is the dependency, not Frank.

**Step 6 — R5 stays until node_exporter lands.**
Retarget wq#744 (audit §3 item 1). Re-open R5 with data, not argument.

### Tickets this report should seed

Per `instincts.yaml:48` — a finding that names a defect becomes a ticket in the same turn it is
discovered. I am recording these as recommendations rather than opening them, because the ticket's
CONSTRAINTS line makes A7 proposal-only; opening seven tickets is itself a change to the board.
**Recommend Frank or the orchestrator seed these:**

1. **P1** — Sweep retired reboot/approval rules from all 6 stale locations (§6 step 1). Blocks nothing; unblocks agent consistency.
2. **P1** — `dispatch-lint --universal` default-on + wire one caller (§6 step 2).
3. **P1** — Correct `instincts.yaml:51`; it is false about the fleet's architecture (C6).
4. **P2** — R2 reword across 3 files + `manifest.yaml` capability rename.
5. **P2** — `instincts.yaml:26` `search` → `recall` (C10). One-word fix, currently unfollowable.
6. **P2** — Strike the drained persona-chain headline from `UNIFIED_DISPATCH.md` (C12).
7. **P3** — R3 threshold + R6 trigger re-scope.
8. **P3** — Re-open R5 after node_exporter (depends on retargeted wq#744).

---

## 7. Honest limits of this report

Per `change-safety.md:148` — a clean sweep is a claim, and claims get evidence.

- **Corpus scanned:** 14 files, ~1,460 live lines, ~150 discrete rules. Backup files (`*.bak.*`) were
  excluded deliberately — 24 exist under `instructions/` and none is loaded by anything.
- **Positive control run** (per `change-safety.md:140`): I did not trust `dispatch-lint`'s clean
  default output. Running it with `--universal` produced 8 findings on the same corpus, which is how
  the default-off gap in §0 was found. A sweep that has never gone red has not been shown to work.
- **CONFIRMED vs INFERRED:** every §3 contradiction, every line number, the `dispatch-lint` output,
  the state-store values, the `claude` binary target, `fworker.py:628-630`, and wq#383's `done_at`
  are **CONFIRMED** — read from disk or from a live command this session. The *cost* attributions
  (R3 "converts work into backlog", R5 "everything contends on one T5810") are **INFERRED** — they
  are the audit's assertions and I could not measure either. R5's is unmeasurable until F6 closes.
- **Origin tracing was weaker than the ticket asked.** `agents/work-queue` is not a git repository
  (`fatal: not a git repository`), so git-history archaeology was unavailable for the dispatch and
  rules files — the largest single gap in this audit. `lumen_episode.py` has no `search` subcommand
  (C10); I used `recall`, which is semantic and returned nothing relevant for R5. Where I could not
  trace an origin I flagged it (§5) rather than inventing one.
- **I did not change anything.** No file outside `reports/` was written. The two proposed RETIRE
  lines in §4.8 are text in this report, not in `RETIRED_RULES.md`.

## 8. Sources

`instincts.yaml` · `instructions/playbooks/{change-safety,session-start,ssh-access,cloudflare-ops,long-jobs,github-standards}.md`
· `instructions/manifest.yaml` · `agents/work-queue/{UNIVERSAL_RULES,RETIRED_RULES,UNIFIED_DISPATCH}.md`
· 5 sibling `*_DISPATCH.md` · `foreman/FOREMAN_DISPATCH.md` · `foreman/harness/fworker.py`
· `foreman/dispatcher/config.json` · `bin/dispatch-lint` (run twice: default and `--universal`)
· `lumen_state.py current anthropic_subscription` · `lumen_episode.py recall` · `wq show 383`
· `reports/opsaudit_a6_approval_model.md` (the JUDGE step this SUBTRACT step is gated on)
· `instructions/FLEET_OPS_AUDIT_2026-08-21.md`
