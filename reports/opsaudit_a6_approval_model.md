# A6 — The approval model: risk-tiered standing approval vs per-action halt

Ticket wq#756. Campaign spec: `/home/lumen/instructions/FLEET_OPS_AUDIT_2026-08-21.md` (R1, F1).
Measured 2026-08-22 against `/home/lumen/agents/failure-queue/failures.db`,
`/home/lumen/agents/work-queue/queue.db`, `instructions/playbooks/change-safety.md`,
`instructions/manifest.yaml` and `harness/guard.sh`.

**Nothing was changed by this pass.** No edit to `change-safety.md`, `manifest.yaml` or any
guard. §6 is diff-ready text for A9 to apply *if* Frank accepts §8. RED tier is untouched and
stays untouched.

---

## 0. The answer, before the argument

G4 was written to stop an agent from making a bad mutation. The failure history says agents
almost never make bad mutations, and that when they did, **Frank would have approved every one
of them.** The control does not act on the failure mode it was built for.

What the history actually shows failing is *verification*: an agent asserting a precondition it
never proved. So the recommendation is not "loosen G4". It is **swap the currency of approval
from permission to evidence** — replace "Frank said yes to this specific action" with "these
four artifacts exist and a separate process confirmed them, or the action does not run."

And one hard constraint on adoption, argued in §9: **the evidence-runner does not exist yet.**
Adopt the class table now, keep Class S switched off until the runner ships. Prose preconditions
have already failed twice in this fleet (fq#592, then fq#601 the same night). A third round of
prose is not a design.

---

## 1. What changed since 2026-07-09, verified rather than assumed

The ticket asserts four things are now true that were not true when G4 was written. Three hold.
One does not, and it changes the design.

| Claim | Verdict | Evidence |
|---|---|---|
| Bi-temporal state store | **holds** | `lumen_state.py` supersession; a wrong fact is corrected, not destroyed |
| Episode store | **holds** | `lumen_episode.py` precheck/commit; commits with a non-empty `--failed` auto-mirror into `fq`, 665 rows total |
| Backup verified at G3 | **does NOT hold as stated** | fq#22: `/home/lumen/pokemon` was in **0 of 393** restic snapshots while the backup jobs "ran green" for months |
| Rollback verified at G6 | **holds, but only where somebody proved it** | wq#566 restored a checkpoint, hash-matched it, and **loaded it in the emulator** — 4/0 party read. That is a real proof. It is also the only one of its kind on disk |

The third row is the whole design. "There is a verified backup" is exactly the precondition
that already failed silently, at fleet scale, for months, on the flagship product tree. Any
standing-approval model that accepts "backups are green" as its safety floor inherits fq#22
directly.

So the backup precondition below is not "a backup exists". It is **restore-proven for this
target, in this run**, to the wq#566 standard.

### What already exists that did not exist in July

`harness/guard.sh` (252 lines, PreToolUse, fail-closed on empty or unparseable input) is a
**mechanical** deny wall for every fworker session. It blocks `sudo`, reboot/shutdown/halt,
`systemctl stop|disable|mask`, `dd of=`, `mkfs`/`wipefs`, `ip link set`, `macchanger`,
`git push --force`, all `docker` except `ps`/`logs`, and reads of `/home/lumen/.secrets`. It
enforces the G0 host guard itself (`hostname != lumen` → exit 2) and writes every block to a
JSONL event stream.

That is wq#229's tier-1 blocklist, shipped — for the worker path. wq#229 designed the same wall
for Commander and it **was never applied** (its own §8 decision points are still open, including
the `sudo` fail-open hole it found in Desktop Commander's `validateCommand`). This asymmetry is
the boundary in §3: standing approval is safe only where the wall is under it.

---

## 2. What G4 actually protects — measured, not assumed

66 curated incidents in the failure queue (F0 uncurated excluded).

| class | what it is | count |
|---|---|---:|
| F9 SILENT_GAP | something broken, nobody noticed | 15 |
| F1 UNVERIFIED_ASSERTION | claimed without checking | 14 |
| F6 WRONG_TOOL | wrong environment, wrong host, wrong CLI | 7 |
| F10 CONFLICT_UNDETECTED | two contradictory facts coexisting | 5 |
| F2 STALE_RECALL | acted on a retired fact | 5 |
| F7 REDUNDANT_WORK | duplicate or colliding effort | 4 |
| F11 / F12 / F3 / F5 | unverified generic advice, lost handoff, telemetry blindness, letter-vs-intent | 3 each |
| **F4 RULE_VIOLATION** | **an agent did a thing it was told not to** | **2** |
| F8 CONTEXT_BLOAT | oversized context payloads | 2 |

**F4 is 2 of 66 — 3%.** F9+F1 together are 29 of 66 — 44%. The rule G4 enforces is aimed at the
smallest category in the ledger, and the categories that dominate it are ones a halt-for-approval
does nothing about. You cannot approve your way out of "nobody noticed".

Detection attribution on the same 66: **agent 42, Frank 20, monitor 3, never 1.** Frank is
already not the primary control. He is the escalation path for 30% of catches, and the
serialization point for 100% of mutations.

### The counterfactual that decides it

Every mutation-caused incident in the curated history, and what a per-action approval would have
done:

| incident | what happened | would Frank have said no? |
|---|---|---|
| fq#33 (06-25) | improvised initramfs edit on a headless node; an identical earlier attempt bricked Hathor's boot | **No.** "Fix the initramfs on Hathor" is a yes. The fault was improvisation on a boot path, not authorization |
| fq#32 (06-25) | GPU-B power gating generalized from Wadjet to Isis/Hathor; amdgpu binds both → `pci_channel_io_perm_failure` + overheating | **No.** "Apply the working power config to the other two" is a yes. The fault was not re-verifying per-box topology |
| fq#45 (06-03) | `lumen-pause.timer` shipped with no resume counterpart; fleet sat dead ~62h | **No.** A pause timer is a reasonable thing to want. The fault was an unpaired stop, and then 62 hours of nobody noticing |
| fq#30 (04-26) | smoke tests run against `chat_id=main`, 28 test messages into Frank's live conversation | **No.** "Run the smoke tests" is a yes. The fault was the target id |
| fq#23 (07-16) | power-ab poller built, unit left as `.service.new`, never installed, discovered dead 3 days later | **No.** The fault was that nothing checked `is-active` |
| fq#592 / fq#601 (08-15) | pipeline printed its own verdict `MISMATCH — do not reseed`, then reseeded anyway; and an episode+ntfy shipped a "BYTE-EQUAL" claim batched with the check that 403'd | **No.** Both actions were sanctioned. The fault was that a conclusion shared a batch with its own check |
| fq#10 (03-09) | agent published 25 posts against Frank's explicit repeated STOP; `DAILY_POST_CAP=1` present in code but ineffective | **He did say no.** Twice. It happened anyway |

Six of seven: the approval would have been granted, and the incident would have happened
identically on the far side of it. The seventh is worse for G4 than the other six — Frank had
issued an explicit, repeated denial and it was not enforced, because the cap that would have
enforced it was present in code and never exercised.

**Per-action approval has never been the thing that stopped a bad mutation on this fleet.** What
would have stopped six of these seven is a precondition artifact: per-box G1 re-verification, a
paired restore, a distinct test target, an `is-active` check, a separate-process gate. §4 makes
each of those a precondition, sourced from the incident that names it.

### What it costs

67 items are parked as of today (`blocked` ∪ open `user` lane). Oldest blocked: **41 days**.
Average age of an open `user` ticket: **23 days**. A2 (wq#752) enumerated 77 under a wider
definition and classified every one: **30 genuinely Frank's** (spend, hardware, risk acceptance),
9 agent-answerable, 10 stale/moot, **28 that should never have been gated** — including wq#349
(a pip install on a bench host) and wq#462 (retiring a systemd unit that targets a NIC dead since
08-02 and fails daily).

---

## 3. The class table

Four classes. **Frank sets them in a Frank-owned file; the loop never self-promotes** — that is
manifest.yaml's standing invariant and this proposal does not touch it. Anything unlisted falls
through to A, exactly as unlisted actions fall through to orange today.

**Scope boundary, non-negotiable:** Class S applies **only inside an execution context carrying
a mechanical deny wall** — today that is an fworker session under `harness/guard.sh`. The
interactive Commander path has no wall (wq#229 staged one; it was never applied), so on that
path every action stays at its pre-existing class. Standing approval without a wall under it is
just a weaker rule.

| # | action | class | boundary — what stays A | why this class |
|---|---|---|---|---|
| S1 | `service_restart` | **S** | operator-path units (`cloudflared`, `desktop-commander`, `supergateway`, `mcpw`) stay A. `stop`/`disable`/`mask` are in **no** standing class and are already denied outright by `guard.sh:88` | restart is reversible, single-unit, and its verification is one command |
| S2 | `write_file` — config edit on lumen | **S** | boot path (initramfs, GRUB, fstab, netplan) stays A. Crown jewels (`manifest.yaml`, `change-safety.md`, `lumen_exec.py`, `approvals.yaml`) stay A. Remote hosts stay A | reversible via a restore-proven sibling `.bak`; blast radius is one file on one box |
| S3 | `package_install` — venv / user scope | **S** | system `apt`, `dist-upgrade`, anything needing `sudo` stays A (and `sudo` is denied by `guard.sh:85`) | scoped to one environment; cannot change system ownership |
| S4 | `git_push_own` — feature branch, lumen-owned repo | **S** | `main`/default branch stays A. Third-party repos stay RED | already de-facto standing: every fworker lands this way via `lumen-pr` from an isolated worktree. This makes the existing practice legible |
| S5 | `ssh_remote_exec` — **read-only** | **S** | any remote mutation stays A | a read is not a mutation. Today it halts, which is pure friction. Target IP resolves from `lumen_state` in-cycle, never from prose or ssh-config |
| S6 | `state_ingest` / `episode_commit` | **S** | contradicting an existing open predicate stays A until conflict-flagging exists | bi-temporal: a wrong fact is superseded, not destroyed. Reversal is an ingest |
| B1 | CVE / package patching across a named node set | **B** | crown-jewel nodes (Ma'at/Wadjet vault) stay A | one approval, one declared window, **N independent per-target G1s**. See §4 P2 |
| B2 | bulk queue / state hygiene | **B** | anything touching `done`/`rejected` history stays A | dry-run default + idempotent, the shape A2's `opsaudit_a2_apply.sh` already ships |
| A | `db_change` (crown jewel), `cloudflare_change`, operator-path restarts, all remote mutation, `main` push, boot-path files, **anything whose G1 "why" is undetermined**, **anything unlisted** | **A** | — | unchanged from today's G4 |
| RED | reboot, dd, MAC spoof, third-party push, secret exposure, Anthropic API billing, Surface Pro business | **RED** | — | **unchanged. No approval path. Not in scope for this ticket** |

Two notes on entries that look loosened but are not:

- **S1 excludes `stop`.** fq#45 cost 62 hours of fleet downtime and it was an unpaired stop. The
  line between `restart` and `stop` is not cosmetic; it is the line between an action that
  restores its own invariant and one that does not. `guard.sh` already enforces it mechanically.
- **S3 has zero incidents against it.** In 66 curated records, no failure is attributable to a
  venv or user-scope package install. That absence is the argument for the class, and it is
  stated as an absence rather than dressed up as evidence.

The RED-tier wording problem the spec raises at R2 (agents misreading "never call Anthropic APIs"
as "avoid Claude") is real but is **A7's ticket**, not this one. The tier stays; only its prose
is arguably wrong, and rewording it is a subtraction, not an approval-model change.

---

## 4. Preconditions — the evidence a Class S action must produce BEFORE acting

Each is sourced from the incident that proves the naive version fails. All five must pass. Each
is produced by a **separate process whose non-zero exit aborts the run**; none may be
self-reported by the acting agent.

**P0 — No conclusion in the same batch as its check.**
*Source: fq#592, then fq#601 the same night — second occurrence of the identical defect.* In
#592 the pipeline printed `MISMATCH — do not reseed` and executed the reseed through
unconditional command sequencing. In #601 an episode commit and an ntfy claiming "BYTE-EQUAL via
the real service token" were batched with the parity check itself, which 403'd; the batch does
not stop on a failed sub-check, so the false claim shipped. **This is the precondition on all the
others** — a precondition an agent evaluates and reports on itself is worth nothing, and this
fleet has the receipts twice over.

**P1 — Restore-proven backup for THIS target in THIS run.**
*Source: fq#22 — `/home/lumen/pokemon` in 0 of 393 restic snapshots while backups ran green for
months.* Not "a backup job succeeded". Not "`stat -c%s > 0`". The wq#566 standard: restore the
artifact to a scratch path, hash-match it against the live source, and where the artifact is
loadable, **load it**. wq#566 did exactly this and its report says the right thing in its
subtitle: *restore PROVEN by emulator load, not exit 0*.

**P2 — Blast radius = one unit, on lumen, re-verified per target.**
*Source: fq#32 — GPU-B gating that was correct on Wadjet drove `pci_channel_io_perm_failure` and
shared-core overheating on Isis and Hathor, because amdgpu binds both GPUs there.* Its own
`prevented_by` reads: *verify per-box GPU binding topology before applying any power config
derived from a different box*. Generalizing a verified change to a second box is a **new** action
with its own G1, never a continuation of the first. This is also what makes Class B safe: **B
batches the approval, never the verification.** One yes, N independent G1s.

**P3 — A rollback command exercised in this run.**
*Source: fq#23 — the power-ab poller was built, its unit left as `power-ab-poll.service.new`,
never installed, and assumed to be collecting data for 3 days.* A rollback that has never been
run is in exactly the state that unit was in. Dry-run counts; untested does not.

**P4 — Paired restore registered with the supervisor BEFORE execute.**
*Source: fq#614 — headless workers die silently mid-ticket, twice in one day, leaving claims and
slocks dangling with nothing alerting.* The acting agent may not be alive to roll back. So the
restore is **owned by fworker, not by the agent**: registered before G5, cleared on a clean G7,
and executed by the supervisor on session death or watch-window failure. This is the single
largest build dependency in this proposal and it does not exist today.

**P5 — Any cap or alarm in the path is live-tested with a planted positive.**
*Source: fq#10 — `DAILY_POST_CAP=1` was present in the code and ineffective; 25 posts shipped
against an explicit repeated STOP. Reinforced by fq#561 — a recall-bench drift alarm installed
and enabled that has never been shown to fire, with no baseline and no planted trap.* A cap
nobody has watched trip is a comment.

---

## 5. Auto-rollback triggers — these fire without asking

| trigger | action | source incident |
|---|---|---|
| G6 re-check does not reach expected state | restore from P1 artifact, re-verify, notify FAILED+ROLLED-BACK | current playbook, unchanged |
| Unit not `active` at **T+60s and T+300s**, or `NRestarts` incremented since G5 | supervisor executes the P4 paired restore | fq#23, fq#45 |
| Session dies with an unretired P4 registration | supervisor executes the restore and files an aq alert | fq#614, fq#562 |
| Disk% or **inode%** crossed threshold since G5 | halt the class for the window, notify | fq#49 — 22.6M orphaned MinIO stage files took root to 81% disk / **87% inodes**; inode% is the one that gets missed |
| Any precondition process exits non-zero | action never executes; ticket drops to A | P0 |
| A second Class S action targets the same unit within the watch window | second action drops to A | prevents flap-masking |

Rollback is never a decision the acting agent gets to make. It is a supervisor behaviour with a
timer.

---

## 6. Diff-ready rewrite of G4

Replaces `instructions/playbooks/change-safety.md` lines 49–54 verbatim. Sized to hold the
playbook under its documented ~2K-token budget (the versioning note at line 98). **Not applied —
A9's to land if Frank accepts §8.**

```markdown
### G4 — Approval (the yellow contract)
Look up the action's CLASS in approvals.yaml. Frank owns that file; the loop
NEVER self-promotes (manifest invariant). Class S applies ONLY in a context
carrying a mechanical deny wall (fworker/guard.sh). Elsewhere: A.

  S (standing)   Act now, report after — only if ALL of P1-P4 are produced by
                 a SEPARATE process whose nonzero exit aborts the run:
                 P1 backup RESTORE-proven for this target in this run
                    (restore + hash-match + load if loadable; not exit 0)
                 P2 blast radius = one unit on lumen; a second target is a
                    NEW action with its own G1, never a continuation
                 P3 rollback command exercised this run (dry-run counts)
                 P4 paired restore registered WITH THE SUPERVISOR before G5,
                    so it fires even if this session dies
  B (batch)      One approval covers a named target set for a named window.
                 The approval batches; the verification NEVER does — every
                 target runs its own G0-G3, G5-G7.
  A (per-action) Compose ONE notify_frank request, then HALT. Silence is NOT
                 approval; approval is non-transferable and dies with the
                 session. DEFAULT for anything not listed as S or B.
  RED            No approval path. Unchanged.

Drop to A whenever: the action is unlisted; a precondition cannot be proven;
the G1 "why" of an existing setting is undetermined; or a check would report
its own result — a conclusion may NEVER share a batch with the check that
establishes it (fq#592, fq#601). Caps and alarms in the path must have been
seen to fire on a planted positive (fq#10, fq#561). approvals.yaml entries
carry an expiry; a lapsed entry reads as A, not S.
```

Companion file, Frank-owned, proposed shape only:

```yaml
# approvals.yaml — Frank-decided. The loop reads; it never writes.
version: 1
ratified: "2026-08-__"
expires:  "2026-11-__"     # lapsed => every entry reads as A
context_requirement: guard_wall   # S is void without a mechanical deny wall
standing:
  - {action: service_restart,  scope: "lumen, non-operator-path units", verbs: [restart]}
  - {action: write_file,       scope: "lumen config; NOT boot-path, NOT crown-jewel"}
  - {action: package_install,  scope: "venv/user only; no sudo, no apt"}
  - {action: git_push_own,     scope: "feature branches, lumen-owned repos"}
  - {action: ssh_remote_exec,  scope: "read-only; target from lumen_state in-cycle"}
  - {action: state_ingest,     scope: "non-contradicting predicates"}
  - {action: episode_commit,   scope: "all"}
batch: []                    # Frank names target set + window per grant
```

The expiry line answers A2's handoff finding #1 directly — *a gate is written once and read
never*. An approval that cannot lapse becomes the next thing nobody re-reads.

---

## 7. What this does NOT fix

Stated plainly, because the campaign's own §4 says the subtract step is the one that gets
skipped by enthusiasm.

- **It does not touch the 30 items that are genuinely Frank's.** A2 measured them: spend,
  physical hardware, risk acceptance, business. Those stay his under any model. The three that
  compound daily — wq#556, wq#418, wq#305 — are unaffected by this proposal and are still the
  most valuable page in the backlog.
- **It does not fix the largest failure classes.** F9 (silent gap) and F1 (unverified assertion)
  are 44% of curated incidents and this is not a control for either. It removes a control that
  never addressed them; it does not add one that does.
- **The win is bounded and mostly comes from two cheap rules, not from the table.** A2's finding:
  *research-before-build failures close themselves with the disproof*, and *reversible +
  backed-up + single-unit = fix it*. The class table is the second of those, made specific.
- **Three of A2's 28 over-gated items are a validation bug, not a policy question.** Build
  tickets mis-filed into the `user` lane are unreachable because `wq claim` refuses that lane by
  design. No approval model fixes that; a lane check does.
- **It does not close the Commander gap.** wq#229's tier-1 blocklist and its `sudo`-strip patch
  are still staged and unapplied, and its own §7 gaps (`ip link` MAC path, `write_file`/
  `edit_block` unscoped, fleet SSH remote not argv0-blockable) are unsolved. Class S is
  deliberately scoped away from that path rather than pretending otherwise.

---

## 8. What Frank is agreeing to — one paragraph

You are agreeing that for six narrow, reversible, single-box action types — restarting a
non-operator-path service on Lumen, editing a non-boot-path config file on Lumen, installing into
a venv, pushing a feature branch on our own repos, running a read-only command on another node,
and writing to the state or episode store — a worker running under the guard-hook deny wall may
act without pinging you, provided a separate process has first proven a restorable backup of that
exact target, a one-unit blast radius, an exercised rollback, and a restore registered with the
supervisor that fires even if the worker dies; anything else, including every stop/disable, every
remote mutation, every crown-jewel database, Cloudflare, `main`-branch pushes and the whole RED
tier, still halts for you exactly as today. In exchange you stop being the serialization point
for the reversible 36% of a 67-item backlog whose oldest item is 41 days old. What you are
accepting in risk is narrow and named: an agent can now break one service or one config file on
Lumen without asking first, and you find out from the report instead of the request. What the
evidence says you are giving up is close to nothing — in 66 catalogued failures, exactly two were
rule violations, and in every mutation-caused incident on record you would have approved the
action anyway.

**Accept / reject / amend the table in §3.** If you reject, say which row; the classes are
independent and S5 (read-only remote exec) in particular is defensible on its own.

---

## 9. Recommended adoption ladder — do not switch S on yet

The honest engineering position: **the table is ready and the enforcement is not.** Three things
in §4 do not exist — the precondition runner (P0/P1/P3), the supervisor-owned paired restore
(P4), and `approvals.yaml` plus its reader. Until they do, Class S is prose, and prose
preconditions failed in this fleet on 2026-08-15 and then failed again the same night.

| step | what lands | gated on |
|---|---|---|
| 1 | **S5 only** — read-only `ssh_remote_exec` reclassified. No new machinery: a read cannot mutate, and G0's in-cycle target resolution already exists | nothing. Safe today |
| 2 | `approvals.yaml` + reader + expiry; table adopted with **every S row switched off** | Frank accepts §8 |
| 3 | Precondition runner as a separate process (P0/P1/P3), P1 built to the wq#566 restore-proof standard | step 2 |
| 4 | Supervisor-owned paired restore + watch window in fworker (P4, §5) | step 3 |
| 5 | S1/S2/S3/S4/S6 switched on, one row at a time, each with a planted-positive test proving its abort path fires (P5) | step 4 |
| 6 | Apply wq#229's staged Commander blocklist + `sudo`-strip, then reconsider extending S beyond the worker path | Frank-attended; wq#229 §8 |

A9 is the ticket that applies whatever Frank accepts. Steps 3 and 4 are real build work and
should be their own tickets rather than riders on A9.

---

## 10. Sources

- Failure queue, 66 curated records: `/home/lumen/agents/failure-queue/failures.db`. Cited:
  fq#10, #12, #22, #23, #26, #30, #32, #33, #45, #49, #561, #562, #592, #601, #614.
- Work queue, measured 2026-08-22: 67 parked (`blocked` ∪ open `user`), oldest blocked 41d, mean
  open `user` age 23d. wq#383 confirmed `done_at 2026-08-01`, matching A2's correction to F1.
- `reports/opsaudit_a2_frank_backlog.md` — the 77-item classification and its §6 handoff.
- `reports/566_lpp_backup.md` — the restore-proof standard P1 adopts.
- `/home/lumen/agents/work-queue/reports/229_writeaccess_governance.md` — the shipped
  risk-tiered design, its control table, and its still-open §8 decision points.
- `instructions/playbooks/change-safety.md` (G0–G7, HARD RULES), `instructions/manifest.yaml`
  (four-color contract, `fallthrough: orange`, no self-promotion), `harness/guard.sh` (the wall).
