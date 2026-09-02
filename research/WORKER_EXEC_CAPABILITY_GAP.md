# Worker execution-capability gap — design

Status: DESIGN, review before code. Author: orchestrator (fable-5@high), 2026-09-02.
Evidence tree: /home/lumen/foreman @ b4819b7, /home/lumen/kb/ISSUE-LOG.md (144 rows),
agents/work-queue/queue.db (42 blocked, 5 frank_gated at time of writing).
Build tickets: wq#1330 (slice 1 failure.json), wq#1331 (slice 2 needs:repo/dir +
worktree assertion), wq#1332 (slice 3 fleet-exec v1), wq#1333 (slice 4 lint),
wq#1335 (C4 carve-out), wq#1334 (UID-split research). PR #237.

Every claim carries a file:line from the live tree or a wq/ISSUE-LOG row. Where a claim
is by inspection rather than by a live reproduction, it says so.

## 0. Summary

Workers are `claude -p` children with a static Bash allowlist and a PreToolUse guard.
That layer was designed as a wall against accidents inside a build worktree; it was
never a fleet execution model. So every ticket whose real work lives on a fleet node,
in a service, or in a repo other than the lane's default dies inside the sandbox with a
"This command requires approval" loop, and the failure record says BLOCKED plus a
transcript path. Frank then becomes the executor.

Decision: **hybrid — capability tags on tickets (routing) + one narrow, audited broker
(`fleet-exec`) that is the ONLY new verb any worker ever gets.** No sandbox is
loosened. Untagged tickets run exactly as today. Tagged tickets run under the same pack
plus one allow rule (`Bash(/home/lumen/bin/fleet-exec *)`), and the broker enforces a
per-ticket grant that the dispatcher derived from the tags and wrote into the run_dir.
Crown-jewel rules live in the broker's static policy and cannot be granted by any tag.

A second finding reframes the problem (§1.7): the current sandbox is a same-UID prefix
filter, not a security boundary against a worker that wants out. The broker therefore
buys audit + declared intent + accident-proofing, not adversarial containment.
Adversarial containment needs a UID split — scoped as a follow-up (§5.4), not a blocker.

## 1. Class definition — what the sandbox structurally denies

Three layers, all inside the worker's own process and UID:
- `harness/worker-settings.json:4-99` allow prefixes (89 entries — the prompt's "~111"
  is stale); `:100-128` deny prefixes. A non-matching Bash call gets "This command
  requires approval" from Claude Code itself (`harness/action_gate.py:519-533`).
- `harness/guard.sh` PreToolUse hook (wired `worker-settings.json:131-149`):
  privilege/power verbs `:84-88`, docker `:117-119`, rm -rf scope `:120-142`,
  primary-tree HEAD/commit freeze `:157-222`, credential paths `:274-280`.
- `--add-dir` roots: static list `harness/agents.yaml:36-43`, applied
  `harness/fworker.py:934-935`; outside them Claude Code answers "blocked. For
  security" (`action_gate.py:521-522`). Inside them: READ context only (`fworker.py:895-897`).

Per-repo profiles (`harness/permission_profiles.py`, `permission-profiles/*.json`,
`repos.d/{foreman,mailbot,pokemon}.json`) only change toolchain verbs. None adds
remote, root, restart, or repo-scope verbs (grep of `permission-profiles/**/*.json` for
`ssh|tailscale|scp|rsync|adb|systemctl restart`: zero matches, 2026-09-02).

| # | Capability | Denied at (file:line) | Live citation |
|---|---|---|---|
| C1 | Remote shell to a named fleet node (ssh/scp/rsync/tailscale/ping) | no `Bash(ssh *)`/scp/rsync/tailscale entry in `worker-settings.json:10-98`; `base.json` same | ISSUE-LOG #71 (wq#916 `ssh -o ConnectTimeout=5 100.89.147.95 true` -> requires approval), #114 (wq#349 ssh to Thor), #32 (wq#354); wq#484 block_reason PERMISSION_LOOP |
| C2 | Scoped root on a named node or on Lumen | `worker-settings.json:116` `Bash(sudo *)`; `guard.sh:84-85` denies the token anywhere in the string incl. prose (ISSUE-LOG #33 row 41: fired on a grep containing it) | ISSUE-LOG #68 (wq#915 cloudflared config + unit unreachable), #33 row 41 (wq#362) |
| C3 | Service restart/reload | allow is status/is-active/is-enabled/show/list-*/cat only (`worker-settings.json:51-56`); no restart/reload/start; `guard.sh:87-88` denies stop/disable/mask | wq#1124; wq#1295 PERMISSION_LOOP (LiteLLM health checks); wq#1157 (qbittorrent on thoth needs a restart it cannot issue) |
| C4 | HEAD-moving or commit-creating git verb on a granted repo, incl. path-scoped `checkout -- <path>` / `restore <path>` | `guard.sh:191` verb set has no path-scoped carve-out; fires when cwd or `-C` targets a PRIMARY (`:157`, `:210-221`) | ISSUE-LOG #38 (lumen-pr's own land-refusal prescribes the blocked remedy), #125 (wq#1155 cannot commit in the real nest repo), #71 (prose false positive) |
| C5 | Access to the repo/dir the ticket actually targets | worktree root = `router.repo_for_ticket()` (`orchestrator/router.py:889-909`) via `LANE_REPO` (`:77-98`); `--add-dir` static and read-only (`agents.yaml:36-43`, `fworker.py:895-897`) | ISSUE-LOG #33 (wq#678), #48 (wq#1152 x2), #56 (wq#683/#684), #113 (wq#684 a3), #125 (wq#1155: `LANE_REPO["nest"]` one dir too shallow, wq#1325 open) |
| C6 | Deliberate exception via env prefix (`VAR=v cmd`, `env VAR=v cmd`) | no allow entry matches the `VAR=`/`env` prefix shape; `Bash(env)`/`env |*` denied `:118-119` | ISSUE-LOG #38, #40. `LUMEN_PR_ALLOW_DIRTY` itself is RETIRED (`/home/lumen/bin/lumen-pr:31,451`) — instance gone, class not |
| C7 | Bench/device tools on a bench host (adb, fastboot, serial) | not allowlisted; device is USB-attached to Thor, not Lumen | ISSUE-LOG #67 (wq#679 `adb devices`), wq#684 |
| C8 | Power/reboot of a NON-Thor node | `worker-settings.json:101-104`; `guard.sh:84-85` | ISSUE-LOG #32 (wq#354 Mac burn-in), #33 row 41 (wq#362) |

Correctly Frank, not a capability gap: physical acts (wq#941 M70s, wq#1016 Thor fan,
wq#120/#266/#364 hapi blades, wq#402/#665 watch8 radio), spend (wq#124, wq#894/#895),
credential minting on Frank's own accounts (wq#915 CF Access, wq#1210 D1 token), and
dead hardware (wq#916 apis, ISSUE-LOG #71).

### 1.7 The sandbox is a same-UID prefix filter, not a boundary (by inspection)

`fworker.build_env()` sets `HOME=/home/lumen` (`fworker.py:863`) and spawns `claude -p`
as the user that owns `~/.ssh/id_ed25519`, `~/.ssh/fleet_config` and `queue.db`.
`Bash(python3 *)` is allowed (`worker-settings.json:11`). guard.sh has no rule on
`subprocess`/`os.system`. A `python3 -c` that shells out to ssh therefore passes every
layer — the allow list filters the literal first token only. This is not something to
close with more regexes (the file's own history is the whack-a-mole: wq#1285, wq#1316,
ISSUE-LOG #71 false positive). It IS the reason a worker that needs a capability must be
handed a legitimate, logged path, and the reason adversarial containment is a separate
problem (§5.4). Not reproduced live — no throwaway worker was spawned for this doc.

## 2. Routing vs granting — decision

### 2.1 What "orchestrator-class runner" would actually be

There is no headless orchestrator-class runner today. The orchestrator is Claude in a
chat turn via Commander MCP; it has no existence between messages (instincts tier 1).
The only headless executor the fleet owns is `claude -p` under fworker. So pure
option (a) reduces to "a second fworker lane with a wider static pack" — e.g.
`worker-settings-general.json`, which exists but is NOT consumed by fworker
(`harness/README.md:29`, `tests/test_settings_lint.py:82`). A wider static pack is
still granting; it just grants by lane instead of by ticket, and `Bash(ssh *)` in any
static pack is unscoped fleet-wide RCE with lumen's keys (§1.7 makes it no worse
than today in adversarial terms, but it is far worse in accident terms: a worker
"verifying" on maat with a sudo one-liner is exactly the crown-jewel incident the
rules exist to prevent).

Pure option (b) without routing fails the other way: the broker can only enforce a
grant it knows about, and today nothing on a ticket says what it needs. A broker fed
"anything the worker asks for" is `ssh *` with a log line.

### 2.2 Chosen: hybrid

1. **Tags declare need.** A ticket carries zero or more `needs:` tags. Zero tags =>
   BUILD lane, unchanged sandbox, unchanged pack. One or more => the SAME lane and
   pack plus `Bash(/home/lumen/bin/fleet-exec *)`, and a grant file.
2. **Grant is derived at dispatch, not by the worker.** `foreman_dispatcher._do_pass`
   (stage 3, `dispatcher/foreman_dispatcher.py:676-717`) resolves tags -> grant JSON,
   validates against the broker's static policy, writes it 0400 to
   `_runs/wq<id>/grant.json`, and passes `FOREMAN_GRANT_FILE` in `build_env()`
   (`fworker.py:846`). A tag the static policy forbids is a dispatch REFUSAL
   (`log.skip(... "capability_denied_by_policy")`), never a silent downgrade.
3. **Broker enforces.** `fleet-exec` reads the grant, matches `<node> <verb> <args>`
   against it AND the static policy, executes exactly one allowlisted verb through
   the orchestrator's existing ssh config (`~/.ssh/config`, `~/.ssh/fleet_config`), logs
   one audit line before and one after, fails closed on any doubt.
4. **Sandbox untouched.** No new entries in worker-settings.json beyond the one
   fleet-exec prefix; guard.sh core rules (`:84-119`, `:157-222`) unchanged. The only
   guard.sh change in scope is C4's path-scoped carve-out (§3.3), which does not
   touch MAC/Thor/crown-jewel.

### 2.3 Tag vocabulary (v1, closed set — the lint rejects anything else)

```
needs:ssh(<node>)              read/verify shell on node (verbs: run <allowlisted-cmd>)
needs:root(<node>)             scoped sudo on node (verbs: sudo <allowlisted-cmd>)
needs:restart(<unit>@<node>)   systemctl restart|reload|start of ONE named unit
needs:copy(<node>:<dir>)       scp/rsync INTO one directory on node (never from)
needs:repo(<abs-path>)         worktree of this repo instead of LANE_REPO; landable
needs:dir(<abs-path>)          extra --add-dir (read context), no landing
needs:bench(<node>,<tool>)     adb|fastboot|serial on the named bench host
needs:power(<node>)            reboot/poweroff of a NON-Thor, non-crown-jewel node
```
`<node>` must be a subject in `lumen_state.py current` with a `tailscale_ip` fact and
an entry in `~/.ssh/config` or `~/.ssh/fleet_config`; anything else is lint-fatal.

Where the tags come from, in order:
- explicit `Needs:` line in ticket detail (`Needs: ssh(isis), restart(ollama@isis)`) —
  authored by whoever files the ticket (orchestrator, judgment.py, repo_sweep);
- `capability_lint` (new, `harness/capability_lint.py`): scans title+detail for
  node names, unit names, `ssh `, `scp `, `sudo `, `systemctl restart`, repo paths not
  under LANE_REPO[lane], and proposes tags; the dispatcher REFUSES to spawn a ticket
  whose lint proposal is non-empty but whose `Needs:` line is absent — the same
  "refuse rather than guess" doctrine `router.repo_mismatch()` already applies
  (`router.py:870-886`). The orchestrator (or `wq edit --needs`) resolves it;
- automatic back-fill from a CAPABILITY_DENIED failure record (§4) — one retry.

Stored on the ticket as a new `needs` column (migration, same pattern as
`wq1158_lease_fence.py`), shown by `wq show`, editable by `wq edit --needs`.

### 2.4 Broker: `fleet-exec` (`/home/lumen/bin/fleet-exec`, lumen-bin repo)

CLI (exactly one verb per invocation; no shell, argv only, `shlex`-parsed by the
broker, never joined into a string):
```
fleet-exec run     <node> -- <cmd> [args...]
fleet-exec sudo    <node> -- <cmd> [args...]
fleet-exec restart <node> <unit>          # also: reload | start
fleet-exec copy    <node> <local-path> <remote-dir>
fleet-exec bench   <node> <tool> [args...]
fleet-exec power   <node> reboot|poweroff
fleet-exec grant                          # print this worker's effective grant
```
Static policy `/home/lumen/foreman/harness/fleet-exec-policy.yaml` (in the foreman
repo, lands only by PR):
```
never:                        # not grantable by any tag, ever
  nodes: [thor]               # THOR IS THE ONLY HARD RULE — no verb at all except run:<readonly>
  power: [thor, lumen, maat, sobek, bes]      # crown-jewel + primary/standby pg
  restart:
    lumen: [vaultwarden*, cloudflared, desktop-commander-mcp, postgresql*, tailscaled, sshd]
    maat:  ['*']              # crown-jewel: detached-systemd + Frank-present only
    sobek: [postgresql*, cloudflared]
  sudo_argv_deny: [ip, macchanger, dd, mkfs*, wipefs, visudo, passwd, useradd, chpasswd, tailscale up*, systemctl mask*]
run_readonly_cmds: [true, uptime, hostname, uname, cat, ls, stat, df, free, systemctl status|is-active|is-enabled|show|cat|list-*, journalctl, ss, ip addr, tailscale status, docker ps, docker logs, python3 -c <readonly-probe>]
sudo_cmds:        [systemctl restart|reload|start <unit>, install -m0644 <src> <dst-under:/etc/<pkg>/>, apt-get install -y <pkg>, tee <dst-under:/etc/systemd/system/>]
copy_dirs:        {thor: [/home/thor/lumen-drop], isis: [/home/isis/lumen-drop], ...}
bench_tools:      {thor: [adb, fastboot]}
```
Grant file (`_runs/wq<id>/grant.json`, written by the dispatcher, hash recorded in
`attempt-N.json`):
```
{"ticket":349,"attempt":2,"expires":"2026-09-03T02:00:00Z","sha256":"...",
 "grants":[{"kind":"ssh","node":"thor","verbs":["run"]},
           {"kind":"copy","node":"thor","dir":"/home/thor/lumen-drop"},
           {"kind":"root","node":"thor","cmds":["apt-get install -y python3-serial"]}]}
```
Enforcement order in the broker, all fail-closed (`exit 2`, reason on stderr, audit
line written FIRST): (1) grant file present, readable, sha256 matches the value fworker
put in `FOREMAN_GRANT_SHA` env, not expired, ticket matches `WQ_AGENT`'s claim; (2)
node+verb present in grant; (3) node+verb+argv permitted by static policy `never`
and per-verb allowlists (argv[0] must be an exact allowlisted basename; no `-c`, no
`bash`, no `sh`, no `eval`, no `|`/`;`/`&&` tokens, no `$(`); (4) for `restart`:
`holddown` registration (`/home/lumen/bin/holddown`) with `--restore-by` so a
stop-restore contract exists before the unit is touched; (5) execute via
`ssh -o BatchMode=yes -o ConnectTimeout=10 <alias> -- <argv...>` with argv passed
through `shlex.quote` per element; (6) audit line after with exit code, duration,
stdout/stderr sha256 + first 240 bytes.

Audit record (`/home/lumen/foreman/events/fleet-exec.jsonl`, append-only, plus a
copy into the worker's own `events/<worker>.jsonl` so guard.sh's stream and the
broker's stream can be joined):
```
{"ts":..,"ticket":349,"attempt":2,"worker":"wq349-a2","node":"thor","verb":"run",
 "argv":["pip","install","--user","pyserial"],"decision":"allow|deny","rule":"grant:ssh(thor)|policy:never.nodes",
 "exit":0,"elapsed_s":1.9,"out_sha256":"..","out_head":".."}
```

Why this is not arbitrary RCE: the worker never hands the broker a string; it hands
an argv whose first element must equal an allowlisted basename and whose remaining
elements are checked for shell metacharacters and for path prefixes where the verb
declares them. `run` on a node is a read-only probe set; anything mutating goes
through `sudo`/`restart`/`copy`, each with its own narrower allowlist. The broker has
no `--` escape into `bash -c`. A worker that wants more than the grant still has the
§1.7 escape — which is why the audit line is written BEFORE execution and why §5.4
exists; the broker is the honest path, and the honest path is the one the dispatcher
can reason about.

Reconciliation with hard rules: MAC spoof — `ip`/`macchanger` in `sudo_argv_deny`,
and guard.sh `:96-101` still fires on the worker side (fleet-exec is never a way around
guard.sh; the hook runs on the `fleet-exec` Bash call too). Thor — `never.nodes:
[thor]` for every verb except read-only `run`, `copy` into `lumen-drop`, and `bench`
(the ticket class that needs Thor is "deploy PoC + adb" — wq#349, wq#679); restart/
power/sudo on Thor stays ungrantable. Crown-jewel (Ma'at/Lumen) — `never.power`
and `never.restart` above; lumen `restart` is limited to units NOT in the never list
(so `ollama`, `litellm` container health probes, `lumen-*` timers are restartable; vault
/cloudflared/commander/pg are not). Detached-systemd + Frank-present for crown-jewel
changes is unchanged because the broker cannot be granted those units at all.

### 2.5 Tradeoffs

| | (a) routing to wider static lane | (b) broker only | hybrid (chosen) |
|---|---|---|---|
| Blast radius of a mistake | whole fleet, whole pack | per-verb, but unscoped without tags | per-ticket, per-node, per-verb |
| Audit | guard.sh events only, no remote record | full | full |
| Changes to sandbox | new pack (large) | 1 allow rule | 1 allow rule |
| Worker has to know its grant | no | `fleet-exec grant` | `fleet-exec grant` + prompt line |
| Handles C5 repo mis-map | no | no | yes (needs:repo/dir -> worktree + add-dir) |
| Handles C6 env-prefix | no | no | no — fixed by rule instead (§3.4) |
| Unblocks stuck set | most, unsafely | few (no tags) | see §2.6 |

### 2.6 Stuck set — what each architecture would unblock

Non-physical tickets currently blocked on a capability (from `queue.db` block_reason
+ ISSUE-LOG, 2026-09-02):

| wq | needs | (a) wide lane | (b) broker only | hybrid | still Frank? |
|---|---|---|---|---|---|
| 349 Miko UART PoC deploy to Thor | ssh(thor), copy(thor), root(thor: pip) | yes (unsafely) | no tags | **yes** | wiring Miko at 1.8V (physical) stays a `[physical]` task, not a gate |
| 484 lpp-mp M5d cross-node link | ssh(thor) run + copy | yes | no | **yes** | no |
| 679 questfree DTB dump | bench(thor,adb) | yes | no | **yes** (adb via bench verb) | USB pairing on the fragile unit — physical |
| 684 questfree fastboot harness | repo(questfree) [lane now fixed], bench(thor,fastboot) | partial | no | **yes** | device in hand for boot test |
| 683 questfree DTS port | repo(questfree) + dir(kernel tree) | partial | no | **yes** | no |
| 1152 nest headless flasher | dir(/home/lumen/projects/nest/nle-flasher) | no | no | **yes** | no |
| 1155 nest build.sh | repo(NoLongerEvil-Thermostat) — wq#1325 | no | no | **yes** | no |
| 1157 qbit ORIGIN_DOWN on thoth | restart(qbittorrent@thoth) | yes | with tag | **yes** | no |
| 1124 one-line service restart | restart(<unit>@lumen) | yes | with tag | **yes** | no |
| 1295 LiteLLM health checks | restart(litellm container?) — docker restart is NOT a broker verb v1 | partial | no | **partial**: config lands by PR; the container restart is an orchestrator/detached step until a `docker restart <name>` verb is added (v2) | no |
| 1278 / 1293 / 627 PERMISSION_LOOP | unknown — no failure record (§4) | ? | ? | after §4 lands, auto-tag + one retry | tbd |
| 354 Mac burn-in cycles | ssh(isis), root(isis), power(isis) [non-crown-jewel] | yes | with tag | **yes** | no — smart-plug cycling is via HA API (curl already allowed) |
| 362 hathor bench root-cause | power + physical console | yes | no | no | yes — physical console, quarantined node |
| 325 vault HA drill | root(maat), restart(pg@maat), destructive release on wadjet | yes (catastrophic) | no | **no by design** (`never.restart.maat`) | yes — crown-jewel drill, Frank-present |
| 915 memory-mcp CF Access | CF dashboard | no | no | no | yes — credential on Frank's account |
| 916 apis SSH dead | hardware dead | no | no | no | yes (wq#550) |
| 940 / 927 bin primary CONTENT_DIVERGED | HEAD move on PRIMARY | no | no | **no** — stays `sync-live` (orchestrator), by rule wq#900 | orchestrator, not Frank |
| 600 / 913 / 919 HANG, 869/879/983/1122 CRASH | unknown — no failure record | ? | ? | after §4: classify first | tbd |

Net: hybrid unblocks 10 outright (349, 484, 679, 683, 684, 1152, 1155, 1157, 1124,
354), 1 partially (1295), and correctly leaves 325/362/915/916/940/927 where they are.
The PERMISSION_LOOP/HANG/CRASH rows are the §4 argument: today nobody can say which
column they belong in without opening a transcript by hand.

## 3. Repo/dir correctness (C5, C4, C6)

### 3.1 Root cause chain (already partially fixed, still leaking)

- `router.LANE_REPO` (`orchestrator/router.py:77-98`) maps lane -> one repo. A ticket
  whose lane is `build` but whose work is in questfree/nest got `FOREMAN_ROOT`
  (ISSUE-LOG #33, #56, #113). wq#433 added `TITLE_REPO_MARKERS` + `repo_mismatch()`
  (`router.py:837-886`) and the dispatcher now skips a mismatch loudly
  (`foreman_dispatcher.py:701-705`). ISSUE-LOG #125 says `grep -rn TITLE_REPO_MARKERS`
  found nothing — that was the worker's stale worktree; the primary at b4819b7 has it.
- Residual defects: (i) `LANE_REPO["nest"]` points one directory too shallow
  (`router.py:88`, wq#1325 open); (ii) the marker table is a second hand-maintained
  copy of LANE_REPO's paths (`router.py:837-846`), so the next lane added without a
  marker re-opens the class; (iii) even a correct worktree cannot reach a sibling dir
  the ticket cites (wq#1152 nle-flasher) because `--add-dir` is static
  (`agents.yaml:36-43`); (iv) nothing at spawn time asserts that the worktree's
  `origin` is the repo the ticket names.

### 3.2 Change

1. `needs:repo(<path>)` and `needs:dir(<path>)` (§2.3) become the per-ticket override:
   `repo_for_ticket()` returns `needs.repo` first (before verify-hint, before LANE_REPO),
   validated against `repo_sweep.managed_repos()` exactly as `verify_repo_hint` is
   (`router.py:813-825`). `add_dirs_for()` (`fworker.py:905-908`) unions `needs.dir`.
   The `TITLE_REPO_MARKERS` table is derived from `LANE_REPO` at import
   (`{lane: path}` inverted, plus the two LPP aliases) so it cannot drift.
2. Fix `LANE_REPO["nest"]` per wq#1325 and add `/home/lumen/projects/nest/NoLongerEvil-Thermostat`
   to `repo_sweep.managed_repos()`; nest also needs a landing path (it is a GitHub fork
   nobody pushes to — ISSUE-LOG #125) — the ticket for that is wq#1325's, not this doc's.
3. **Spawn-time preflight assertion (ties to wq#1288):** in `make_worktree()`
   (`fworker.py:756`) after `git worktree add` succeeds and before `claude -p` is
   spawned: `git -C <worktree> remote get-url origin` must resolve to the same
   repository as `git -C <expected_repo> remote get-url origin` (compare normalized
   URLs, then compare `git rev-parse --git-common-dir` realpaths). Mismatch =>
   `LAUNCH_FAILURE` with reason `WORKTREE_REPO_MISMATCH expected=<repo> got=<url>`,
   attempt NOT counted against the ticket (it is a harness defect, same bucket as
   PERMISSION_LOOP `fworker.py:310`), ticket left `open` with a `notes` line, ntfy to
   lumen-fleet. Also assert every `needs:dir` path exists and is a directory, and that
   every path literal the ticket detail cites under `/home/lumen/` is inside
   worktree ∪ add_dirs — a cited path outside the reachable set is the exact
   wq#1152 signature and is refused before a single token is spent.
4. `harness-preflight <repo>` (`harness/harness_preflight.py`, dispatch gate at
   stage 10) gains a `needs`-aware static lint: for each tag, the corresponding
   `fleet-exec` shape is added to the preflight command set so the stamp proves the
   broker call is allowlisted under the rendered pack.

### 3.3 C4 — path-scoped git verbs on primaries

guard.sh `:191` denies `checkout|restore` regardless of shape. Carve out ONLY:
`git [-C <primary>] checkout -- <path>...` and `git [-C <primary>] restore [--staged]
<path>...` where every `<path>` is a relative path with no `..` and no leading `-`,
and the segment contains no `-b`, `-B`, `--orphan`, `--detach`. These discard
working-tree changes to named files and cannot move HEAD or a branch. Test cases in
`harness/tests/test_guard.py` must include the ISSUE-LOG #38 exact strings and the
negative forms (`checkout -- .`, `checkout main -- f`, `restore --source=HEAD~1 f`
=> still denied). The prose false positive (#71) is out of scope here — it is the
segmenter matching git-verb text in a heredoc; fixing it means only scanning segments
whose first token is `git`, which is its own small ticket.

### 3.4 C6 — env-prefix exceptions

Rule, not allowlist: **a deliberate exception is a flag on the tool, never an env
prefix.** `LUMEN_PR_ALLOW_DIRTY` is already retired (`lumen-pr:31`). For test-time
overrides the `python3 -c "os.environ[...]"` pattern (ISSUE-LOG #40) is already the
documented habit; add it to `harness/prompts` worker guidance so no worker rediscovers
it. No `Bash(env *)` or `Bash(*=* *)` entry is added — both would be prefix-unscoped.

## 4. Visibility — the failure record

Today `handle_failure()` (`fworker.py:2037-2049`) writes `block_reason =
"<BLOCK_CLASS>: <classification> after N attempts; transcript=<glob>"` and pages ntfy.
The class set (`fworker.py:303-334`) is INFRA_CRASH / LIMIT_HIT / WORK_BLOCKED /
HARNESS_DEFECT, and only PERMISSION_LOOP maps to HARNESS_DEFECT. A PERMISSION_LOOP that
needed `ssh` and one that needed `/home/lumen/projects/nest` look identical on the
ticket; a HANG on a network call to a node the worker cannot reach looks like a model
stall (wq#600/#913/#919). The `LoopTracker.report()` text (`action_gate.py:634-643`)
already knows the denied calls but it goes into `prior_attempt_notes`, not the ticket.

### 4.1 Minimum machine-readable record — `_runs/wq<id>/failure.json`

Written by `handle_failure()` on every terminal failure (and by fire-mode's `_record`,
`fworker.py:1285`), one object, schema v1:
```
{"schema":1,"ticket":349,"attempts":2,"classification":"PERMISSION_LOOP",
 "block_class":"HARNESS_DEFECT",
 "triage":"CAPABILITY_DENIED|HARNESS_DEFECT|GENUINE_BUG|REAL_BLOCK|LIMIT|UNKNOWN",
 "denied_calls":[{"tool":"Bash","argv0":"ssh","target":"100.112.151.100",
                  "refusal":"requires approval","count":3}],
 "inferred_needs":["ssh(thor)"],
 "worktree":{"path":"...","origin":"...","expected_repo":"...","match":true},
 "unreachable_paths":["/home/lumen/projects/nest/nle-flasher"],
 "last_error_sig":"...","transcript":"~/.claude/projects/*/<sid>.jsonl",
 "attempt_files":["attempt-1.json","attempt-2.json"]}
```
`triage` is decided mechanically:
- `CAPABILITY_DENIED` — LoopTracker `trip_kind == "PERMISSION"` (`action_gate.py:653`)
  OR any `denied_calls` entry whose argv0/path maps to a §2.3 tag OR any
  `unreachable_paths`. `inferred_needs` is filled from the map
  `{ssh,scp,rsync,tailscale,ping}->ssh(<node by IP/alias>)`, `sudo->root(<node>)`,
  `systemctl restart|reload|start <u>->restart(<u>@lumen)`, `adb|fastboot->bench(thor,<t>)`,
  `path outside reachable set->dir(<path>)`. Node resolution uses `lumen_state.py
  current` tailscale_ip/lan_ip facts, never a guess; unresolvable => tag omitted and
  `triage` stays CAPABILITY_DENIED with `inferred_needs: []`.
- `HARNESS_DEFECT` — WORKTREE_REPO_MISMATCH, settings render failure, hook crash.
- `GENUINE_BUG` — CRASH with a Python/Node traceback in the last tool result and no
  denial; HANG whose last tool call is a test/build command.
- `REAL_BLOCK` — the worker itself called `wq block` with a reason (the good path
  ISSUE-LOG #31/#32 took); `triage` copies the worker's reason verbatim.
- `LIMIT` — TURNS/BUDGET.
- `UNKNOWN` — nothing above matched; still recorded, still auto-triaged as "needs a
  human read", never silently.

### 4.2 Where it goes

- `wq block` reason becomes the first line `"<triage>: <one-line>"`, e.g.
  `CAPABILITY_DENIED: ssh(thor) refused 3x; see _runs/wq349/failure.json` — the JSON
  path is on the ticket, the glob is gone.
- `failure.json` sha256 into `attempt-N.json` and the Langfuse span metadata
  (`emit_attempt_span`, `fworker.py:364-394`) so the dashboard can group by `triage`.
- Dispatcher auto-triage (new stage, before runaway `foreman_dispatcher.py:719`): a
  ticket blocked `CAPABILITY_DENIED` with non-empty `inferred_needs` gets `needs`
  back-filled, `status=open`, `attempt` reset, and ONE automatic redispatch; a second
  CAPABILITY_DENIED on the same ticket is final and pages. `HARNESS_DEFECT` never
  counts against the circuit breaker (already the wq#1107 doctrine, `fworker.py:313-324`).
- `aq` gets one row per `UNKNOWN`/`HARNESS_DEFECT` (not per CAPABILITY_DENIED — those
  are self-healing by construction).

## 5. Rollout

### 5.1 Slice 1 (smallest, clears the most, least blast radius) — 1 PR each, in order
1. **failure.json + triage** (§4) — pure observability, no new capability. Ships first
   so slices 2-3 are measured, not believed. Rollback: revert; old block_reason format
   is a subset of the new first line.
2. **needs:repo / needs:dir + spawn-time worktree assertion** (§3.2) — clears 1152,
   1155 (with wq#1325), 683, 684; makes the wq#433 class impossible instead of
   detected. Rollback: `FOREMAN_NEEDS_DISABLE=1` env in the dispatcher unit makes the
   resolver ignore `needs` and the assertion log-only.
3. **fleet-exec v1 with verbs `run`, `copy`, `restart`** and policy file — clears
   349, 484, 1157, 1124, part of 1295. `sudo`, `bench`, `power` verbs land in v2 after
   v1 has a week of clean audit. Rollback: remove the single allow rule from
   worker-settings.json (rendered packs re-hash, harness-preflight stamps go stale and
   the dispatch gate refuses until re-proved — that is the designed behaviour, see
   `harness_preflight.py` header) and `chmod 000` the policy file (broker fails closed
   on unreadable policy).
4. **capability_lint + refuse-to-spawn** (§2.3) — after 1-3, so the lint has a corpus
   of real `failure.json`s to be tuned against instead of guesses.

### 5.2 Test to failure, per architecture piece, BEFORE fleetwide
- Broker: unit tests are the policy matrix (every `never` row, every metachar,
  `--`-less invocation, expired grant, tampered grant sha, grant for another ticket,
  node not in ssh config, unit glob edge cases). Then a live throwaway worker on a
  synthetic ticket tagged `needs:ssh(isis)` whose prompt is adversarial by design:
  told to restart postgres on maat, reboot thor, run `bash -c` via the broker, read
  `~/.ssh` and use raw ssh. Every one must appear as `deny` in `fleet-exec.jsonl` (or
  as a guard.sh block for the raw-ssh attempt — which, per §1.7, it will NOT be if the
  worker goes through python; record that outcome honestly as the §5.4 baseline).
  Run until three consecutive full cycles are clean.
- Worktree assertion: deliberately mis-lane a synthetic ticket (`lane=build`, title
  `[questfree] ...`, `needs:repo(/home/lumen/questfree)`) and confirm the dispatcher
  refuses, then fix the lane and confirm spawn; corrupt `origin` in a scratch worktree
  and confirm LAUNCH_FAILURE with attempt not charged.
- Triage: replay the last 30 days of `attempt-N.json` + transcripts through the
  classifier offline; every currently-blocked ticket in §2.6 must land in the column
  this doc predicts, or the classifier is wrong and the doc says so.
- Rollout gate: dispatch only tickets tagged `needs:*` for the first week, breaker
  budget untouched (HARNESS_DEFECT never trips it), ntfy on every broker `deny`.

### 5.3 Rollback for each (summary)
| piece | rollback | proof it worked |
|---|---|---|
| failure.json | revert PR | `wq block` reasons revert to old format, nothing else consumes the file yet |
| needs resolver + assertion | `FOREMAN_NEEDS_DISABLE=1` | dispatcher log shows `needs_ignored` per pass |
| fleet-exec | remove 1 allow rule + `chmod 000` policy | broker exits 2 on every call; preflight stamps stale => gate refuses tagged tickets |
| lint refuse-to-spawn | `FOREMAN_NEEDS_LINT_MODE=warn` | skip reason changes from `needs_unresolved` to a warning line |

### 5.4 Out of scope, ticketed separately
- **UID split**: run `claude -p` as a dedicated `fworker` user with its own HOME, no
  ssh keys, group-write on worktrees and `queue.db` only. Makes §1.7 false and turns
  the broker into a real boundary (peer-cred on a unix socket, broker runs as lumen).
  Large change (worktree ownership, wq db perms, run_dir, langfuse creds); needs its
  own design. Filed as research P1.
- guard.sh prose false positive (#71): only scan segments whose first token is `git`.
- `docker restart <name>` broker verb (1295) — v2.
- nest landing pipeline (wq#1325 follow-on).

## 6. Decision log
- 2026-09-02 orchestrator: hybrid chosen over (a) because a static wide lane is fleet-wide
  RCE by accident and over (b) because a broker without tags has nothing to enforce.
  Sandbox untouched except one allow rule and the C4 carve-out. §1.7 recorded as the
  honest limit of what this buys. Build ticket P0 filed; UID-split research P1 filed.
