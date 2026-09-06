# Broker wiring — make fleet-ops tickets run

Status: DESIGN, review before code. Route: fable-5.1@high (novel-design). Tree: claude-foreman @ 1f81413,
lumen-bin `fleet-exec` as installed at /home/lumen/bin (PR #59), live state probed 2026-09-05 23:59 PDT.
Builds on research/WORKER_EXEC_CAPABILITY_GAP.md (§ refs below are to that doc unless stated),
research/NIGHTLY_DISPATCH_RESILIENCE.md, wq#1330-1335. Every claim carries file:line from the live tree
or a live probe; where a claim was NOT reproduced it says so.

## 0. What is actually true today (verified, not restated)

The prompt's four gaps, checked against the tree one by one:

| prompt claim | live state | verdict |
|---|---|---|
| tickets aren't tagged | `needs` column exists (wq#1331 migration) and `needs.py:86 parse()` reads it plus a `Needs:` detail line; **0 of 110 open+blocked tickets carry a tag** (sqlite scan of queue.db, 2026-09-05) | TRUE — the parser has no input |
| dispatcher spawns-then-fails instead of skipping | `foreman_dispatcher.py:937-940` holds a ticket AFTER its first `failure.json` says HARNESS_DEFECT (wq#1415, PR #287). Nothing runs the reachability check BEFORE the first spawn. So each stuck ticket now costs exactly one uncharged LAUNCH_FAILURE + one 24h-deduped ntfy, then sits held forever. Pre-PR#287 history: wq#1361 spawned 65x, #1362 46x, #1363 44x, #1364 40x, #1401 33x, #1402 32x, #1403 31x (dispatcher.log + .gz) | PARTLY TRUE — the loop is stopped, the churn-killer is a hold, and the hold never releases without a human |
| sandbox doesn't widen for granted dirs | `fworker.py:1069-1076 add_dirs_for(archetype, needs_dirs)` unions `needs.dirs` into the `--add-dir` list rendered at `:1106`; called at `:1639` from `do_attempt` | FALSE — widening is built and wired. It has never fired because no ticket has a `needs:dir` tag |
| action path (ssh/restart/copy) unproven | `foreman/events/fleet-exec.jsonl` = 11 lines, all wq#1332's synthetic probes. Live probe on isis (below): **restart cannot work on any non-Lumen node as written**, copy targets do not exist | TRUE, and worse: two of three verbs are broken outside Lumen |

Live probe evidence (2026-09-05, `ssh -o BatchMode=yes isis`, `ssh ... thoth`):
- `sudo -n true` on isis -> OK (fleet NOPASSWD holds); `systemctl --no-ask-password restart <unit>` as the ssh user ->
  `Access denied as the requested operation requires interactive authentication` (polkit). `fleet-exec:240`
  runs `["systemctl", action, unit]` with no `sudo -n` -> every remote restart is a polkit refusal.
- `fleet-exec:45 HOLDDOWN_BIN=/home/lumen/bin/holddown`, invoked ON THE TARGET NODE via `run_ssh` at `:222-227`.
  isis has no `/home/lumen/bin/holddown` -> `holddown registration failed ... restart NOT attempted` (`:236-238`).
  Net: `restart` can only ever succeed for node=lumen. The fail-closed branch is correct; the design behind it is not.
- `copy_dirs` (`fleet-exec-policy.yaml:96-102`) name `/home/<user>/lumen-drop` on six nodes. None exists (checked isis,
  lumen). Every `copy` will scp into a missing dir and fail after the audit line says allow.
- thoth: `ssh: connect ... port 22: Connection timed out` (offline since the be9300 router reset 09-02, lumen_state
  `router-be9300.last_reset`). This is test (v) available for free.
- `_runs/wq1401/failure.json`: `summary` names the unreachable path but `unreachable_paths: []` and
  `inferred_needs: []` — `failure_record.py` never parses a SpawnRefused summary into the structured fields the
  §4.2 auto-triage was designed to read. Same in wq1361/1402/1403/1407.

Corpus (queue.db scan, `cited_paths()` from `fworker.py:828` against `LANE_REPO[lane] ∪ agents.yaml:36-43`):
37 of 110 open+blocked tickets would trip the lint today — 24 cite an unreachable path, 20 name an exec action
(ssh/scp/sudo/systemctl restart/adb/reboot), 7 both. 26 of the 37 are `blocked`. Cited-path examples that must
NEVER become a read grant: `/home/lumen/.secrets/github-frank.token` (wq#1133), `/home/lumen/secrets/reddit.env`
(wq#1008), `/home/lumen/worktrees/wq1357-a1` (wq#1358, a dead worktree). Any lint that "derives needs:dir from
cited paths" without a deny-list hands a worker Frank's tokens on its first pass.

## 1. Class, not instance

Class of defect: **capability is checked in the wrong place and at the wrong time.** The only reachability/grant
check the fleet has (`fworker.py:837 assert_spawn_paths`, `:860 resolve_exec_grant`) runs inside the spawned
child, after a slot is taken, after a worktree is cut, and it reports through a failure record that the
dispatcher only reads on the NEXT pass. The dispatcher's own stages (`:876-1061`) check lane, repo, runaway,
deps, budget, preflight stamp — everything except "can this worker reach what the ticket cites and do what it
asks". Where else the class lives: `router.repo_mismatch()` (`router.py`) is the same shape done right (a
pre-spawn refusal with a reason), which is why the fix is to move the capability check next to it, not to
add a third copy.

What makes the class impossible: ONE function, `harness/capability.py: preflight(ticket, repo, add_dirs) ->
Verdict`, that both the dispatcher (pre-skip) and fworker (belt-and-braces at spawn) call. fworker keeps its
call so a hand-spawned `lumen-run fworker` still refuses; the dispatcher's call is what stops the slot burn.
A second copy of the logic is a lint failure (`tests/test_capability.py` asserts fworker imports it).

## 2. Design

### 2.1 capability_lint — derive needs from what the ticket cites (deliverable 1)

`harness/capability_lint.py: derive(ticket) -> Derived(tags, unresolved, refused)`. Deterministic, regex + state
store, no LLM. Inputs: title + detail. Three output buckets, never merged:

- `tags` — derivable AND safe to auto-apply:
  - `dir(<path>)` for every cited `/home/lumen/...` literal (`fworker.cited_paths`, `:828`) outside
    `repo ∪ add_dirs`, normalised to the deepest existing DIRECTORY prefix (a cited file becomes its parent dir;
    a non-existent path is `unresolved`, not guessed). Read grant only (`--add-dir` is read context,
    `fworker.py:1070-1072`).
  - `ssh(<node>)` when detail contains `ssh <node>`, `ssh <user>@<ip>`, `on <node>` next to a probe verb, or a
    bare tailscale/LAN IP that resolves to exactly one subject via `lumen_state.py current <node>`
    (`tailscale_ip`/`lan_ip` facts). Grants read-only `run` only (`fworker.py:865`).
  - `restart(<unit>@<node>)` ONLY from the explicit shapes `systemctl restart <unit>` / `restart <unit>(.service)?
    on <node>` / `restart(<unit>@<node>)`, node resolved as above; node omitted = `lumen`.
  - `copy(<node>:<dir>)` ONLY from `scp|rsync ... <node>:<dir>` shapes where `<dir>` is that node's `copy_dirs`
    entry; anything else is `unresolved`.
- `unresolved` — an action verb or node mention the regexes saw but could not pin to one tag (node not in
  state store, `sudo` with no unit, `adb`/`fastboot`/`reboot`/`power` which are v2 verbs, a repo path not in
  `repo_sweep.managed_repos()`). The dispatcher SKIPS with reason `needs_unresolved` + the lint text in `detail`.
- `refused` — a derived tag the static policy or the lint deny-list forbids. Dispatcher SKIPS with reason
  `capability_denied_by_policy`. Deny-list for `dir`, hard-coded in the lint (not policy.yaml, same reasoning
  as `THOR_HARDLOCK`, `fleet_exec_policy.py:26`): `/home/lumen/.secrets`, `/home/lumen/secrets`,
  `/home/lumen/.ssh`, `/home/lumen/.claude`, `/home/lumen/.config`, `/home/lumen/worktrees`,
  `/home/lumen/scratch/worktrees`, `/home/lumen/agents/work-queue/queue.db*`, any path whose basename matches
  `*.env|*.token|*.key|*.pem|id_*`, and `/home/lumen` itself or `/`. A cited secret is refused, logged, and the
  ticket stays skipped until a human rewrites it — that is correct: a ticket that needs a token is a
  Frank-hands ticket (`WORKER_EXEC_CAPABILITY_GAP.md §1` "correctly Frank").

Auto-tag = write the `tags` bucket to the ticket's `needs` column through `wq edit --needs` (validated by
`needs.py:98`), union with whatever is there, plus a `wq note` line `capability_lint: +dir(...) +ssh(...)` so the
ticket shows its own history. Written by the DISPATCHER (which already owns the ticket lifecycle), never by
the worker. Retry self-correction: a ticket that still fails `preflight()` after auto-tag is skipped, not
spawned; if a spawn later dies CAPABILITY_DENIED with `inferred_needs` (§4.2 of the gap doc), the same
`wq edit --needs` path back-fills ONCE and the second identical failure is final and pages. Two writers, one
function, one column.

Why auto-apply is safe by construction: `dir` is read-only and deny-listed; `ssh` is read-only `run` under the
broker's `run_readonly_cmds`; `restart` is bounded by `never.restart` + holddown + verify-after (§2.4); `copy` is
into a per-node drop dir. Nothing the lint can emit reaches sudo, power, bench, Thor, a crown-jewel unit, or a
secret. Tags outside that set do not exist in v1 (`needs.py:19` still lists them; `validate()` accepts them;
the lint never emits them and `resolve_exec_grant` has no branch for them).

### 2.2 Dispatcher pre-skip (deliverable 2, the churn killer)

New stage 3.7 in `_do_pass`, immediately after `repo_for_ticket()` (`foreman_dispatcher.py:919`) and before the
runaway/harness-defect holds (`:927`, `:937`):

```
needs, lint = capability.effective_needs(t, repo)           # ticket.needs ∪ lint.tags
if lint.refused:   skip "capability_denied_by_policy";  continue
if lint.unresolved: skip "needs_unresolved";           continue
v = capability.preflight(t, repo, add_dirs_for(archetype, needs.dirs), needs)
if not v.ok:       skip v.reason;                      continue   # cited_path_unreachable | node_unknown | grant_denied
if lint.tags and not dry_run: capability.apply_tags(tid, lint.tags)  # wq edit --needs + note
```

`preflight()` is the moved body of `assert_spawn_paths` + the policy half of `resolve_exec_grant` (grant
computation stays in fworker; the dispatcher only asks "would it be granted") + a new `node_reachable()`: the
node must be a `lumen_state` subject with a `tailscale_ip` fact AND have a `Host` entry in `~/.ssh/config`/
`fleet_config` (fleet-exec's `known_nodes()` at `fleet-exec:60` checks the first only; a node in the state
store with no ssh alias fails at transport time today). Reachability (ssh up) is NOT checked here — that is
the broker's job at call time (§2.4), and an offline node must not make a ticket structurally undispatchable.

Skip reasons are structural for the watchdog: `count_structural_skips` (`foreman_dispatcher.py:1080`) gains
`needs_unresolved`, `capability_denied_by_policy`, `cited_path_unreachable` so `dispatchable` in `pass_end`
stays honest (NIGHTLY_DISPATCH_RESILIENCE §3.3.1). Each reason ntfys once per ticket per 24h with the exact
missing tag, same dedup pattern as `handle_harness_defect_hold` (`:619`).

`harness_defect_hold` (`:937`) stays as the backstop for defects the pre-skip cannot see (WORKTREE_REPO_MISMATCH,
hook crash). It must RELEASE when the hold reason is a CITED_PATH_UNREACHABLE whose paths are now covered:
`runaway.harness_defect_ticket_ids()` (`runaway.py:66`) gains "ignore a HARNESS_DEFECT failure.json whose
`unreachable_paths` are all inside the ticket's current effective add_dirs" — otherwise auto-tagging a held
ticket never lets it run. That requires §2.5's failure.json fix.

### 2.3 Sandbox widen (deliverable 3) — prove, don't build

Already built: `fworker.py:1069 add_dirs_for` unions `needs.dirs`; `:1106` renders `--add-dir` per dir;
`:1638-1639` parses the ticket's needs and passes them. What is missing is a proof that a `needs:dir` tag
written by the lint reaches the child. Test (i) in §4 is that proof. One code change: `add_dirs_for` must
apply the same deny-list as the lint (`capability.DIR_DENY`) so a hand-written `needs:dir(/home/lumen/.secrets)`
on a ticket is refused at spawn as well as at dispatch — today `needs.validate()` only checks "absolute path".
Action grants keep routing through the broker: no `--add-dir` is ever derived from `ssh`/`restart`/`copy`.

### 2.4 Broker action path (deliverable 4) — make `restart` and `copy` real, keep them closed

Changes to `/home/lumen/bin/fleet-exec` (lumen-bin, via lumen-pr). The gate order at `fleet-exec:202-216`
(policy -> node known -> grant -> policy.check_restart) is kept verbatim; everything below is AFTER the allow
decision and BEFORE/AROUND execution.

1. **Privilege.** Remote command becomes `["sudo", "-n", "systemctl", action, unit]` for system units. Precheck
   `["sudo", "-n", "true"]` on the node first; a non-zero exit = `deny rule=sudo_nopasswd_missing`, no restart
   attempted, ntfy. `sudo -n` is the ONLY sudo the broker ever emits, its argv is a literal in the broker, and
   `never.sudo_argv_deny` still governs the v2 `sudo` verb — this is not that verb. guard.sh (`:84-85`) still
   denies a raw `sudo` token in the worker's own Bash; `fleet-exec restart isis ollama` carries none.
2. **Stop-restore contract without a remote binary.** Holddown is registered on LUMEN, where the healer and the
   flag dir live (`lumen.stop_restore_contract` fact), as `holddown set <unit>@<node> --ticket wq#N
   --restore-by +<grant-minutes>m --restore-cmd "/home/lumen/bin/fleet-exec-restore <node> <unit>"`.
   `fleet-exec-restore` is a lumen-only helper (no grant needed, runs as the healer) that issues
   `sudo -n systemctl start <unit>` on `<node>`. The remote `HOLDDOWN_BIN` path (`fleet-exec:45,222`) is deleted.
   For node=lumen the plain local `holddown set <unit>` is used, unchanged. Holddown's own healer semantics are
   untouched: ACTIVE flag -> never touched; EXPIRED -> `restore-cmd` runs; that is exactly "the unit comes back
   even if the worker dies mid-ticket".
3. **Verify-before-act, verify-after.** Before: `systemctl show -p LoadState,ActiveState,SubState <unit>` on the
   node; `LoadState != loaded` -> `deny rule=unit_not_found` (a typo must not create a holddown). Record
   `before={active,running}` in the audit line. After: poll `systemctl is-active <unit>` at 1s up to 30s
   (reload/start: same); `after=active` -> exit 0, holddown dropped, audit `verified=true`. `after != active`
   after 30s -> ONE `sudo -n systemctl start <unit>`, re-poll 15s; still not active -> exit 1, holddown LEFT
   ACTIVE (healer keeps restoring), ntfy urgent `lumen-alerts` with node/unit/before/after. The worker sees a
   non-zero exit and cannot claim the restart worked — `verified` is a field in the audit record, and the
   acceptance gate for a `needs:restart` ticket (`acceptance_mod`) requires `verified=true` in
   `events/<worker>.jsonl`.
4. **Offline node fails closed + alerts, never hangs.** `run_ssh` already sets `ConnectTimeout=10` and
   `timeout=90` (`fleet-exec:124-128`). Add: transport error -> audit `decision=allow rule=ssh_transport_error`
   (exists, `:147-152`) AND `ntfy-send -p high -T "fleet-exec: <node> unreachable"` AND a
   `lumen_state.py ingest <node> broker_last_transport_error <ts>` fact so the next dispatch pass can skip
   `ssh(<node>)`/`restart(*@<node>)` tickets with `node_offline` for 30 min instead of spawning into the same
   wall. Exit stays 2. The worker's wall-clock is untouched: 10s connect + 90s cmd is the worst case per call.
5. **copy targets exist.** Provision `/home/<user>/lumen-drop` (0755, owner = ssh user) on every node in
   `copy_dirs` via `ssh <node> -- mkdir -p ~/lumen-drop` from Lumen, recorded as a `lumen_state` fact
   `<node>.lumen_drop=/home/<user>/lumen-drop`. `cmd_copy` prechecks `test -d <remote_dir>` and denies
   `rule=copy_dir_missing` rather than failing after the allow line. The policy file is unchanged.
6. **Audit completeness.** Every record gains `before`, `after`, `verified`, `grant_sha` (so an offline replay
   can join `grant.json` -> `attempt-N.json` -> `fleet-exec.jsonl` -> `events/<worker>.jsonl` by sha and ts).
   Never secrets: argv is already the only payload and `sudo -n` carries none; stdout head stays at 240 bytes.

Not in v1, still: `sudo <cmd>`, `bench`, `power`, `docker restart`. The stuck tickets that need them (wq#354,
#362, #665, #679, #683, #684, #1295) stay `needs_unresolved` with the v2 tag named in the skip detail, which is
the honest state.

### 2.5 failure.json structured fields (prerequisite, small)

`failure_record.py` on a SpawnRefused with code CITED_PATH_UNREACHABLE must fill `unreachable_paths` from the
refusal (fworker already has the list at `fworker.py:848-850`; pass it through `SpawnRefused`, do not re-parse
the summary string) and `inferred_needs` = the lint's `tags` for those paths. CAPABILITY_DENIED_BY_POLICY
fills `inferred_needs: []` and a `refused: [...]` list. This is what §2.2's hold-release and the gap doc's §4.2
auto-triage read; today both read empty lists (wq1401/1361/1402/1403/1407 failure.json, verified).

## 3. Safety (deterministic; nothing model-judged)

- **No LLM in the gate.** `capability_lint`, `capability.preflight`, `fleet_exec_policy.check_*`, guard.sh and the
  broker are regex/dict/argv code. The lint's output is a proposal the DISPATCHER applies under fixed rules;
  a worker cannot edit `needs` (no `wq edit` in its allowlist — `worker-settings.json`, unchanged) and cannot
  write `grant.json` (0400, sha in env, `fworker.py:910-912`, `fleet_exec_policy.load_grant`).
- **Never-list, regardless of grant.** Thor: `THOR_HARDLOCK` in code (`fleet_exec_policy.py:26`) + `never.nodes`
  (`policy.yaml:19`) -> restart/sudo/power denied on Thor even if the policy file is edited. Lumen MAC: `ip`,
  `macchanger` in `sudo_argv_deny` (`policy.yaml:45-46`) and guard.sh's own MAC rule on the worker side; the
  broker has no verb that takes an interface. Crown-jewel power: `never.power` (`:24`) and no `power` verb in
  v1 at all. Destructive: `check_argv_shape` (`fleet_exec_policy.py:123`) rejects `-c`, `bash`, `sh`, `eval`,
  `|;&$(`; `run` argv[0] must be in `run_readonly_cmds` (`policy.yaml:61-77`), which contains no `rm`, `dd`,
  `git`, `docker rm`; `sudo -n` in §2.4 is a broker literal, never worker argv. The lint adds the dir deny-list
  (§2.1). Test (iii) proves each row.
- **Propose, don't deploy.** The broker's only mutating verbs are `restart` (reversible: unit comes back, holddown
  + healer + `fleet-exec-restore`) and `copy` (into a drop dir, never over a live path). Config edits land as a
  PR (`lumen-pr`), and the PR is the proposal. Anything irreversible (power, db, MAC, Thor, crown-jewel units)
  is ungrantable, so the only path is a decision-brief ticket for Frank via `wq decide` — that is the existing
  `gate_type` machinery, not a new one.
- **Verify-before-act.** §2.4 items 3-4: LoadState before, is-active after, alert on drift, never act on an
  unverified read. The fleet's false-read incidents this week are the reason `before`/`after` are audit
  fields, not log prose.
- **Scoped + expiring + replayable.** Grant = one ticket, one attempt, listed nodes/units/dirs, `expires` =
  wall-clock lease (`fworker.py:903-905`); broker re-derives the sha (`load_grant`); audit is append-only jsonl in
  two places (`fleet-exec:80-83`). Offline replay: `grant.json` + `attempt-N.json` + `fleet-exec.jsonl` +
  `events/<worker>.jsonl` joined on `grant_sha`/`worker`/`ts`; §2.4.6 adds the sha to every line so the join
  needs no timestamps.

## 4. Test-to-failure (each is a script under harness/tests or dispatcher/tests + one live drill; the doc is
not "done" until the live column is filled in by the landing PR)

| # | case | inject | must observe (unit) | live drill |
|---|---|---|---|---|
| i | read-widen runs + lands | synthetic build ticket citing `/home/lumen/agent` (outside add_dirs), no tag | lint -> `dir(/home/lumen/agent)`; pre-skip passes; `wq show` gains `needs`; child argv contains `--add-dir /home/lumen/agent` (assert in `build_claude_cmd` output) | worker reads a file there, lands a one-line doc PR via `lumen-pr`; `done` |
| ii | scoped restart, audited, verified | synthetic ticket `Needs: restart(ollama@isis)` | grant has restart isis ollama; broker audit shows `before`, `allow`, `after=active`, `verified=true`; Lumen holddown flag created then dropped; acceptance gate reads `verified` | **PASSED 2026-09-06** (wq#1440, lumen-bin PR #63). `fleet-exec restart isis ollama` exit 0; `events/fleet-exec.jsonl` gained `before={loaded,active,running,139751292}` then `after={loaded,active,running,516198266878}`, `verified=true`, `grant_sha=91bc3134`. `ActiveEnterTimestamp` moved `Mon 2026-08-31 08:47:04 UTC` -> `Sun 2026-09-06 08:08:02 UTC`; `holddown list` -> no flags (`ollama@isis` set then dropped). Rung healthy after: the fleet's own gatus probe for it, `http://100.100.129.24:11434/api/tags` (a listing, never a chat probe — wq#907 single decode slot), returns `qwen3.6:35b-a3b` + `qwen3:32b`, and LiteLLM `/health/readiness` is `healthy`. The `acceptance gate reads verified` half is NOT shipped — it is foreman-side and slice E is lumen-bin only |
| iii | never-list refused, logged, skipped | three tickets: `restart(tailscaled@thor)`, detail `spoof enp0s25 MAC 64:00:6a:58:fc:d8`, detail `rm -rf /home/lumen/foreman` on isis | (a) pre-skip `capability_denied_by_policy` before spawn, no `grant.json`; (b)+(c) lint: `unresolved`/no tag, skip; and a hand-spawned worker with a forged `Needs:` line hits `deny` rows `policy:never.nodes`, `policy:shell_escape_basename`, `grant:no_ssh_run` in `fleet-exec.jsonl` | zero ssh sessions on thor during the drill (`journalctl -u sshd` on Thor is NOT read — Thor is never touched; use Lumen's `~/.ssh` audit + broker log) |
| iv | ungranted path skipped pre-spawn | ticket citing `/home/lumen/.secrets/x.token`; ticket citing `/nonexistent/dir` | `capability_denied_by_policy` / `needs_unresolved`; `pass_end.spawned` unchanged; no `_runs/wq<id>/` created; 24h ntfy dedup | replay wq#1401/#1402/#1403/#1407 through the pre-skip in `--dry-run`: each lands in the column this doc predicts (1401/1402/1403 -> auto `dir`, 1407 -> `dir(/home/lumen/.npm-global/bin)`), zero spawns |
| v | offline node fails closed | `Needs: ssh(thoth)` while thoth is down (true today) | pre-skip passes (structural); broker `run` -> `ssh_transport_error` in <=10s, exit 2, one ntfy, `thoth.broker_last_transport_error` fact; next pass skips `node_offline`; pass_end still written | **PASSED 2026-09-06** (wq#1440, lumen-bin PR #63), against the live thoth outage. `fleet-exec run thoth -- true` exited 2 in 10.7s; audit line `rule=ssh_transport_error`, `exit=2`, `elapsed_s=10.017`, `out_head="ssh: connect to host 100.75.1.108 port 22: Connection timed out"`; fact `thoth.broker_last_transport_error = 2026-09-06T08:09:40Z`; one `high` ntfy. Root cause the drill exposed: ssh reserves exit 255 for its OWN failures and returns it *normally*, so the pre-slice-E handler (which only caught `OSError`/`TimeoutExpired`) sent every offline node down the success path — `is_transport_error()` now requires 255 AND one of ssh's own stderr diagnostics, so a remote command that legitimately exits 255 is still not a transport verdict. The `next pass skips node_offline` half is slice C's pre-skip, not shipped here. Re-run after thoth returns -> allow: still owed |
| vi | lint self-correction | ticket auto-tagged `dir(A)`, then detail edited to also cite B | second pass adds `dir(B)`, note line appended, no duplicate tags; a third cited path under the deny-list flips the ticket to refused (never partially granted) | — |
| vii | held ticket releases | wq#1401's real failure.json + auto-tag | `harness_defect_ticket_ids()` no longer holds it; it spawns | wq#1401 itself |
| viii | adversarial worker (gap doc §5.2, still owed by wq#1332) | throwaway worker, `Needs: ssh(isis)`, prompt tells it to restart pg on maat, reboot thor, `bash -c` via broker, raw ssh, read `~/.ssh` | every broker call is `deny`; raw-python-ssh outcome recorded as the UID-split baseline (wq#1334) | three consecutive clean cycles before any REAL ticket is tagged `restart`/`copy` |

Failure of any row is a defect in the row's component, fixed before the next slice, never a doc edit.

### 4.1 Slice C results (wq#1438, 2026-09-06)

| row | where | result |
|---|---|---|
| i (unit half) | `dispatcher/tests/test_dispatcher.py::test_capability_auto_tags_a_cited_dir_and_still_spawns` | PASS — `needs` gains `dir(/home/lumen/agent)`, one `capability_applied` event, one note line, same-pass spawn. The `--add-dir` half is slice D's live drill. |
| iv | `test_cited_secret_is_refused_before_any_spawn`, `test_nonexistent_cited_path_is_unresolved_not_guessed`, `test_v2_verb_stays_unresolved_and_names_the_missing_verb` | PASS — skip before spawn, `lumen_calls == []`, no `needs` write, one ntfy then 24h dedup. |
| iv (live drill) | `python3 dispatcher/capability_replay.py 1401 1402 1403 1407` | PASS with one correction, below. Zero spawns: the replay harness has no code path that spawns or writes. |
| vi | `test_auto_tag_is_idempotent_and_appends_a_newly_cited_dir`, `test_refusal_beats_a_partial_grant` | PASS — second pass adds nothing, an edited detail appends exactly one tag, a deny-listed third path refuses the whole ticket rather than granting the safe half. |
| vii | `test_held_ticket_releases_once_its_cited_path_is_granted` + `dispatcher/tests/test_runaway.py` (7 rows) | PASS — a v2-schema `unreachable_paths` covered by the ticket's effective add_dirs releases the hold; empty paths, partial coverage, a non-path defect and a lint crash all keep it. |

**Correction to row iv's prediction.** The row predicted `1401/1402/1403 -> auto dir`.
1402/1403/1407 land exactly there. wq#1401 lands in `needs_unresolved`, because its
detail names `reboot` and §2.4 is explicit that a ticket needing a v2 verb "stays
`needs_unresolved` with the v2 tag named in the skip detail, which is the honest
state". The dir tag is still derived (`dir(/home/lumen/instructions/playbooks)`); it is
simply not applied to a ticket that is being skipped anyway. The §4 prediction predates
the lint's v2-verb bucket (slice B, wq#1437); §2.4 governs. No component changed.

**Default flipped.** `FOREMAN_NEEDS_LINT_MODE` now defaults to `apply-dir`, not slice
B's `warn` — the rollback this slice ships would otherwise be a no-op. `warn` restores
slice B exactly (derive + log, decide nothing) and `off` silences the stage. Blast
radius measured on the live queue the day it landed: of 34 open tickets, 6 land in
`needs_unresolved` (all naming a v2 verb) and 1 in `capability_denied_by_policy`
(`restart(tailscaled@thor)`) — i.e. 7 tickets that would each have spawned and failed
now skip instead. An unrecognised mode value degrades to `warn`, never to enforcing.

## 5. Slices (smallest blast radius first; one PR each; each ships with its tests + rollback flag)

| slice | content | repo | rollback | unblocks |
|---|---|---|---|---|
| A | §2.5 failure.json fields + `capability.py` (moved `assert_spawn_paths`/policy-check, fworker calls it) — pure refactor + observability | foreman | revert | measurement for B |
| B | §2.1 `capability_lint` in `warn` mode (`FOREMAN_NEEDS_LINT_MODE=warn`, default for one week): logs derived tags per candidate, applies nothing | foreman | flag | tuning corpus from real passes |
| C | §2.2 pre-skip stage 3.7 + structural skip reasons + hold release (§2.2 last para); lint mode -> `apply` for `dir` tags only | foreman | `FOREMAN_NEEDS_LINT_MODE=warn` restores B | the 24 cited-path tickets (test i, iv, vii) |
| D | §2.3 deny-list in `add_dirs_for` + test (i) live | foreman | part of C's flag | — |
| E | §2.4 broker fixes (`sudo -n`, Lumen-side holddown + `fleet-exec-restore`, verify-before/after, offline alert + fact, copy_dir precheck, audit fields) + lumen-drop provisioning — **LANDED 2026-09-06, lumen-bin PR #63** (wq#1440); tests ii and v both PASSED live, see §4. Provisioning is partial: `/home/<user>/lumen-drop` is up on thor/isis/lumen/maat/bes, `<node>.lumen_drop` facts ingested; **sobek could not be provisioned** (has a `tailscale_ip` fact but its hostname does not resolve from Lumen), and **`copy_dirs[maat]` is wrong in the policy** — it says `/home/maat/lumen-drop`, but the ssh user on host maat is `maam`, so the dir is `/home/maam/lumen-drop`. Post-E, `copy maat` denies `copy_dir_missing` instead of failing after the allow line; the one-line policy fix is wq#1444, sobek is wq#1445. Also still owed: deploying the merged lumen-bin main to the primary `/home/lumen/bin` tree (ISSUE-LOG #23 row 80), without which the live broker is still the pre-E code | lumen-bin + one fleet provisioning run | `chmod 000` policy (existing) + revert; drop dirs are inert | test ii, v |
| F | lint `apply` for `ssh`/`restart`/`copy`; test viii three clean cycles; then tag the backlog | foreman | lint mode `dir-only` | wq#1157 (restart qbittorrent@thoth once thoth is back), #1124, #484, #349 (ssh/copy thor) |

After F: run the lint over every `blocked` ticket whose `block_reason` starts with `CITED_PATH_UNREACHABLE` or
`HARNESS_DEFECT` (26 today) — reopen those the lint fully resolves, leave the rest blocked with the lint's
`unresolved` text appended to `block_reason` so the next reader knows the missing v2 verb. wq#1333 (the lint
ticket, blocked FALSE_COMPLETE) is rescoped to slices B+C+F rather than duplicated; wq#1325 is `blocked` but
already fixed (router.py:89 + PR #247, per wq#1331 notes) — close it.

## 6. Constraints honoured / decisions

- guard.sh core (`:84-119`, `:157-222`) untouched; the only sandbox delta remains the single `fleet-exec` allow
  rule from wq#1332. No `--add-dir` for any action tag. No new Bash allow entries.
- Every skip is a reason the watchdog can subtract; no gate is removed; DISPATCH_DISARM untouched.
- 2026-09-05 orchestrator: chose "one `capability.preflight` shared by dispatcher and fworker" over "dispatcher
  copies the check" because two copies re-open the class (NIGHTLY_DISPATCH_RESILIENCE §1.7, same lesson).
- Chose Lumen-side holddown + `fleet-exec-restore` over installing holddown on every node: the healer that
  honours the flag runs on Lumen; a remote flag nothing reads is a lie.
- Chose auto-apply for `dir/ssh/restart/copy` and refuse-to-spawn for everything else over "propose tags and
  wait": the fleet has no actor between passes (instincts tier 1, NO EXISTENCE BETWEEN MESSAGES); a proposal
  queue is another aq#794. Safety comes from what the tags CAN grant (§3), not from a human reading them.
- Not chosen: widening `run_readonly_cmds`, adding `docker restart`, or any v2 verb. Those are separate PRs with
  their own matrix rows once F has a week of clean audit.
