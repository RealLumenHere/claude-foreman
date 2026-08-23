# Enforce UNIVERSAL_RULES in dispatch-lint + CI the dispatch files (ops-audit A8 / wq#758)

Campaign spec: `/home/lumen/instructions/FLEET_OPS_AUDIT_2026-08-21.md` (F7). Audit finding: the
linter enforced `RETIRED_RULES.md` (8 rules, all files clean) but nothing enforced
`UNIVERSAL_RULES.md` — the rules whose absence has already cost money — and the six dispatch
files disagreed four ways about the lease TTL.

## 0. What already existed vs. what this ticket built

`load_universal()`/`check_universal()` in `/home/lumen/bin/dispatch-lint` already implement the
REQUIRE/AGREE checks the ticket scope describes (shipped under wq#561, gated behind an opt-in
`--universal` flag). That part of the scope was **already done** — verified by reading the
source and confirming the flag produces exactly the REQUIRE/AGREE report shape the ticket
describes. `--universal` was deliberately kept opt-in per the flag's own comment: turning it on
by default the day it was built would have fired on 7 known, then-unfixable violations and
trained everyone to ignore the unit. This ticket is the "same change that normalises the six
dispatches" the comment in `lumen-dispatch-lint.service` said would flip it on.

What this ticket actually built:
1. Fixed the seven REQUIRE/AGREE violations in the six dispatch files (never by editing
   `UNIVERSAL_RULES.md`'s regexes — the constraint was to correct the files, not the rule).
2. Resolved the heartbeat-TTL disagreement against the value `wq` actually enforces.
3. Added `dispatch-lint --universal` as a preflight in `wq-lane-init`, tested both the pass and
   the refuse-and-clean-up path.
4. Staged (but could not install — see §4) `--universal` onto the daily systemd timer.

## 1. Baseline measurement (`dispatch-lint --universal`, before any edit)

```
UNIVERSAL RULES NEVER STATED (7):
  ARTICLE_RESEARCH_DISPATCH.md  [no-add-all] [precheck] [progress-board]
  FABLE_QUEUE_DISPATCH.md       [no-add-all] [precheck] [progress-board]
  UNIFIED_DISPATCH.md           [no-add-all]

UNIVERSAL RULES STATED INCONSISTENTLY (1):
  [heartbeat-ttl]  '240': ARTICLE_RESEARCH_DISPATCH.md, FABLE_QUEUE_DISPATCH.md
                   '90':  GITHUB_DISPATCH.md, UNIFIED_DISPATCH.md
```
Retired-rule check was already clean (8 files, 8 rules, 2 legitimate citations exempted in
`POKEMON_DISPATCH.md`) — untouched by this ticket.

A prior attempt at this ticket (killed) had taken `.bak.20260822_011910_pre_wq758` snapshots of
all six files but made no edits — confirmed with `diff` against the live files before starting;
this pass started from the same baseline above, not from partial work.

## 2. Heartbeat TTL — what wq actually enforces

`/home/lumen/bin/wq`: `DEFAULT_TTL_MIN = 240` (line 71), used as the fallback for both `claim
--ttl` and `heartbeat --ttl` wherever the caller omits an explicit value. **240 is the enforced
truth.** UNIFIED_DISPATCH.md and GITHUB_DISPATCH.md instructed `--ttl 90` — an explicit
override *below* the real default that nothing in wq's own code or docs justifies — while
FABLE_QUEUE_DISPATCH.md and ARTICLE_RESEARCH_DISPATCH.md already said 240. Fixed both `claim`
and `heartbeat` call sites in UNIFIED_DISPATCH.md and GITHUB_DISPATCH.md from 90 to 240 (four
call sites total) so every file that states a number states the one wq enforces.

## 3. Per-file fixes

**UNIFIED_DISPATCH.md**
- `wq claim --lane $L --ttl 90` → `--ttl 240`; `wq heartbeat <id> --ttl 90` → `--ttl 240`.
- Added `git add -A` ban to the "Universal floor" section (this lane does real commits via the
  GITHUB LAW section, so the rule is directly load-bearing here, not decorative).

**GITHUB_DISPATCH.md**
- `wq claim --lane github --ttl 90` → `--ttl 240`; `wq heartbeat <id> --ttl 90` → `--ttl 240`.
- no-add-all/precheck/progress-board were already present — untouched.

**ARTICLE_RESEARCH_DISPATCH.md** (research lane; "Research only — web_search + writing a report
file", runs many agents in parallel per its own header)
- Added a precheck step (new LOOP step 2) before the only state change this lane makes —
  writing the report file.
- Added a real coordination board reference + a genuine board file:
  `boards/RESEARCH_QUEUE_PROGRESS.md` (did not exist before; created following the exact header
  format `wq-lane-init` stamps on every other lane's board). This lane runs multiple parallel
  agents per its own dispatch text, so a real board — not just a string that satisfies the
  regex — is the correct fix per the ticket's "never weaken to pass the linter" constraint.
- Added a conditional no-add-all bullet (this lane doesn't commit code today, but the rule is
  scope ALL for exactly the case where a future ticket asks it to).

**FABLE_QUEUE_DISPATCH.md** (produces reports/specs/diffs/tests only, per its own GUARDRAILS —
"even when a pack yields a fix, output the diff + passing test; never apply it")
- Added a precheck step (new LOOP step 3, before the reasoning pass).
- Added a real coordination board reference + `boards/FABLE_QUEUE_PROGRESS.md` (same reasoning
  as ARTICLE — `wq list fable` is the ticket queue, not a coordination board; this lane needed
  an actual one).
- Added a conditional no-add-all bullet under GUARDRAILS.

**POKEMON_DISPATCH.md**, **_TEMPLATE_DISPATCH.md** — already fully compliant; both are the gold
reference the wording above was matched against. Untouched.

## 4. Wiring — timer and wq-lane-init

**wq-lane-init (done, tested):** added a preflight immediately after the template is stamped
out — `dispatch-lint --file <new dispatch> --universal`. A failure prints the violations, `rm`s
the just-written dispatch file, and exits 1, so a broken template can never hand out a
non-compliant lane. Verified both branches:
- Happy path: `wq-lane-init wq758test /tmp/... "..."` → `dispatch-lint: 1 file(s) clean...` →
  `LANE READY`.
- Failure path: temporarily stripped the precheck line from `_TEMPLATE_DISPATCH.md`, re-ran
  under a second throwaway lane name → linter printed the missing `[precheck]` rule, script
  printed `REFUSING: ... failed dispatch-lint --universal`, exited 1, and the half-written
  dispatch file was gone afterward. Restored the template byte-for-byte (`diff` clean) and
  deleted every throwaway artifact (test dispatch/board files, `/tmp` scratch dir) before
  finishing.

**lumen-dispatch-lint.timer (staged, not installed):** `/etc/systemd/system/*` is root-owned;
`test -w` on the live `.service` file confirms this session cannot write it, and `sudo` is on
the house deny-list, so I cannot install this myself. Followed the fleet's existing convention
for exactly this situation (see `power-remediate.service.staged` /
`power-remediate.timer.staged`, landed by wq#637 and later installed by Frank) and staged
`/home/lumen/bin/lumen-dispatch-lint.service.staged`: identical unit, `ExecStart` changed from
`/home/lumen/bin/dispatch-lint` to `/home/lumen/bin/dispatch-lint --universal`, comment rewritten
to explain why it's now safe to enable (six files fixed, wq-lane-init preflights new ones).
**Frank/root action still needed:** `cp` it over the live file and
`systemctl daemon-reload` (no `enable`/`start` needed — the timer unit and its `WantedBy` are
unchanged).

"CI the dispatch files": `/home/lumen/agents/work-queue` is not a git repo (confirmed — no
`.git` in it or any parent up to `/home/lumen`), so there is no push-triggered CI to hook into.
The two enforcement points the ticket names — the daily timer and the `wq-lane-init` preflight —
are this fleet's equivalent for a non-versioned live-config directory: continuous enforcement on
a schedule, plus a birth-time gate that makes a new dispatch un-born rather than born-broken.

## 5. Final state

```
$ dispatch-lint --universal
CITATIONS EXEMPTED (2) — POKEMON_DISPATCH.md lock-branch-lifetime citations, unchanged, correct.
dispatch-lint: 8 file(s) clean against 8 retired rule(s), 2 citation(s) exempted
$ echo $?
0
```
All six dispatch files pass both the retired-rule check and `--universal`. Zero rules were
edited to make this true — `UNIVERSAL_RULES.md` and `RETIRED_RULES.md` are unchanged from
before this ticket.

## 6. Files touched

- `/home/lumen/agents/work-queue/UNIFIED_DISPATCH.md` — edited
- `/home/lumen/agents/work-queue/GITHUB_DISPATCH.md` — edited
- `/home/lumen/agents/work-queue/ARTICLE_RESEARCH_DISPATCH.md` — edited
- `/home/lumen/agents/work-queue/FABLE_QUEUE_DISPATCH.md` — edited
- `/home/lumen/agents/work-queue/boards/RESEARCH_QUEUE_PROGRESS.md` — created
- `/home/lumen/agents/work-queue/boards/FABLE_QUEUE_PROGRESS.md` — created
- `/home/lumen/bin/wq-lane-init` — edited (preflight gate), committed via `lumen-pr` in the
  `lumen-bin` repo
- `/home/lumen/bin/lumen-dispatch-lint.service.staged` — created, committed via `lumen-pr` in
  the `lumen-bin` repo; **not yet installed** (needs root — see §4)
- `/home/lumen/bin/dispatch-lint` — unchanged (REQUIRE/AGREE logic pre-existed from wq#561)
