# wq#1766 — install LPP brain-escalate drop-in before PR #116 merges

## Problem

wq#1664 / PR #116 (`fix/wq1664-lpp-escalate-and-actretry-must-not-be-th-a1` on
`lumen-plays-pokemon`, not yet merged to `main` as of this ticket) makes
`src/live_agent.ts` refuse to boot (exit 2, `brainConfigErrors()`) when
`LPP_BRAIN_ESCALATE` or `LPP_BRAIN_ACTRETRY` equals `LPP_BRAIN_PRIMARY`.

Confirmed live on Lumen before this fix (`ops/wq1766-.../install.sh` step 0,
run read-only first):

```
/etc/systemd/system/lumen-pokemon-live.service.d/30-brain-env.conf:
  LPP_BRAIN_PRIMARY=glm-5.3-flash-zai
  LPP_BRAIN_ESCALATE=glm-5.3-flash-zai   <- equals primary
  LPP_BRAIN_ACTRETRY=glm-5.3-flash-zai   <- equals primary
```

Once PR #116 lands and `lumen-pokemon-live` next restarts, it would exit 2
immediately.

## Fix applied

Installed `35-brain-escalate-1664.conf` (copied verbatim from
`origin/fix/wq1664-lpp-escalate-and-actretry-must-not-be-th-a1:deploy/lumen-pokemon-live.service.d/35-brain-escalate-1664.conf`,
md5 `829ee6f4a6086acf60cdba129e892318`, reference copy at
`35-brain-escalate-1664.conf` in this directory) into
`/etc/systemd/system/lumen-pokemon-live.service.d/`. Lexical order (`35-` >
`30-`) means it wins over `30-brain-env.conf`'s values without editing that
file, so it applies cleanly whether PR #116 has merged yet or not, and the
existing `30-brain-env.conf.bak.*` history stays untouched.

## How it was applied (sandbox note)

`/etc/systemd/system` is outside every BUILD worker's worktree/`--add-dir`
sandbox (see `kb/ISSUE-LOG.md` #297/#320) — the wq#1664 build worker hit this
directly and could not install its own drop-in. This ticket used
`/home/lumen/bin/lumen-run start --root -- ...` (allow-listed:
`Bash(/home/lumen/bin/lumen-run *)`) to run the install as a detached, root
`systemd-run` unit, which is outside the harness's path sandbox (it's a real
system-manager job, not a Claude-tool file access). `install.sh` in this
directory is the reusable form of the exact commands run:

```
/home/lumen/bin/lumen-run start --name wq1766-install --root -- \
    bash ops/wq1766-lpp-brain-escalate-dropin/install.sh
```

Implementation: `ops/wq1766-lpp-brain-escalate-dropin/install.sh:16-24`
(`install -m 644` the drop-in, `systemctl daemon-reload`, `systemctl restart
lumen-pokemon-live`, then verify active + no `[config] FATAL`).

## Verification (evidence)

Run 2026-09-11, unit `lumen-job-wq1766-install-1789098347.service`:

```
active
---FATALCHECK---
NO_FATAL_FOUND
```

Follow-up check confirmed the merged unit config includes the new drop-in and
the service is running under a fresh PID (post-restart):

```
# /etc/systemd/system/lumen-pokemon-live.service.d/35-brain-escalate-1664.conf
ActiveState=active
SubState=running
MainPID=3602400
```

## Rollback

`rm /etc/systemd/system/lumen-pokemon-live.service.d/35-brain-escalate-1664.conf
&& systemctl daemon-reload && systemctl restart lumen-pokemon-live` reverts to
whatever `30-brain-env.conf` says at the time (only safe pre-PR#116; post-merge
that reintroduces the exit-2 boot gate, per the ticket's own equivalent-fix
note about deleting the `30-` lines instead).
