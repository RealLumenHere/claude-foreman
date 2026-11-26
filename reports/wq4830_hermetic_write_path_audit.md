# wq#4830 — fworker write-path audit and hermetic output contract

Date: 2026-11-25 PST · Agent: agent-4830-3ab29e · Host: lumen · CLI: claude 2.1.329

## Summary

Before this change a worker could write anywhere uid `lumen` can. The sandbox was a
set of denylists, so anything not named was allowed. This PR turns the tool-level
write path into an allowlist, makes `/scratch/agent-work/<ticket-id>/` a real
per-ticket output dir, and adds a kernel-level mount layer that is built and tested
but **off by default** (it breaks `lumen-run`; see "Decision for Frank").

Two premises in the ticket did not match the code:

- **There was no mount or bind logic in `fworker.py`.** The child is a plain
  `subprocess.Popen` of `claude -p` as uid `lumen`. The whole sandbox was the CLI
  permission pack (`worker-settings.json`) plus two PreToolUse hooks (`guard.sh`,
  `action_gate.py`).
- **Output did not "mostly" go to `/scratch/agent-work/<ticket-id>/`.** Nothing in
  `foreman/harness` or `bin` referenced that path. It held two hand-made dirs from
  10-27 (4142, 4423). Workers wrote to their worktree
  (`/home/lumen/worktrees/wq<id>-a<n>`), to `$TMPDIR` inside it, and to the wq
  scratch dir `/tmp/<agent>/`.

## What was open (probed against the live guard before the change)

Each row is a PreToolUse envelope fed to the live `guard.sh`, then to
`action_gate.classify_*`. "Allowed" means both walls passed it.

| # | Call | Before | After |
|---|------|--------|-------|
| 1 | `Write /home/lumen/.bashrc` | allowed (AMBER snapshot, then runs) | refused |
| 2 | `Write /home/lumen/.config/systemd/user/x.service` | allowed | refused |
| 3 | `Write /home/lumen/<anything>` | allowed | refused |
| 4 | `Write /home/lumen/worktrees/wq9999-a1/x.py` (another ticket's worktree) | allowed | refused |
| 5 | `Write <worktree>/../../../home/lumen/foreman/harness/zz.py` | allowed: the wq#1719 wall matched the raw string | refused, on the resolved path |
| 6 | `Write` through a symlink in the worktree that points outside | allowed | refused |
| 7 | `NotebookEdit` anywhere | not hooked at all (matcher was `Bash\|Read\|Edit\|Write`) | hooked, same allowlist |
| 8 | `Grep` / `Glob` with `path=/home/lumen/.secrets` | not hooked by guard.sh (same matcher gap). CLI 2.1.329 does not offer these two tools to a session, so this was reachable only in the `sdk` runtime, whose tool list has both | hooked: the existing credential deny now runs |
| 9 | `Bash: echo x >> /home/lumen/foreman/harness/zz` | allowed (GREEN) | refused |
| 10 | `Bash: cp a.txt /home/lumen/foreman/harness/zz` | allowed (AMBER) | refused |
| 11 | `Bash: sed -i ... /home/lumen/foreman/harness/guard.sh` (the live guard) | allowed (AMBER) | refused |
| 12 | `Bash: cat ~/.secrets/foo` | allowed | refused |
| 13 | `Bash: cat $HOME/.secrets/foo` | allowed | refused |
| 14 | `Bash: cd /home/lumen && cat .secrets/foo` | allowed | refused |
| 15 | `Bash: cat /home/lumen/.sec*/foo` | allowed | refused |
| 16 | `Bash: echo x > /home/lumen/zz`, `touch /home/lumen/.bashrc` | allowed | logged (shadow), refused under `FOREMAN_HERMETIC_BASH=enforce` |
| 17 | `Bash: python3 -c "open('/home/lumen/.sec'+'rets/x')"` | allowed | **still allowed at the hook**; refused by the mount layer |

Row 17 is the limit of any hook. Workers hold `Bash(python3 *)`, `Bash(node *)`,
`Bash(sed *)` and `Bash(cp *)`, so a hook that reads the command string can catch an
honest mistake but not a write made inside an interpreter. Only the mount layer
closes that.

## Checked with a real session

One `claude -p` run (CLI 2.1.329, haiku, 4 turns) against a copy of
`worker-settings.json` pointed at this branch's `guard.sh`: Write into the working
dir was allowed; Write to `/home/lumen/wq4830_smoke_probe.txt` came back as
"guard.sh: BLOCKED — hermetic output contract (wq#4830) ..." and no file was made.
The CLI accepted the widened matcher. Grep and Glob calls returned "No such tool
available", which is where the note on row 8 comes from.

## What enforcing costs: 30 days of real sessions replayed

Every Write/Edit target and every Bash command from the 243 fworker sessions of the
last 30 days (`~/.claude/projects/-home-lumen-worktrees-wq*`) was run through the new
checks. Paths only were read out, no content.

Write/Edit, 1,767 calls:

| Calls | Sessions | Where | Under the contract |
|------:|---------:|-------|--------------------|
| 1,324 | 160 | own worktree | allowed |
| 385 | 133 | wq scratch dir `/tmp/fworker-wq<id>/` | allowed |
| 4 | 4 | Edit on `kb/ISSUE-LOG.md` | allowed (wq#1831) |
| 34 | 33 | a primary tree | already refused before this change |
| 10 | 2 | a second-repo lumen-pr worktree whose name lacks the ticket id | **newly refused** |
| 1 | 1 | a second-repo lumen-pr worktree whose name has the ticket id | allowed |
| 5 | 4 | `/home/lumen/scratch/research/` | **newly refused** |
| 3 | 1 | `/home/lumen/claude_memory.md` | **newly refused** |
| 1 | 1 | bare `/tmp/<file>` | **newly refused** |

So 19 of 1,767 calls (1.1%), in 8 of 243 sessions, hit a refusal they would not have
hit before. Each refusal names the output dir. Two of those classes are workflow
changes Frank should know about: research workers have been writing deliverables
straight into `/home/lumen/scratch/research/`, and one session edited
`claude_memory.md` in place. Both now land under `/scratch/agent-work/<id>/` instead.
If either location should stay writable, name it in `FOREMAN_HERMETIC_EXTRA_WRITE`
handling in `fworker.build_env` rather than loosening the default.

Bash, 10,512 calls: 3 commands would be newly hard-refused, all real writes into the
live foreman tree (wq4929 `mkdir` + `cp` into `/home/lumen/foreman/security/` and the
`rm` that followed, wq4820 `chmod +x` on a file under `/home/lumen/foreman/ops/`).
7 more hard hits were `ls` of a dir under `.secrets`, already refused today. 36
commands in 25 sessions wrote outside the set to somewhere else (bare `/tmp` files,
`/home/lumen/bin`, `/home/lumen/scratch`, `~/.local`): those are the shadow rows, and
the reason `FOREMAN_HERMETIC_BASH` starts in shadow.

## Findings against the ticket's items

**5. All file output to `/scratch/agent-work/<ticket-id>/`.** Now created per ticket
by `fworker.hermetic_output_dirs()`, exported as `FOREMAN_OUTPUT_DIR`, passed as an
`--add-dir`, and named in the worker prompt. The writable set is: the worker's own
worktree, that output dir, and the wq scratch dir `/tmp/<agent>/`. The ticket says
"exactly one place". The worktree has to stay writable or lumen-pr has nothing to
commit, and the scratch dir is what `wq claim` tells every worker to use (wq#598,
wq#1697), so the set is three dirs, all created by the harness for that one ticket.
Folding the scratch dir into the output dir is a `bin/wq` change and is not in this PR.
One more path is writable because lumen-pr needs it: a second-repo worktree under
`/home/lumen/scratch/worktrees/` whose name carries the ticket's `wq<id>`. Any other
worktree under that root, including another agent's, is refused.

**6. `/home/lumen/foreman` read-only.** Write/Edit were already refused by the
wq#1719 wall on a raw-string match; rows 5, 6, 9, 10 and 11 went around it. Now
refused on the resolved path. Bash keeps one soft spot inside the tree: `_runs/` and
`events/` are runtime state, not source, and stay in shadow. Under the mount layer
the whole tree is read-only to every process, except `.git` (see item 8), `events/`
and the ticket's own `_runs/wq<id>/`.

**7. `/home/lumen/.secrets` invisible.** The add-dir gating was not airtight: rows 8
and 12 to 15. Those are closed at the hook, by resolving each word as a path instead
of matching the literal string. "Invisible" in the ticket's sense is only true under
the mount layer, where the dir is an empty tmpfs. fworker reads its tokens before the
spawn and passes them by env, and `lumen-pr` holds no token (token-engine socket,
wq#2273), so hiding the dir from the child breaks neither.

**8. lumen-pr as the only write path to the foreman repo.** True for commits and
branch moves (wq#900, wq#929 walls) and now for working files. Not true in one
respect that this PR does not change: a worker's `git commit` in its linked worktree
writes objects and refs into `/home/lumen/foreman/.git`. That is the PR path itself,
so the mount layer binds the git common dir read-write. A worker can therefore still
run raw `git` against that `.git`; the guard's git-verb walls are what stand there.

## What changed

| File | Change |
|------|--------|
| `harness/hermetic.py` (new) | The contract: writable set, modes, Bash destination parser, hook entry, bwrap argv |
| `harness/guard.sh` | `hermetic_gate()` called for Write/Edit (after the two older walls), Bash (last check) and a new `NotebookEdit` branch. Fail-closed on any helper error |
| `harness/fworker.py` | `import hermetic`; `build_env` exports `FOREMAN_OUTPUT_DIR` and the three explicit mode variables; output dir added to `--add-dir`; `run_child` wraps the child argv when the mount layer is on |
| `harness/worker-settings.json`, `permission-profiles/verify-probe.json` | guard.sh matcher now `Bash\|Read\|Edit\|Write\|NotebookEdit\|Grep\|Glob` |
| `harness/prompts/worker.md` | One bullet: where non-PR output goes and how to name a second-repo branch |
| `harness/tests/test_wq4830_hermetic.py` (new) | 106 tests |
| `harness/tests/test_guard.py` | One assertion updated: a path off the primary list is no longer writable by default |
| `harness/tests/conftest.py` | `FOREMAN_OUTPUT_ROOT` pointed at the test tmp dir |

`capability_lint.py` is unchanged. `DIR_DENY_EXACT` and `DIR_DENY_PREFIXES` gate
`needs:dir` grants, which are read context. With the allowlist in place a granted
dir no longer carries write access, so the lint needed no new entry.

## Modes and rollback

Set in fworker's environment or in `/home/lumen/foreman/hermetic.json`
(`{"modes": {...}}`), never from inside a worker. Unknown values mean `enforce`.

| Variable | Default | Values |
|----------|---------|--------|
| `FOREMAN_HERMETIC` | `enforce` | `shadow` logs `hermetic_shadow` rows and allows; `off` skips the contract |
| `FOREMAN_HERMETIC_BASH` | `shadow` | `enforce` refuses every explicit Bash write outside the set; foreman source and the secrets dir are refused in every mode but `FOREMAN_HERMETIC=off` |
| `FOREMAN_HERMETIC_MOUNT` | `off` | `enforce` runs the child under bubblewrap |

Rollback of the whole change without a revert:
`echo '{"modes":{"FOREMAN_HERMETIC":"off"}}' > /home/lumen/foreman/hermetic.json`

Shadow rows land in `/home/lumen/foreman/events/<worker>.jsonl` as
`hermetic_shadow_bash`. Count them before flipping Bash to enforce:
`grep -h hermetic_shadow_bash /home/lumen/foreman/events/*.jsonl | jq -r .reason | sort | uniq -c | sort -rn | head`

## The mount layer

`hermetic.mount_prefix()` builds: `bwrap --ro-bind / /`, private `/dev`, `/proc` and
`/tmp`, read-write binds for the writable set, the worktree's git common dir, the
events dir, the ticket's run dir, `~/.claude`, `~/.claude.json`, `~/.cache`, `~/.npm`,
`~/.local/state` and `/home/lumen/agents/work-queue`, then an empty tmpfs over the
secrets dir. Verified on lumen (Ubuntu 26.04.1, kernel 7.0.0-43, bubblewrap 0.11.1,
unprivileged user namespaces allowed for bwrap). The tests run real bwrap and assert
on the kernel's refusal: a `python3 -c open(...)` write to the live `guard.sh`, to
`/home/lumen/.bashrc`, to `/home/lumen/bin` and to the output root outside the
ticket's own dir each fail with "Read-only file system"; the secrets dir lists zero
entries; a `git commit` in a linked worktree still lands in the primary's `.git`.

It is off by default for four reasons, the first of which is a decision:

1. **`sudo` does not work inside it.** bwrap sets no-new-privileges, and
   `bin/lumen-run` starts its units with `sudo -n systemd-run`. Measured:
   `bwrap --ro-bind / / sudo -n true` exits 1 with "The no new privileges flag is
   set". `Bash(/home/lumen/bin/lumen-run *)` is on the worker allowlist and the test
   doctrine sends the full harness suite through it. Any unprivileged confinement has
   this property, Landlock included.
2. **It has not run a real `claude -p` session.** The read-write state list is my
   reading of what the CLI and the worker tools write, not a measurement. `~/.claude.json`
   is bound as a single file, so an atomic replace of it will fail inside the sandbox;
   whether CLI 2.1.329 falls back to an in-place write is untested. `lumen_episode.py`
   and `issue_log_append.py` write under `/home/lumen/minerva` and `/home/lumen/kb`,
   which are not on the list.
3. **A second-repo worktree cannot be created inside it.** `lumen-pr start` makes its
   worktree under `/home/lumen/scratch/worktrees/`, which is read-only in the sandbox.
   Worktrees that exist at spawn and carry the ticket id are bound read-write; one
   started mid-session is not.
4. **`fleet-exec` and `lumen-run` leave the sandbox by design.** A job started
   through either runs outside the child's mount namespace. The contract is airtight
   only once those brokers apply the same view to what they launch.

## Decision for Frank

Turning the mount layer on means choosing what a hermetic worker does about
`lumen-run`: lose it, or get it back through a broker that starts the unit with the
same read-only view (`lumen-run` already has `--bind-ro`, wq#4765, so the mechanism
exists on that side). Until that is chosen the kernel-level half of this ticket is
built but not on, and rows 16 and 17 above stay open. Follow-up: wq#5351 (frank_gated).

## Not changed, on purpose

- Worktrees stay at `/home/lumen/worktrees`. Moving them under
  `/scratch/agent-work/<id>/` would make the set one dir, but it touches
  `capability_lint`, the reaper, `lumen-pr` and live sessions: a rebuild, not a
  tightening.
- `/home/lumen/.secrets` was not touched.
- Other primary trees (`bin`, `minerva`, `kb`, ...) get the resolved-path Write/Edit
  wall but only shadow logging for Bash destinations. The ticket names foreman.
