# wq#4040 — root-apply wq#4006 (REA security rescope bundle)

wq#4017 verified the bundle shipped by wq#4006 had never been applied: live
`~/.claude.json` still registered `rea`/`rea-maksoor` at user scope and `~/re`
was entirely absent (5x MISS on `install.sh --verify`). Per the bundle's own
`ops/wq4006-rea-security-rescope-a1/RUNBOOK.md` ("What's NOT done
(privileged/non-worker step)"), `~/.claude.json` and `~/re` sit outside a
sandboxed BUILD worker's granted dirs, so the apply has to run through
`lumen-run` — a detached `systemd-run` unit that executes as `lumen` outside
the per-tool Bash sandbox, the same escape hatch used for wq#3846
(`reports/wq3846_root-apply_wq3845-healer-drop-desktop-commander-mcp.md`).

## Preconditions checked

`ListAgents` (this session's view of Commander's live session registry, not a
raw `ps` sweep per instinct 4) reported "No reachable agents — no other
Claude session is running on this machine right now" immediately before the
apply, so no fworker could have been mid-tool-call against `rea`/
`rea-maksoor` when the edit landed.

## Apply

```
lumen-run start --name wq4006-apply -- bash -c \
  'WQ4006_REA_VERSION=6.5.0 bash ops/wq4006-rea-security-rescope-a1/install.sh'
```

Unit `lumen-job-wq4006-apply-1792062613.service` -> `DONE:0`. Journal:

```
== wq#4006: rescoping REA MCP registration ==
ACQUIRED 'wq4006-rea-rescope' by wq4006-install.sh for wq#4006 [build/p1] until 2026-10-15 16:10:13Z (ttl 300m) FENCE=1
backed up /home/lumen/.claude.json -> /home/lumen/.claude.json.pre-wq4006-20261015T111013Z
removed from mcpServers: rea, rea-maksoor
wrote /home/lumen/re/.mcp.json (scope: project)
wrote /home/lumen/re/PROVENANCE.json for rea-agents@6.5.0
== done: rea/rea-maksoor removed from user scope, project scope live at /home/lumen/re ==
RELEASED 'wq4006-rea-rescope'
LUMEN_JOB_EXIT=0
```

## Verify

```
lumen-run start --name wq4006-verify -- bash ops/wq4006-rea-security-rescope-a1/install.sh --verify
```

Unit `lumen-job-wq4006-verify-1792062623.service` -> `DONE:0`, journal:
`VERIFY OK`. This exercises every MISS check `install.sh --verify` ran at
wq#4017 (`rea-guard`, `HOSTILE_INPUT_POLICY.md`, `.mcp.json`,
`PROVENANCE.json`, and the "rea/rea-maksoor absent from user scope" check at
`ops/wq4006-rea-security-rescope-a1/install.sh:43-63`), now all passing
against the live box.

## Pinned-version confirmation

```
lumen-run start --name wq4006-rea-doctor -- bash -c \
  'cd /home/lumen/re && rea --version; echo "---"; rea doctor --provider ghidra'
```

Unit `lumen-job-wq4006-rea-doctor-1792062631.service` -> `FAILED:1`. `rea
--version` printed `6.5.0`, matching the `WQ4006_REA_VERSION` pin and
`PROVENANCE.json`'s recorded version. `rea doctor --provider ghidra`'s own
exit 1 comes from two causes unrelated to this ticket's scope: (a) it flags
the now-intentionally-absent `~/.claude.json` registration as
`config_drift`/"missing" — exactly the state wq#4006 set out to produce, not
a regression; `rea doctor` has no notion of the project-scope relocation
being the fix rather than drift — and (b) `GHIDRA_INSTALL_DIR` is unset in
the `lumen-run` unit's environment, an orthogonal provider-config gap this
ticket's scope (rescope + pin + provenance + guard) never touched. Neither
finding bears on wq#4006/wq#4040's acceptance criteria.

`rea-maksoor`'s own guard/pin is explicitly out of scope per the RUNBOOK
("Maksoor-side rea-maksoor has no equivalent guard (separate ticket, out of
scope here)") and is not attempted here.

## Liveness

`ops_verified(wq4006-rea-security-rescope-a1)` (probe at
`harness/liveness.py:432-451`) runs `bash install.sh --verify` itself against
the live box and now gets rc 0 — SATISFIED.

Refs: wq#4040, wq#4017, wq#4006
