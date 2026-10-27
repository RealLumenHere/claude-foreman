# DESIGN: orchestrator-verify lane — grants for [verify] passes and small live fixes that fworkers are walled out of

- Ticket: wq#4681 (DESIGN ONLY — no implementation in this PR)
- Author session: design-verify-lane-daa967, 2026-10-26 (PDT)
- Status: PROPOSED — review before any build ticket is filed
- Evidence base: foreman main f9b62afd (2026-10-27 04:17Z), live harness files read on Lumen the same session. Every file:line below was read, not copied from the ticket. Where the ticket text was stale it is marked **[ticket stale]**.

## 0. TL;DR

`[verify] wq#N` tickets are auto-filed for every shipped diff (judgment.file_verify_pass) into the static `build` lane with archetype `verify-pass`, whose grants are the *build-worker* grants: a foreman worktree, read-only add_dirs, no `ssh`, no `docker`, no `/home/lumen` root, no `sudo`. The probe a verify ticket is asked to run almost always lives *outside* that box (a unit on the box, a container, another node, a home-root doc, a non-foreman repo). So the ticket burns an attempt, classifies CAPABILITY_DENIED / PERMISSION_LOOP / KILLED, and parks until a human-driven orchestrator session — which has none of those walls — clears it by hand. 17 such tickets were cleared in one session on 2026-10-26.

Chosen approach: **C (A + B), built in two independent steps.** A = a `verify-probe` archetype/lane whose grant is *reach without mutation* (ssh BatchMode read-only run via fleet-exec, docker read-only via the existing MCP guard, `/home/lumen` + minerva read via a narrow home-root allowlist, pytest/node --test in any managed worktree, writes only to `reports/` and `wq note`). B = a separate `ops-apply` profile for the small live fixes, fail-closed behind lumen-change-safety precheck + backup + the verify/critic gate, never auto-dispatched. A alone drains the queue of verify tickets; B alone is dangerous without A's evidence. D (stop auto-filing [verify] for these archetypes) is rejected: it removes the signal instead of the wall.

## 1. Root cause + evidence grade

### 1.1 The denial surfaces, verified file:line

| # | Surface | Where (verified) | What it refuses | Grade |
|---|---------|------------------|-----------------|-------|
| S1 | Home-root deny | `harness/capability_lint.py:55` `DIR_DENY_EXACT = ("/home/lumen", "/")`; applied at `:547` (`_deny_reason`) and `:687` (home-root file → advisory, never granted) | any `needs:dir(/home/lumen)`, and any cited home-root file (ORCHESTRATOR-HANDOFF.md, HANDOFF.md, claude_memory.md) normalises to `/home/lumen` → advisory only. Comment at `:42-45`: hard-coded on purpose so an edited policy file cannot hand a worker Frank's tokens. ISSUE-LOG row 1553 (wq#4581) confirms "no worker route exists by design". | A — code + log row + 2 failure.json (wq#4622, wq#4625: `dir(/home/lumen) refused 6x`) |
| S2 | Primary-tree write freeze | `harness/guard.sh:452-476` (Bash: HEAD-moving git verbs in a primary tree denied, wq#900) and `:670-725` (Write/Edit inside any `PRIMARY_TREES` entry blocked, wq#1719; sole exception Edit on `/home/lumen/kb/ISSUE-LOG.md`, wq#1831). `/home/lumen/minerva` is in the list at `:476` and `:698` (wq#1540). | Edit/Write of anything under `/home/lumen/minerva` (and foreman/bin/agent/logs...) from a worker. Read is allowed only if the tree is an add_dir — minerva is an add_dir only for `research-synthesis` (`harness/agents.yaml:79-89`), NOT for `verify-pass` (`agents.yaml:69-72`, no `add_dirs:` → inherits `defaults.add_dirs` `:39-47`, which has no minerva). | A |
| S2b | minerva data is not in git | `/home/lumen/minerva/.gitignore:15-26` ignores `context/`, `archival/`, `episodes/`, `*.db` | Even a worker with a minerva *worktree* cannot see `context/LIVE_FLEET_STATE.md`, `claude_memory` sections or minerva.db: they exist only in the live primary. So "give it a worktree" is not a route for minerva verification; read reach into the primary is the only route. | A |
| S3 | docker deny beats allow | `harness/worker-settings.json:23-24` allow `Bash(docker ps*)`, `Bash(docker logs*)`; `:121` deny `Bash(docker *)`. Claude Code evaluates deny before allow → the two allow lines are dead. | every `docker` invocation, including `docker ps`. (The READ-ONLY docker MCP from wq#4514-4518 is registered at *user* scope for interactive Claude Code on Lumen; it is not in the fworker allowlist either — `grep mcp__docker harness/worker-settings.json` is empty, only `mcp__keenable__*` at `:103-104`.) | A |
| S4 | Non-foreman repos | `harness/repo_sweep.py:92-94` `managed_repos() = _lane_repos() | EXTRA_REPOS | _simpill_clones()`; `_lane_repos()` `:42-49` now unions `router.REPO_TAGS` (wq#4623, commit 43644a32 **on main**); `orchestrator/router.py:1332-1338` `REPO_TAGS = {ccc, messenger, foreman, bin, minerva}`. Live result this session: `managed_repos()` **includes** `/home/lumen/ccc` and `/home/lumen/messenger`. | **[ticket stale]**: ISSUE-LOG 1565 is marked SOLVED by wq#4623. What is still true: (a) a ticket only lands in a messenger/ccc worktree if its title carries the `[messenger]`/`[ccc]` tag or a `repo:` detail field (router `:1340` `_REPO_FIELD_RE`); (b) `verify_repo_hint` (`router.py:1161-1172`) honours `Verify repo:` only if the path is in `managed_repos()` — now true for ccc/messenger; (c) the repo primary is appended as a read-only add_dir (`fworker.py:2501-2502`) — so **read** reach exists, **write** does not (S2). ISSUE-LOG 1575 (wq#4624, same day) shows the remaining gap: a patch can be produced but must be hand-applied by the orchestrator. | B (code says fixed; 1575 post-dates 4623 and still blocked — needs one dry-run replay to settle, see §7) |
| S5 | ssh from a worker | `harness/worker-settings.json` has **no** `Bash(ssh*)` allow (grep empty) → every raw `ssh` is "requires approval" (failure.json wq#4614 `permission-refused 4x (ssh)`, wq#4627 `1x (ssh)`, wq#4622 first denied call is `ssh lumen@100.117.66.102`). The sanctioned path is `/home/lumen/bin/fleet-exec` (`worker-settings.json:21` allow), which needs a `needs:ssh(node)` grant minted at spawn (`fworker.py:2122-2150`) from `harness/fleet-exec-policy.yaml:194` `allow.ssh: [lumen, isis, bes, anubis, maat, sobek, hathor, ptah, sekhmet, horus, neith, khnum, maksoor]` and `/home/lumen/bin/fleet_exec_policy.py:26` `THOR_HARDLOCK = frozenset({"thor"})`. | **anubis and maksoor are grantable** (read-only run) if the ticket carries the tag; **thor is never grantable** (hardlock, correct); the practical denial is that auto-filed `[verify]` tickets carry **no** `needs:ssh(...)` tag (judgment.py:116-138 writes none), so the grant file is never minted and the worker reaches for raw `ssh`, which is off the allowlist. | A for the mechanism; the ticket's "ssh to anubis/thor/maksoor denied" is **half right**: thor by design, anubis/maksoor by missing tag |
| S6 | sudo / systemctl | `worker-settings.json:110-112,122` deny `systemctl stop*`, `disable*`, `mask*`, `sudo *` | any restart of a unit (`sudo -n systemctl restart`), any ntfy ACL edit via `docker exec` (S3). Correct for a build worker; it is exactly what option B must gate. | A |

### 1.2 The five replayed failure records (`_runs/wq*/failure.json`, read this session)

| wq | title (archetype) | classification / block_class / triage | first denied calls | denial class |
|----|-------------------|----------------------------------------|--------------------|--------------|
| 4479 | [verify] wq#4470 Claude Code CLI as Commander replacement (verify-pass) | KILLED / INFRA_CRASH / CAPABILITY_DENIED | `grep /usr/local/lib/token-engine/*.py` blocked_path; `dir(/home/lumen/.config/systemd/user/)` refused; `dir(.../mcp-oauth-worker/wrangler.toml)` | **S1-class + secret-prefix**: `/home/lumen/.config` is in `DIR_DENY_PREFIXES` (`capability_lint.py:49`), `/usr/local/lib/token-engine` is in the guard credential deny (`guard.sh:642`). Correct refusals — this verify needed a *credential-adjacent* read. Only an orchestrator (or a verify lane with an explicit, audited home-root read allowlist) can run it. |
| 4591 | [verify] wq#4577 CLOSURE GUARD (verify-pass) | KILLED / INFRA_CRASH / UNKNOWN ("no mechanical signature matched") | `ls` requires approval; `wq add` requires approval | **permission-loop on allowed tools** (`ls`, `wq` are allow-listed at `:16,:29`): the model issued them in a shape the pattern did not match (cd-chained / quoted). Not a wall — a harness classifier gap. Out of scope for the lane; file separately (§8). |
| 4614 | [verify] wq#4485 Thor SSH hardening (verify-pass) | PERMISSION_LOOP / HARNESS_DEFECT / CAPABILITY_DENIED | `ssh lumen@100.117.66.102` ×1, `-v` ×3 | **S5 + THOR_HARDLOCK**: verifying a change *on Thor* needs ssh to Thor, which no lane will ever get. The only correct route is orchestrator-only with an explicit block_reason — this is the archetype option D is about. |
| 4622 | [verify] wq#4610 [ccc] E: session metrics dashboard (verify-pass) | PERMISSION_LOOP / HARNESS_DEFECT / CAPABILITY_DENIED | `ssh lumen@...`, `gh auth`, `gh pr`; `dir(/home/lumen) refused 6x` | **S1 + S5 + wrong tool**: ccc repo is Gitea, not GitHub; worker fell back to ssh-to-self and `/home/lumen` reads. A verify lane with the ccc primary as add_dir (now granted by wq#4623 when the `[ccc]` tag is present — the *verify* title is `[verify] wq#4610 [ccc] E: ...`, so `_leading_bracket_tags` sees `[verify]` first: **confirm in dry-run whether the second tag is honoured**, §7 T4) and `node --test` reach would have passed. |
| 4625 | [verify] wq#4609 [ccc] F-2 wq ticket panel (verify-pass) | PERMISSION_LOOP / HARNESS_DEFECT / CAPABILITY_DENIED | `find ccc` blocked_path; `node /tmp/fworker-wq4625/ccc-review/test/wq.test.js` ×2 requires approval | **S1 + test-runner reach**: `node` is not allow-listed as a bare runner in worker-settings (only `npx *`, `node_modules/.bin/*` at `:100-101`); the worker copied the repo to /tmp to get around S2 and then could not run the test. A verify lane needs `node --test` / `pytest` in *any managed worktree*. |
| 4627 | [verify] wq#4626 research-prompt-queue drain stalled (verify-pass) | KILLED / INFRA_CRASH / CAPABILITY_DENIED | `ssh lumen@...` ×1; last error = primary-tree git wall (`guard.sh:573`) | **S5 + S2**: the probe is "is the unit draining `scratch/research_prompts/`" — a `systemctl status` + `ls` on the box, zero mutation. Pure A-lane work. |

Classification of the 17 by what the orchestrator actually needed (from titles + the 12 failure.json present; 4624/4619/4631/4581 have no failure.json — they were `wq block`ed by the worker, not killed):

- **Read-only probe on Lumen itself** (status of a unit/timer, ls a home-root or minerva path, read a report, run a test): 4479*, 4498, 4499, 4591, 4620, 4622, 4625, 4627, 4631, 4581, 4152 → **A covers** (*4479 needs the credential-adjacent read — A with an explicit allowlist entry, or orchestrator-only).
- **Read-only probe on another node** (anubis Gitea, maksoor): 4615, 4617 (ccc on Thor: **thor → orchestrator-only**), 4618 → **A covers via fleet-exec ssh(node) tag**, Thor excluded.
- **Small live mutation** (apply a staged messenger patch + restart `lumen-messenger`, `docker exec ntfy` ACL): 4624, 4619 → **B covers**.
- **Thor-only** (4614, 4617 verify-on-Thor): **D-class: must stay orchestrator-only with block_reason**, never dispatched.

### 1.3 Root cause statement

The verify ticket's **grant is derived from its own lane/archetype (build / verify-pass)**, not from **what it has to touch** (the shipped ticket's deploy surface). `file_verify_pass` (`orchestrator/judgment.py:98-145`) copies the shipped ticket's *repo* into `Verify repo:` but copies **none** of its `needs:` tags, none of its ops bundle's install target as an ssh/dir need, and sets no archetype hint for "this verifies a live unit". The dispatcher therefore spawns a build worker for a probe that is, by construction, outside a build worker's box. **Evidence grade A**: the wall is deliberate and correct for build workers (S1 comment, S2 comment, THOR_HARDLOCK); the defect is the *router*, which routes a probe to a box that cannot probe.

Narrative-kill attempt: "the walls are just too tight — widen `DIR_DENY_EXACT`". Disproof: wq#4479 shows the first thing a verify worker reached for was `/home/lumen/.config/systemd/user` and `token-engine` — a widened home-root grant would have handed it credential-adjacent paths. The denial surfaces are right; the routing is wrong. Second kill: "wq#4623 already fixed it" — 1575 (wq#4624) was blocked *after* 4623 landed, because 4623 adds read reach and the ticket needed a restart. Partial fix only.

## 2. Class analysis — why [verify] auto-filing routes to a lane that cannot probe

**Class:** *grant-by-origin instead of grant-by-target.* A derived ticket inherits the grant envelope of the lane it is filed into, not of the thing it must touch. The verify ticket is the loudest instance because it is filed for **every** shipped diff (`fworker.py:4262-4274`, `JUDGMENT_CLI verify-pass --for <id>`), so the class fires once per PR.

Where else the same class already exists (answer required by FIX-THE-CLASS):
1. `[repo-sweep]` tickets (`repo_sweep.py`) — solved for repo path by `repo_hint` (wq#1094), the same pattern `verify_repo_hint` copied; still inherits no ssh/dir needs.
2. `[selfheal]` tickets (wq#4638/4639: node offline → verify) — the verify of a selfheal is a *node reachability* probe; same wall (S5).
3. `[fleet-ssh]` tickets from `fleet-ssh-check --file-tickets` — probe needs ssh(node); filed without the tag.
4. `ops-deploy` verify (`judgment.py:139-145` `ops_verified(<bundle>)` liveness) — the *gate* checks the installed target but the *worker* still cannot read it when the target is home-root or `/etc` (wq#4581, ISSUE-LOG 1553).
5. `[judgment]` escalations (wq#4640/4642) — same envelope as verify-pass.

What makes the class impossible rather than this occurrence fixed: **derive the verify ticket's needs from the shipped ticket's deploy surface at filing time, and route by those needs** (§5). A verify ticket then either (a) dispatches to a lane that holds the grant, or (b) is filed *already blocked* with `block_reason=orchestrator-only:<why>` — never spawned into a box that cannot reach its probe. The wall stays; the attempt is never burned.

Why not simply "give verify-pass more add_dirs": every add_dir on a verify worker is also an add_dir on a sonnet-5@high model reading cold PRs with `Write`/`Edit` allowed (S2 is what keeps that safe). Reach is the thing to grant; *mutation* must stay behind a different, fail-closed profile (option B). Conflating them is how a build worker got `/home/lumen/.config` on its wish list (wq#4479).

## 3. Options with tradeoffs

### Option A — `verify-probe` lane: read-only reach + probe execution, zero mutation
Grant envelope (all additive, none touching `DIR_DENY_EXACT` / `DIR_DENY_PREFIXES` / THOR_HARDLOCK):
- **ssh BatchMode to fleet nodes** via the existing `fleet-exec` broker: the lane spawner mints `needs:ssh(<node>)` grants for every node named in the shipped ticket's `needs:`/ops bundle target (policy `allow.ssh` list, `fleet-exec-policy.yaml:194`; `run_readonly_cmds` only). Thor is never grantable (hardlock) → ticket is filed orchestrator-only (§5.3).
- **read `/home/lumen` root files and `/home/lumen/minerva`**: NOT by widening `DIR_DENY_EXACT`. Instead a new, hard-coded (same reasoning as `:42-45`) `VERIFY_HOME_ROOT_READ_ALLOW = ("/home/lumen/ORCHESTRATOR-HANDOFF.md", "/home/lumen/HANDOFF.md", "/home/lumen/AGENT-FILE-INDEX.md", "/home/lumen/claude_memory.md", "/home/lumen/FLEET_MAP.md")` plus `/home/lumen/minerva` as a **read** add_dir (already the shape `research-synthesis` has, `agents.yaml:79-89`). Everything under `.secrets/.ssh/.config/.claude`, `*.env|*.token|*.key`, `claude_credentials.md*`, `minerva/context/sections/*credentials*` stays denied (`guard.sh:642`, `capability_lint.py:46-57`). wq#4479's token-engine read stays refused → orchestrator-only, correctly.
- **docker read-only**: allow-list the READ-ONLY docker MCP (`mcp__docker__list_containers`, `inspect`, `logs` — the wq#4514 server behind `lumen_mcp_guard`, output masked) for this lane only; keep `Bash(docker *)` denied so the two dead allow lines (`worker-settings.json:23-24`) are deleted rather than "fixed". `docker exec` read-only is **not** granted in A (an exec is an exec; it goes to B).
- **run tests in any managed worktree**: `pytest`, `python3 -m pytest`, `node --test`, `npm test` allow-listed for argv targets inside `managed_repos()` worktrees or `/home/lumen/scratch/worktrees/*`; the guard's primary-tree wall (S2) still forbids running them *in* a primary with a HEAD-moving side effect.
- **writes**: only `foreman reports/<ticket>_verify_wq<N>_<slug>.md` in the worker's own worktree (landed by lumen-pr, as today) and `wq note`/`wq done`/`wq block`. No Edit/Write anywhere else — enforce by a lane-specific settings pack whose Write/Edit allow is the single glob `Write(<worktree>/reports/**)`.

Tradeoffs: (+) drains ~11/17 of the sample with no new mutation surface; (+) rollback is deleting one `agents.yaml` archetype entry + one settings pack; (+) every grant is a path/basename list the lint already understands. (−) does nothing for the 2 "small live fix" tickets; (−) `fleet-exec run_readonly_cmds` may need a few entries (`systemctl status`, `journalctl -n`, `ls`, `stat`, `cat` of non-secret paths) added per node — each is a policy.yaml diff, reviewable; (−) home-root allowlist is a hand-maintained list (acceptable: it is 5 files and the lint refuses anything else).

### Option B — `ops-apply` profile: hand-apply a staged ops diff / `docker exec` ntfy ACL / `systemctl restart` of a non-crown-jewel unit, fail-closed
- Never auto-dispatched by the broker: a ticket enters it only via `wq edit <id> --lane ops-apply` **by the orchestrator** after an A-lane (or orchestrator) verify report exists and the critic gate (`harness/action_gate.py`, judge tier) returns PASS for the *specific* diff/command. Gate field = the report path (wq `gate` is an enforced file path, gotcha:wq-cli).
- Preconditions enforced in the spawner, each a hard refusal (uncharged `SpawnRefused`, same code path as `CAPABILITY_DENIED_BY_POLICY`):
  1. `lumen_episode.py precheck "<plan>"` ran and its output is in the ticket notes (lumen-change-safety gate 2);
  2. a backup artifact path is cited and exists (`<file>.bak.<ts>` for configs; per-DB dump under `/home/lumen/backups/`) — gate 3;
  3. the target unit is in an explicit `allow.restart` list (`fleet-exec-policy.yaml` already has `allow.restart`; crown jewels — cloudflared, vault/Postgres on maat/sobek, litellm, lumen-pgvector, anything on Thor — are in `never.*` and stay there);
  4. the staged diff is a file in the ticket's worktree that `git apply --check` accepts against the live primary (ISSUE-LOG 1575's shape: `/tmp/fworker-wq4624/wq4624.patch`);
  5. the restart is issued through `fleet-exec restart <unit>` (broker-enforced, detached `systemd-run` per instinct 6 for anything Commander rides on) — never raw `sudo systemctl`.
- Post-condition: the same read-only probe from the verify report is re-run and must match; otherwise auto-rollback from the cited backup and `wq block` with `block_reason=ops-apply:postcheck-failed`.

Tradeoffs: (+) closes the last 2/17 and the whole "orchestrator hand-applies patches" toil; (+) every step is a thing lumen-change-safety already requires of a human. (−) it is a mutation lane for an LLM: the gate list above is the *minimum*, and the first build ticket must ship with a test-to-failure harness that proves each precondition refuses; (−) `docker exec` into ntfy to edit an ACL is a container mutation — restrict B's `docker exec` to an allow-list of `(container, argv-prefix)` pairs (`ntfy access ...`), nothing else.

### Option C — A then B (both)
A ships first and alone; B ships only after A has drained the verify backlog for ≥1 week with zero CAPABILITY_DENIED on A-lane tickets (measurable: `wq list --lane verify-probe --status blocked` empty, `failure.json` triage counts). B's build ticket depends on A's (wq deps). **Chosen — see §4.**

### Option D — stop auto-filing [verify] for these archetypes
Add a skip in `file_verify_pass` when the shipped ticket is `ops-deploy`/selfheal/Thor-targeted. (+) zero new surface. (−) removes the adversarial review exactly where fixes touch live units — the highest-value place to verify; (−) hides the toil instead of moving it; (−) contradicts `verify_pass.local_author_always` (models.yaml:360,373) which exists so a cheaper author model is always checked. Rejected, **except** as the narrow rule in §5.3: Thor-targeted verifies are filed *blocked* (orchestrator-only), not skipped.

## 4. Chosen approach

**C, staged.** Build order:
1. **Router/archetype change** (§5) — ships first and is useful on its own: it stops burning attempts on tickets no lane can run (files them blocked with a reason).
2. **Option A** lane (`verify-probe` archetype + settings pack + fleet-exec needs derivation).
3. **Option B** profile (`ops-apply`), gated on A's soak.
Each is its own build ticket with its own test-to-failure plan; none is a prerequisite of a service change; nothing in this PR implements any of it.

## 5. Router / archetype changes (design, not code)

### 5.1 Derive needs at filing time — `orchestrator/judgment.py:file_verify_pass`
Today (`:116-138`) the verify ticket gets: title `[verify] wq#N <60 chars>`, prose detail, `Verify repo: <path>` when `router.repo_for_ticket(t)` resolves, `ops_verified(<bundle>)` liveness when an ops bundle is cited. Add, in the same function, a **needs derivation** from the shipped ticket:
- copy the shipped ticket's `needs:ssh(<node>)` tags verbatim (the verify must reach every node the fix reached);
- for an ops bundle, read its `install.sh` target node(s) (the `_OPS_INSTALL_RE` / `ops_bundles_cited` machinery already parses the bundle, `acceptance.py:812` + wq#4641/4643) and emit `needs:ssh(<node>)` for each non-lumen target;
- for a cited home-root file (`capability_lint` normalises to `/home/lumen`), emit `needs:homeroot(<file>)` — a **new tag kind** that `capability.policy_grants` resolves against `VERIFY_HOME_ROOT_READ_ALLOW` only, refusing anything else (so `needs:homeroot(/home/lumen/.fleet_pw)` is a dispatch REFUSAL, same as a policy-forbidden ssh tag);
- for a `Verify repo:` outside `LANE_REPO` (ccc, messenger, minerva), emit the repo as both the worktree base (already, wq#4623) and `needs:dir(<repo>)` read (already appended at `fworker.py:2501`), **and** carry the leading bracket tag forward in the title so `_leading_bracket_tags` sees it — `[verify][ccc] wq#4610 ...` rather than `[verify] wq#4610 [ccc] ...` (§7 T4 decides whether this is needed).

### 5.2 Route by needs — `orchestrator/router.py`
- New archetype `verify-probe` (number 9; `router.py:227` table). `classify_archetype` keeps mapping `[verify] wq#` to `verify-pass` (`:659`) **unless** the derived needs contain any of {`ssh(*)`, `homeroot(*)`, `dir(/home/lumen/minerva)`, an `ops_verified()` liveness, docker-read} — then `verify-probe`. Pure diff-review verifies (the majority of foreman-repo PRs) stay on `verify-pass` with today's envelope; nothing changes for them.
- `verify-probe` entry in `harness/agents.yaml` (additive): `primary_tuple: sonnet-5@high`, `max_turns: 400`, `wall_clock_timeout_s: 2400`, `add_dirs: defaults + /home/lumen/minerva + /home/lumen/agent + /home/lumen/logs` (exactly research-synthesis's list, `agents.yaml:79-89`, which has run for weeks without an S1 incident), `permission_profile: verify-probe.json` (new pack: Bash allow adds `fleet-exec *`, `pytest*`, `python3 -m pytest*`, `node --test*`, `npm test*`, `systemctl status*`, `systemctl list-timers*`, `journalctl *`; the read tools of the wq#4514 docker MCP (list/inspect/logs, behind lumen_mcp_guard, masked); Write/Edit allow narrowed to `<worktree>/reports/**`; deny list = today's `:106-130` **plus** `Bash(docker exec*)`, `Bash(git push*)` except via lumen-pr, `Edit(*)`/`Write(*)` outside reports).
- Dispatcher pre-skip (`dispatcher/foreman_dispatcher.py` `needs_advisory` path, `:1045`): for `verify-probe`, a `needs_advisory` on `dir(/home/lumen)` is **not** advisory — it means the derivation missed; file the ticket blocked rather than spawn.

### 5.3 Fall back to orchestrator-only with a clear block_reason
When any derived need is unsatisfiable by policy — `ssh(thor)` (THOR_HARDLOCK), a `homeroot()` outside the allowlist, a credential-prefix path (wq#4479), `restart(<crown-jewel>)` — `file_verify_pass` files the ticket **already blocked**: `status=blocked`, `block_reason="orchestrator-only: <tag> refused by <rule> (capability_lint.py:NN | fleet_exec_policy.py:26)"`, `lane=build`, archetype `verify-pass`, plus a `wq note` naming the exact probe command to run. The broker never claims it; `wq list --status blocked` shows it with a reason a human can act on in one line; `session-handoff` already renders blocked tickets. **This is the D-shaped rule and it is the only place D applies.** No attempt is burned; the verify is not skipped.

### 5.4 Settings-pack hygiene that falls out of this
- Delete the two dead allow lines `Bash(docker ps*)` / `Bash(docker logs*)` (`worker-settings.json:23-24`) — they document an intent the deny at `:121` makes unreachable; the intent moves to the docker MCP read tools on `verify-probe`.
- `guard.sh` needs **no** change for A. For B, the `Write/Edit inside PRIMARY tree` block (`:670-725`) stays; B never Edits a primary — it `git apply`s a staged diff via `fleet-exec apply-staged <patch>` (new broker verb, policy-listed, `--check` first), and the primary tree is resynced through `sync-live` as today.

## 6. Ops-apply profile (option B) — gate specification (summary; full spec in its own build ticket)
| Gate | Enforced where | Refusal code |
|------|----------------|--------------|
| ticket entered lane by orchestrator `wq edit --lane ops-apply` (broker never files into it) | dispatcher: lane not in `config.json` auto lanes | n/a (not dispatchable by timer) |
| verify report exists at `gate:` path and names the diff/unit | `wq preflight` gate file check | GATE_MISSING |
| critic/judge PASS recorded for that report (`action_gate.py`, `FOREMAN_JUDGE_ENFORCE=1` for this lane only) | spawner | JUDGE_NOT_PASS |
| `lumen_episode.py precheck` output in notes | spawner | PRECHECK_MISSING |
| backup path cited + exists + newer than the target's mtime | spawner | BACKUP_MISSING |
| target unit ∈ `allow.restart`, ∉ `never.*`, node ≠ thor | `fleet_exec_policy.py` | CAPABILITY_DENIED_BY_POLICY |
| `docker exec` only `(ntfy, access/user argv prefix)` pairs | broker verb `docker-exec-allowlisted` | DOCKER_EXEC_DENIED |
| post-check = re-run verify probe; mismatch → restore backup, `wq block` | worker epilogue + broker | OPS_APPLY_POSTCHECK_FAILED |
Nothing in B touches Vault HA, cloudflared, the Commander path, litellm, lumen-pgvector, or any Thor unit: all remain `never.*` and orchestrator-with-Frank territory.

## 7. Test-to-failure plan — dry-run replay of 5 of the 17

Replay harness: `foreman_dispatcher.py run --once --dry-run` is the existing no-spawn path (FLEET_TOOLS foreman-dispatch); the build ticket adds a `--replay-ticket <id>` flag to it that re-derives needs from the *shipped* ticket and prints the would-be lane/archetype/grant/add_dirs/block_reason without spawning). No worker is spawned, no wq row changes; output goes to `/home/lumen/foreman/_runs/replay/<id>.json`.

| T | ticket | expected outcome under §5 | what "fail" looks like / what it teaches |
|---|--------|---------------------------|-------------------------------------------|
| T1 | wq#4627 (verify research-prompt-queue drain) | `verify-probe`; needs: none beyond defaults; add_dirs include `/home/lumen/agent`, `/home/lumen/logs`; grant: `systemctl status`, `journalctl`, `ls scratch/research_prompts` allowed | if routed `verify-pass` → the "any ops_verified/journalctl-shaped probe" trigger in §5.2 is too narrow; add `[selfheal]`/`drain`/`timer` lexical hints and re-run |
| T2 | wq#4625 (verify [ccc] F-2) | `verify-probe`; worktree base `/home/lumen/ccc` (REPO_TAGS); add_dir `/home/lumen/ccc` read; `node --test` on the worktree allowed; `find ccc` from `/home/lumen` no longer attempted because the worktree *is* ccc | if worktree base = foreman → the bracket-tag order problem (§5.1 last bullet) is real; fix title shape in `file_verify_pass` |
| T3 | wq#4614 (verify Thor SSH hardening) | filed **blocked**, `block_reason="orchestrator-only: ssh(thor) refused by THOR_HARDLOCK (fleet_exec_policy.py:26)"`, zero spawn | if it spawns in any lane → §5.3 is not wired before §5.2; **stop the rollout** — this is the test that must fail first and last |
| T4 | wq#4479 (verify Claude Code CLI as Commander replacement) | filed **blocked**, reason names `/home/lumen/.config` (DIR_DENY_PREFIXES) and `token-engine` (guard credential deny) | if it routes `verify-probe` with those paths advisory → `homeroot()` tag is leaking prefix paths; the lint's `_deny_reason` must run on the derived tag before `policy_grants` |
| T5 | wq#4624 (messenger /wq detail) *as a re-filed ops-apply ticket* | not dispatchable until: verify report + judge PASS + precheck + backup cited; then grant = `apply-staged <patch>` + `restart lumen-messenger` (in `allow.restart`), post-check = curl `/wq` shows `detail` | each missing precondition must produce its own refusal code in order; a spawn with any gate missing = B fails closed incorrectly → do not ship B |

Exit criterion for A: T1, T2 route correctly; T3, T4 block with the exact reasons; then a **live** run of T1 and T2 on `verify-probe` ends `wq done` with a report under `reports/`. Exit criterion for B: T5 refuses 5 times (one per missing gate) before it runs once, and the post-check mismatch path restores the backup in a staged failure (edit the patch to break `/wq` on purpose).

## 8. Findings that become tickets (same turn, per doctrine)
1. `[harness] worker-settings.json: Bash(docker ps*)/Bash(docker logs*) allows are dead under Bash(docker *) deny` — mechanical-edit; delete the two lines or move intent to the docker MCP (this design §5.4).
2. `[harness] failure classifier: KILLED "no mechanical signature matched" on allow-listed ls/wq (wq#4591)` — rca; the pattern match misses cd-chained/quoted invocations of allowed tools.
3. `[harness] ISSUE-LOG 1553 vs 1575: after wq#4623 messenger/ccc are in managed_repos(); update row 1553's "no worker route" to "read route exists, write route is ops-apply"` — docs.
4. `[foreman] primary /home/lumen/foreman has uncommitted edits to harness/agents.yaml + harness/liveness_ledger.yaml (local max_turns 15→40 comment)` — bin-drift-rescue-class: land or discard; blocks any lumen-pr that touches agents.yaml, which the A build ticket will.

## 9. Rollback
Every piece is additive:
- `harness/agents.yaml`: delete the `verify-probe` (and later `ops-apply`) archetype entry → `agents_spec` falls back to `verify-pass` for `[verify]` titles exactly as today.
- `harness/permission-profiles/verify-probe.json` / `ops-apply.json`: delete the file; fworker refuses a missing profile at spawn (uncharged), the broker falls through to the default pack.
- `judgment.py` needs derivation: behind `FOREMAN_VERIFY_DERIVE_NEEDS=off` → files today's ticket shape.
- `router.py` archetype 9: behind the same flag; `classify_archetype` never returns it when off.
- `fleet-exec-policy.yaml` additions (`run_readonly_cmds` entries, `apply-staged` verb, `allow.restart` entries): each a one-hunk revert; `fleet_exec_policy.py` `THOR_HARDLOCK` and `never.*` are never edited by this design.
No service, unit, container, or node is changed by this design or by A. B changes units only through `fleet-exec restart` on the allow list, with a cited backup, and is the last thing built.

## 10. Hard rules honoured
- No secrets on argv or in this doc (paths/names only; every secret path mentioned is a *deny* entry).
- No service changes in this ticket; design only.
- Thor hands-off: THOR_HARDLOCK is load-bearing in §5.3 and §7 T3, and nothing here can grant it.
- DESIGN BEFORE BUILD: build tickets reference this doc; the first one is the router change (§5), then A, then B.
