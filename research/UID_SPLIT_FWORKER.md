# UID split for fworker — run `claude -p` as a dedicated user (wq#1334)

Status: research design, no code. Follow-on to `research/WORKER_EXEC_CAPABILITY_GAP.md`
§1.7 and §5.4 (PR #237). Everything cited below was read on this branch's base
(fworker.py, guard.sh, worker-settings.json, bin/wq, bin/lumen-run, bin/holddown) —
nothing here is guessed.

## 1. Problem

`fworker.build_env()` sets `HOME=/home/lumen` (`harness/fworker.py:863`) and
`run_child()` spawns `claude -p` with plain `subprocess.Popen` (`harness/fworker.py:989-992`)
— same UID as the user that owns `~/.ssh/id_ed25519`, `~/.ssh/fleet_config`,
`~/.secrets/*` and `queue.db`. `Bash(python3 *)` is allowlisted
(`harness/worker-settings.json:11`) and guard.sh has no rule on
`subprocess`/`os.system`, so the allowlist filters only the literal first token: a
`python3 -c` that opens the ssh key or shells out passes every layer. The sandbox is a
same-UID prefix filter, not a boundary (§1.7). More regexes are whack-a-mole (wq#1285,
wq#1316, ISSUE-LOG #71); the fix is a kernel boundary: run the child as a UID that
simply has no read permission on the secrets.

## 2. Goal and non-goals

**Goal.** The `claude -p` child (and everything it spawns, python included) runs as a
dedicated `fworker` UID whose filesystem view contains: its worktree, the shared run
plumbing it genuinely needs, and nothing credential-bearing. Reading
`/home/lumen/.ssh/*` or `/home/lumen/.secrets/*` returns EACCES from the kernel, not a
hook denial. The fleet-exec broker (§3.3 of the capability-gap doc) becomes a real
boundary: the worker *cannot* bypass it because raw ssh has no key to use.

**Non-goals (recorded honestly):**
- Protecting `CLAUDE_CODE_OAUTH_TOKEN` from the worker. build_env puts it in the child
  env (`harness/fworker.py:856-860`); the child necessarily holds its own model
  credential. Mitigation is token scoping, not filesystem perms — open question §10.
- Cross-lane isolation. All lanes share one `fworker` UID, so a worker can touch a
  sibling lane's worktree. Per-lane UIDs (fworker1..N) are deferred (§10); lane
  *attribution* at the broker uses the cgroup, not the UID (§6).
- Queue integrity in v1. Group-write on `queue.db` (§5) still lets a hostile worker
  mangle tickets. That protects nothing but is no worse than today; the wq daemon
  (v2, §5) closes it.

## 3. Architecture: drop at spawn, not whole-unit

Two shapes were considered:

**(B) whole-unit** — the `fworker-wq<ID>` lumen-run unit itself runs `--uid=fworker`
(the flag already exists: `bin/lumen-run:53,81` passes `--uid=lumen --gid=lumen` to
`sudo -n systemd-run`). Rejected: fworker.py the *supervisor* legitimately needs
lumen's powers — it reads the 0600 oauth token (`harness/fworker.py:107-108`), reads
langfuse creds from `/home/lumen/.secrets/langfuse-foreman.env`
(`harness/fworker.py:111-112`), mutates the primary repo's `.git` during
`make_worktree` under the gitlock (`harness/fworker.py:756`, `:778-789`), and issues
`sudo -n systemd-run` refill kicks (`harness/fworker.py:2189`). Running all of that as
`fworker` recreates the problem one level up or breaks it outright.

**(A) child-only drop (chosen)** — fworker.py keeps running as lumen inside its
existing lumen-run unit; only the `claude -p` child is spawned as `fworker`.

### 3.1 Spawn mechanics — the cross-UID signal problem

A plain `sudo -n -u fworker claude ...` prefix is NOT enough: the sudo shim is a
root-owned setuid process, and the watchdog's `proc.send_signal(SIGTERM)`
(`harness/fworker.py:1091`) gets EPERM from a lumen process against both the root shim
and the fworker child. The frame-gap watchdog is PRIMARY liveness
(RELIABILITY_SPEC §E, `harness/fworker.py:971-979`) — a kill path that silently stops
working is a stalled-worker leak.

Chosen spawn: a transient unit per attempt, mirroring the trust lumen already has for
`sudo -n systemd-run` (`bin/lumen-run:81`, `harness/fworker.py:2189`):

```
sudo -n systemd-run --collect --pipe --wait \
  --unit=fworker-child-wq<ID>-a<N> \
  --uid=fworker --gid=fworker-share \
  --setenv=... (the build_env vars) \
  /opt/claude/bin/claude -p ... --settings <run_dir settings> ...
```

- `--pipe` keeps stdout as a pipe to fworker's existing `_reader_thread`
  (stream-json unchanged); `--wait` makes the `systemd-run` process's lifetime track
  the service.
- **Kill path**: `sudo -n systemctl kill --kill-whom=all -s SIGTERM
  fworker-child-wq<ID>-a<N>` (then SIGKILL after the grace window), replacing
  `proc.send_signal`/`proc.kill` (`harness/fworker.py:1091-1094`). Needs one new
  sudoers line (`systemctl kill fworker-child-*`), scoped by unit-name glob.
- Exit-code normalization at `harness/fworker.py:1103-1106` must be re-verified:
  systemd-run --wait reports the service main-process status (143 for SIGTERM), not a
  negative signum — test 3 in §8 pins this.
- `start_new_session=True` (`harness/fworker.py:992`, ISSUE-SOLUTIONS #18) becomes
  redundant — the unit is its own cgroup and survives fworker's process group being
  signaled; keep it anyway for the systemd-run shim itself.
- `resource.getrusage(RUSAGE_CHILDREN)` (`harness/fworker.py:984`, `:1113`) now
  measures the sudo shim, not claude. cpu_s is corroboration-only by spec, so v1 reads
  the unit's `CPUUsageNSec` via `systemctl show` best-effort and logs `cpu_s=None` on
  failure — never a watchdog input.
- Env delivery: `--setenv` per var, or `EnvironmentFile=` pointing at a 0640
  lumen:fworker-share file in run_dir. Prefer `--setenv` — no token-bearing file at
  rest. Note the token is visible in `systemctl show <unit> -p Environment` **to root
  and to lumen only** (systemd hides unit env from unprivileged others); verify in
  test 2g.

### 3.2 Concurrency accounting

`harness/concurrency.py:8` counts live `fworker-wq*` lumen-run units — the
*supervisor* units, unchanged by this design. The new `fworker-child-*` transient
units are additional but match no counting glob; verify no glob in
spawner/concurrency accidentally matches (`fworker-wq*` vs `fworker-child-wq*` — the
child prefix is deliberately distinct).

## 4. New principal and filesystem plan

| thing | value |
|---|---|
| user | `fworker`, system account, shell `/usr/sbin/nologin` (claude is exec'd by systemd, no login shell needed) |
| HOME | `/home/fworker`, 0700 fworker:fworker — contains `.claude/` (transcripts, session state) and a provisioned `.gitconfig` |
| group | `fworker-share`, members: `lumen`, `fworker` |
| claude binary | today `/home/lumen/.npm-global/bin/claude` (`harness/fworker.py:2241`) — path traverses `/home/lumen`. Install a root-owned copy under `/opt/claude/` so lumen's HOME can later tighten to 750 and the worker can't be fed a trojaned binary it can itself write. `--claude-bin` already exists as the seam. |
| worktrees | `/home/lumen/worktrees` root stays lumen-owned; each per-ticket worktree gets `chgrp -R fworker-share && chmod -R g+w && find -type d -exec chmod g+s` at the end of `make_worktree` (`harness/fworker.py:756`) |
| acceptance.json | written by fworker.py as lumen:lumen 0644 — the child can no longer edit it in place (it can still unlink+rewrite via directory write; the dispatch-time hash check already routes that to rework, unchanged) |

### 4.1 The git worktree problem (biggest blast radius)

A linked worktree is not self-contained: its `.git` file points at
`<primary>/.git/worktrees/<name>/`, and **every child git op writes into the primary's
`.git`** — index/HEAD/logs under `.git/worktrees/<name>/`, and commits write objects
into the shared `.git/objects`. So the fworker UID needs group-write on the primary
repo's `.git` for every registered primary (foreman, pokemon, mailbot, work-queue,
nest, …):

- `git config core.sharedRepository=group` per primary, `chgrp -R fworker-share .git`,
  `chmod g+ws` on the dirs — git then creates objects/refs group-writable itself.
- `safe.directory`: the child's git will refuse a lumen-owned gitdir ("dubious
  ownership"). Enumerate the registered primaries in `/home/fworker/.gitconfig`
  at provision time — never `safe.directory=*`.
- Both UIDs must run with `umask 002` for the shared paths to stay group-writable
  (fworker.py sets it before worktree ops; the child unit gets `UMask=0002`).

Alternative considered — full clones per ticket instead of linked worktrees: cleanly
severs the shared `.git`, but breaks lumen-pr's land flow and the gitlock
serialization assumptions (`harness/fworker.py:726`, `:781-789`) and multiplies disk.
Rejected for v1; revisit only if shared-group primaries prove fragile in test 4.

Residual, recorded: group-write on primary `.git` means a hostile worker can corrupt
any primary's object store. Today it can do the same (same UID), so this is not a
regression — but the UID split does NOT protect primaries. Protecting them needs the
clone model or an upload-pack-only daemon; deferred (§10).

## 5. queue.db and wq

`bin/wq:97` opens sqlite directly (`WQ_DB=/home/lumen/agents/work-queue/queue.db`,
today lumen:lumen 644, dir 755). The child must run `wq done/block/release`.

**v1 — group-write:** `chgrp fworker-share` on `queue.db` and its directory,
`g+w` file, `g+ws` dir (sqlite creates `-wal`/`-shm`/journal files in the dir; the
setgid bit plus umask 002 in both writers keeps them group-writable — this is the
classic multi-user sqlite footgun, pinned by test 5). `wq` gets `os.umask(0o002)` at
startup. Residual: queue integrity is not protected from a hostile worker (§2) —
accepted for v1, no worse than today.

**v2 — wq daemon:** unix socket `/run/wq/wq.sock`, daemon runs as lumen and is the
only sqlite writer; `queue.db` returns to lumen-only-write. Caller identity via
SO_PEERCRED + cgroup (same mechanism as §6). Filed separately; not a blocker for the
UID split.

## 6. fleet-exec broker becomes a real boundary

The capability-gap doc's broker (§3.3 there) binds grants with `FOREMAN_GRANT_SHA` in
the worker env — forgeable by anything running as the same UID. With the split:

- Broker runs as a **systemd socket service as lumen**, socket
  `/run/fleet-exec/broker.sock` (0666 socket file; auth is peer-cred, not path perms).
- On connect: `SO_PEERCRED` → require `uid == fworker` (a lumen-side caller is
  refused — the orchestrator has its own paths and must not launder actions through
  the worker broker).
- Lane/ticket identity: read `/proc/<peer pid>/cgroup` → `fworker-child-wq<ID>-a<N>`
  unit name → ticket ID. The grant is looked up **server-side by ticket**;
  `FOREMAN_GRANT_SHA` demotes to corroboration. PID-recycle race: re-read the cgroup
  after the request is parsed and before execution; mismatch → deny (test 6).
- Everything else from the existing broker design is unchanged and *works better*:
  holddown registration (`bin/holddown`) runs inside the broker as lumen; the audit
  log `events/fleet-exec.jsonl` is lumen-owned append — after the split the worker
  **cannot rewrite the audit trail**, which today it can (same-UID write).
- The boundary claim finally holds: raw `ssh` from the worker fails not because a
  regex caught the token but because `~/.ssh/id_ed25519` is unreadable.

## 7. What breaks — inventory

| # | item | today | breakage under split | fix |
|---|---|---|---|---|
| 1 | worktree ownership | lumen 755 | child can't write its own tree | chgrp/g+ws in `make_worktree` (`harness/fworker.py:756`) |
| 2 | primary `.git` (objects, worktrees/) | lumen-owned | child `git commit`/`status` fails | `core.sharedRepository=group` + chgrp on each primary's `.git` (§4.1) — biggest blast radius, test first |
| 3 | git dubious-ownership check | n/a | child git refuses lumen-owned gitdir | enumerated `safe.directory` in `/home/fworker/.gitconfig` |
| 4 | watchdog kill | `proc.send_signal` (`harness/fworker.py:1091`) | EPERM cross-UID | transient unit + `sudo -n systemctl kill fworker-child-*` (§3.1) |
| 5 | cpu_s | `getrusage(RUSAGE_CHILDREN)` (`harness/fworker.py:984`) | measures sudo shim | unit `CPUUsageNSec`, best-effort; corroboration-only |
| 6 | langfuse creds | python escape can read them | none — **improves**; file stays lumen 0600, emit stays in supervisor (`harness/fworker.py:371`) | — |
| 7 | oauth token | in child env | unchanged (unprotectable, §2) | token scoping, open |
| 8 | lumen-run supervisor units | `--uid=lumen` (`bin/lumen-run:53`) | none — supervisor stays lumen (§3) | — |
| 9 | refill kick / dispatcher sudo | `harness/fworker.py:2189` | none — runs in supervisor | — |
| 10 | holddown flags | written by lumen-side tools | none — broker is lumen (§6) | — |
| 11 | guard.sh events dir | child appends `events/<worker>.jsonl` (`harness/guard.sh:13,40`) and `read_cache/` (`:328-330`) | EACCES on lumen-owned dir | chgrp fworker-share + g+ws on `events/`; residual: child can still edit its *own* guard log (group-write) — broker log is the authoritative stream |
| 12 | queue.db | direct sqlite write | EACCES | group-write v1 / wq daemon v2 (§5) |
| 13 | verify-evidence, prior-attempt notes, transcript classification | supervisor (lumen) reads worktree files the child wrote | none if group-read holds (umask 002) | test 1 asserts |
| 14 | rendered settings in run_dir (`harness/fworker.py:916`) | `_runs` 755, file 644 | none — read-only for child | — |
| 15 | claude binary path | `~/.npm-global` traversal through `/home/lumen` | breaks if HOME ever tightens | `/opt/claude` system copy (§4) |
| 16 | `--resume` across the migration | old sessions under `/home/lumen/.claude` | resume of pre-split sessions breaks once | accept; document in rollout notes |
| 17 | `ps -p <pid>` self-inspection by worker | allowed PID-targeted | unchanged (procfs readable cross-UID for basic fields) | — |

## 8. Test-to-failure plan

All on scratch state first — fworker already has the env seams
(`FOREMAN_EVENTS_DIR`, `FOREMAN_RUNS_ROOT`, `FOREMAN_WORKTREES_ROOT`, `WQ_DB`,
`harness/fworker.py:66-74`, `bin/wq:97`). Provision `fworker` + `fworker-share` on
Lumen, scratch primary repo, scratch queue.db. Every probe's *measured* result goes in
the follow-up doc — not believed.

1. **End-to-end synthetic ticket** on scratch: dispatch → child runs as fworker
   (assert via `ps -o user= -p`) → writes evidence.json → supervisor verify-evidence
   reads it → wq done lands. Asserts item 13 (group-read both directions).
2. **Escape probes** — a throwaway worker whose prompt *instructs* it to escape
   (the §1.7 reproduction that was never run live, this time expected to fail):
   (a) `python3 -c "open('/home/lumen/.ssh/id_ed25519')"` → PermissionError;
   (b) read `/home/lumen/.secrets/langfuse-foreman.env` → EACCES;
   (c) raw `ssh <fleet-alias> true` → key unreadable, auth fails;
   (d) `os.kill(<supervisor pid>, 15)` → EPERM;
   (e) sqlite-open `queue.db` raw → v1: succeeds (recorded as the accepted §5
       residual); v2 daemon: EACCES;
   (f) rewrite `events/fleet-exec.jsonl` history → EACCES (broker log lumen-owned);
   (g) `systemctl show fworker-child-* -p Environment` as fworker → must NOT reveal
       the token (unprivileged systemd show hides Environment; verify, else switch to
       EnvironmentFile 0640).
   Every probe must also appear in guard/broker audit streams.
3. **Watchdog cross-UID kill**: worker told to `sleep` past the frame gap → supervisor
   must SIGTERM via `systemctl kill`, child exits, exit-code normalization
   (`harness/fworker.py:1103-1106`) still yields 143→KILLED→resumable. Then the
   SIGKILL escalation path.
4. **Land pipeline on shared-group primary**: child commits, `lumen-pr commit/open/land`
   from the worktree; assert new objects in primary `.git/objects` are
   group-owned/writable (umask discipline held); then `git gc`/`worktree prune` as
   lumen still works on the mixed-ownership store. Run on scratch, then on a real
   low-stakes primary (pokemon) before foreman itself.
5. **sqlite two-UID contention**: lumen dispatcher and fworker `wq done` interleaved
   under WAL; assert `-wal`/`-shm` files come out group-writable from *both* creators
   (the umask 002 requirement) and no `database is locked` regression.
6. **Broker peer-cred**: connect as lumen → refused; as fworker from inside a lane
   cgroup → grant resolved by ticket; from an fworker process *outside* any
   `fworker-child-*` cgroup → refused; forged `FOREMAN_GRANT_SHA` with mismatched
   ticket → refused; pid-recycle simulation (kill peer between accept and exec,
   re-read cgroup) → deny.
7. **Rollback drill**: `FOREMAN_UID_SPLIT=0` in the dispatcher unit env reverts to the
   plain-Popen spawn in one restart; group perms are additive for lumen and can stay.
   Assert a post-rollback ticket completes.

**Failure budget**: any probe in test 2(a-d,f) succeeding, or test 3 failing to kill,
is a hard stop — the design is wrong, not the fleet. Test 4 breaking `git gc` is the
pre-identified likely failure; the fallback is the clone model (§4.1).

## 9. Rollout

Feature flag `FOREMAN_UID_SPLIT` read by fworker at spawn time; default off. Order:
provision user/group + `/opt/claude` (physical-free, one ansible-ish script) → scratch
tests 1-5 → flag on for ONE lane, real tickets, one week — watch KILLED/LAUNCH_FAILURE
rates in `_runs` vs baseline → all lanes → broker peer-cred enforcement (§6) only
after fleet-exec v1 (capability-gap §5.1 item 3) has an audit corpus. Rollback per
test 7 at every stage.

## 10. Open questions (deferred, not blockers)

- Per-lane UIDs for cross-lane worktree isolation — revisit if lanes exceed ~4 or a
  cross-lane incident occurs.
- `CLAUDE_CODE_OAUTH_TOKEN` scoping — the child holds it by necessity; rotation
  cadence and blast-radius review belong to the token's owner ticket (wq#615/#616).
- Protecting primary `.git` stores from a hostile worker (clone model or upload-pack
  daemon) — accepted residual in v1 (§4.1).
- wq daemon (v2 of §5) — file as its own build ticket once the split is stable.
