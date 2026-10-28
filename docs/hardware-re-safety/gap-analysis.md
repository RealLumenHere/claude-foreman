# Hardware-safe RE stack: gap analysis vs fleet gates (wq#4744)

Inputs: `/home/lumen/docs/hardware-re-safety/orchestrator-hardware-safe-re.md` (the proposal) and
`tool-verification-2026-10-27.md` (the tool check). Audited 2026-10-28 against this checkout
(foreman, branch `feat/wq4744-...`, base `19095eda`). All `path:line` refs are in this repo
unless they start with `/`.

## Summary

The fleet already has most of the proposal's Layer 1 and part of Layer 2, built in-house:
`harness/guard.sh` (fixed denials), `harness/action_gate.py` (RED/AMBER/GREEN classifier, research
phase gate, backup-conditional flash class) and a deny-by-default Bash allowlist in
`harness/worker-settings.json`. Adding dcg, cc-safe-setup or Claude-Shield would mostly gate the
same commands twice. The real gaps are specific to hardware:

| # | Gap | Verdict |
|---|---|---|
| a | PreToolUse deny on flash/raw-device tools | **Partly confirmed.** dd/mkfs/wipefs/block-device writes are RED. esptool, openocd, st-flash and the other MCU/SoC flashers are gated only by a research gate that is off in every repo today and stays open once it has opened. |
| a′ | Hardware MCP tools (openocd, flashrom, serial) | **Confirmed, and the largest gap.** No hook matcher covers `mcp__*`. The openocd MCP exposes `write_target_memory`, which is the same failure class as the WAGO 750-831 brick. |
| b | Research ledger with target-specific evidence | **Confirmed.** The gate checks only that PRIOR_ART.md (generic search output) is non-empty, and the backup check only that a file is non-empty. Nothing checks for a datasheet, a flash layout or a recovery checksum. |
| c | Fail-closed human approval for irreversible actions | **Confirmed.** RED fails closed as a hard deny, but nothing can hold an action until a person approves it. There is also a latent LLM-judge allow path with no hardware floor. |
| d | Bypass modes | **No bypass mode used, none forbidden at runtime.** fleet-exec-policy says nothing about permission modes. The modes that really drop hooks are `--safe-mode`, `--restricted` and `--bare`, not `--dangerously-skip-permissions`. |

Headless fleet workers are less exposed than the gate tables suggest. Their allowlist does not
list any flasher or `ssh`, and their only allowed MCP tools are keenable search. Interactive
sessions are the exposed population: the orchestrator, or Frank's sessions in `/home/lumen/re`
where `serial-maksoor` is registered. The worker allowlist does not apply to them, and this audit
could not read their user-level settings (outside this ticket's read grant). That part is
UNVERIFIED and is a follow-up item.

## 1. Tool-by-tool coverage audit

### 1.1 What the fleet actually enforces today

| Layer | Where | Hardware-relevant coverage |
|---|---|---|
| Claude Code permission deny | `harness/worker-settings.json` `permissions.deny` | `Bash(dd *)` only. Prefix match, so it misses wrapped forms; guard.sh says so itself (`harness/guard.sh:4-6`). No mkfs, flashrom, openocd, st-flash or esptool rule. |
| Claude Code permission allow | same file, 98 entries | Deny by default. No flasher, `ssh` or serial tool is listed, so a direct call is refused headless. **But** `Bash(python3 *)`, `Bash(node *)`, `Bash(npx *)` and `Bash(bash scripts/*)` are allowed, so a flasher run through an interpreter clears this layer. |
| guard.sh (PreToolUse, Bash/Read/Edit/Write) | `harness/guard.sh:238-239`, `:253-254` | `dd ... of=` and `mkfs*`/`wipefs`. No flasher names. |
| action_gate BUILTIN_RED | `harness/action_gate.py:112-139` | dd `of=`, mkfs, wipefs, blkdiscard, nvme format, hdparm erase, partition tools without a read-only flag, fastboot erase/format, redirect/cp/cat/tee onto `/dev/(sd\|nvme\|mmcblk\|block/)`. **Not** `/dev/mtd*`, `/dev/mem`, `/dev/disk/by-*` or `/dev/ttyUSB*`. |
| action_gate BUILTIN_FLASH (RED unless a local backup exists) | `harness/action_gate.py:145-151` | fastboot flash, heimdall flash, edl write, adb push to system partitions, flashrom `-w`. **Not** flashrom erase, esptool, espefuse (irreversible eFuse burn), openocd, st-flash, stm32flash, avrdude, dfu-util, rkdeveloptool, upgrade_tool, sunxi-fel/xfel, mtkclient, or the MTD utilities. |
| action_gate research gate | `harness/action_gate.py:184-199`, `:214-227`, `:477-484` | Gates esptool, openocd, flashrom, dfu-util, stm32flash, avrdude, mtkclient, adb, fastboot and serial consoles. It is active only when a `.fworker-rules.yaml` sets `hardware_class` and `phase_gates: [research]`. It opens for good once any non-empty PRIOR_ART.md exists, whatever the operation (read or write). |
| action_gate file tools | `harness/action_gate.py:508-519` | Write/Edit under `/etc`, `/boot`, `/usr` or `/dev` is RED, and so is any edit of the rules file. |
| fleet-exec broker | `harness/fleet-exec-policy.yaml:206-225`, `:260-261` | Remote `run` is read-only argv only. `sudo_argv_deny` lists dd, mkfs and wipefs (`:44-55`). `bench_tools: thor: [adb, fastboot]` is v2 and not implemented. No path exists to run a flasher on another node through the broker. |
| verify/critic, stop_gate, lumen_episode precheck | `harness/hooks/stop_gate.py`, `limpet/critic.py` | Check completion and evidence after the fact. They never sit on the execution boundary. |

Whether any rules file exists: `find /home/lumen/foreman /home/lumen/worktrees/wq4744-a1 /home/lumen/agent /home/lumen/kb -maxdepth 4 -name '.fworker-rul*'` returned nothing, and
`/home/lumen/projects/limpet/.fworker-rules.yaml` does not exist (guard.sh: "not a regular
file"). fworker compiles rules only from the ticket repo and its worktree
(`harness/fworker.py:4247-4249`). So the research gate is **inert for all current Limpet work
dispatched from foreman**. The `/home/lumen/projects` tree was outside this ticket's grant, so a
Telpo or ESP32 project repo with a rules file there could not be ruled out.

Existing tests: `harness/tests/test_action_gate.py:721-858` (research gate, backup-required,
flash class). `pytest -k "research_gate or backup_required or flash"` gave **12 passed**
(2026-10-28).

### 1.2 Per tool

| Tool | Adds coverage beyond the fleet? | Notes |
|---|---|---|
| **dcg** | Little. A generic destructive-command hook with a pack system and allowlist (https://github.com/Dicklesworthstone/destructive_command_guard, fetched 2026-10-28). Its rm -rf/git class duplicates guard.sh and action_gate. It names no hardware flashers (UNVERIFIED: pack list not audited). | Fires under `--dangerously-skip-permissions` like any PreToolUse hook (see §4). |
| **cc-safe-setup** | None. Its 8 baseline hooks overlap guard.sh. | Its README now says hooks *still fire* under `--dangerously-skip-permissions`; see §4. |
| **Claude-Shield** | None. rm -rf, DROP TABLE, reset --hard, force-push and .env protection are all covered (`action_gate.py:112-139`, `guard.sh:250-251`, `:266-287`). | SQLite audit duplicates `foreman/events/*.jsonl` plus the gate handoff files. |
| **gateguard-ai** | Only the "deny first write until facts are stated" pattern. Its facts are code-level. | The fleet needs fact-forcing on target evidence, which is better built into the existing research gate (§3). |
| **Squidbrake** | **Yes, one thing:** a real review-hold, i.e. hold the call until a person approves, plus a self-protection check. Nothing in the fleet does this. | Default `when_unreachable: block_risky`. `squidbrake lockdown` writes managed settings that turn `--dangerously-skip-permissions` off (https://glama.ai/mcp/servers/batrapulkit/squidbrake, fetched 2026-10-28). |
| **guardhook** | None. Write/Edit path denial is already in `action_gate.classify_file_tool` (`:508-519`) and guard.sh. | Protecting known-good dumps and recovery images is a real need. Close it with gate rules (§3), not a third hook. |
| **@devrik-tools/claude-gates** | None specific to hardware. A broad framework whose delegation gates overlap fworker's own routing. | Retire. |

## 2. Hardware-specific gap analysis

### (a) PreToolUse deny on raw-device and flash-tool commands: PARTLY CONFIRMED

- Covered: dd with `of=` (guard.sh plus action_gate, always RED), mkfs/wipefs, block-device
  redirects, fastboot/heimdall/edl/flashrom-write (RED unless a backup exists).
- Not covered by any unconditional rule: esptool and espefuse (ESP32 targets), openocd and
  st-flash (SWD/JTAG), rkdeveloptool and upgrade_tool (RK3399/Telpo), sunxi-fel/xfel, MTD
  writes and MTD device nodes, `/dev/mem`. The research gate covers some of these by name, but
  (i) it is off in every repo checked and (ii) once opened it does not tell a read from a write.
- Interpreter-wrapped calls: action_gate matches tokens anywhere in the command, so an
  interpreter-module form of a covered tool still trips it. A flasher called from a script file,
  or a Python one-liner that opens a device node, does not. The proposal's rule 8 (bypass via
  wrappers) holds here, and no regex table closes it. Only removing access to the device does.

### (a′) Hardware MCP tools bypass every hook: CONFIRMED

The hook matchers are `Bash|Read|Edit|Write` (guard.sh) and `Bash|Edit|Write` (action_gate) in
`harness/worker-settings.json`. An `mcp__*` call reaches neither hook. Hardware MCP servers in
this repo:

- `ops/wq3881-openocd-mcp/server.py:71-81`: `write_target_memory(address, data)` and
  `flash_firmware(...)`, with no gate. An arbitrary write to target memory over JTAG is exactly
  the Forescout/WAGO failure (a payload wrote to a flash-mapped region).
- `ops/wq3882-limpet-flashrom-mcp-a1/server.py:56-78`: `write_flash` has no confirm.
  `erase_chip`'s `confirm=True` is a boolean the model supplies, which makes it a
  self-assessment gate (proposal rule 4).
- `plugins/limpet-plugin-flashrom/mcp/server.py:69-84`: the docstrings say "REQUIRES
  energize-confirm gate", but the code enforces nothing.
- `ops/wq4513-serial-mcp-maksoor/README.md:22`: no authentication; write/DTR/RTS/BREAK on any
  allowlisted UART. Registered on Lumen for `/home/lumen/re` (`claude-mcp-add.sh:8`). A
  U-Boot/BROM console over that link can erase flash.

`docs/hal-spec.md` §9 already makes energize-confirm mandatory policy (Frank, 2026-10-15). The
Limpet product agent implements it as a destructive-class interrupt
(`projects/limpet/src/limpet/agent/graph.py`, tested at `projects/limpet/tests/test_graph.py:115`).
Claude Code sessions that call these MCP servers directly get none of that. Headless workers are
safe only because the allowlist does not list these tools.

### (b) Research ledger with target-specific evidence: CONFIRMED

- PRIOR_ART.md is web, Reddit and GitHub search output from `harness/research_phase.py`. It
  does not mention the target's datasheet, memory map or flash layout, and it holds no recovery
  image hash.
- `_local_backup_ok` (`harness/action_gate.py:342-351`) passes on any non-empty file or
  non-empty directory. It checks no checksum, no image size against flash size, and no
  read-back verification.
- Evidence is checked per project, not per target, and never per operation.

### (c) Fail-closed human approval: CONFIRMED (deny exists, approval does not)

- RED returns exit 2 and writes a handoff for "a reviewing agent" (`harness/action_gate.py:70`,
  `:1156-1177`). That fails closed, but a human cannot approve that specific call and let it run.
  The action has to be redone outside the agent.
- Latent hole: with `FOREMAN_JUDGE_ENFORCE=1`, `enforce_judge_verdict` (`:864-911`) lets an LLM
  judge allow a RED rated Low or Medium (Medium with a snapshot). The judge assigns the tier
  itself, and there is no hardware floor. The setting is off today (default `"0"` at `:105`, and
  nothing in the repo sets it). Flipping it is "the only action left to go live" (`:94-95`).

## 3. Adoption plan (no double gating)

Order of layers, top to bottom. Every layer still runs, and none replaces the one below it.

1. **Squidbrake (optional, interactive RE sessions only)**: the human-approval layer. Scope:
   sessions in `/home/lumen/re` and any session with a hardware MCP server registered. Pilot it
   in `--mode observe` for a week, then switch on `review` for a hardware `rules.yaml` (flash,
   erase, fuse, voltage/clock, `mcp__openocd*`/`mcp__flashrom*`/`mcp__serial*` writes). Its
   default rules target git/cloud and must be written fresh. Headless fworker sessions do **not**
   get it, because no human is there to approve. For them a hardware RED stays a hard deny.
2. **guard.sh + action_gate**: unchanged role. Extend the action_gate tables instead of adding
   a third-party hook (follow-ups T1–T4).
3. **worker-settings.json allowlist**: unchanged, deny by default.

Per tool:

- **dcg / cc-safe-setup / Claude-Shield: pick at most one → dcg, deferred.** Only consider it
  for hosts that do not run the fleet harness (e.g. Frank's ad-hoc sessions on other nodes).
  Never stack it on fworker sessions. cc-safe-setup and Claude-Shield: retire.
- **gateguard-ai: do not adopt.** What it would add (forcing facts before a hardware action)
  belongs in action_gate's research gate as a per-target evidence manifest with hardware fact
  fields (T3). Its default prompts are about code edits.
- **guardhook: retire from the proposal.** File-tool path denial already exists
  (`action_gate.py:508-519`). Write-denial for known-good dumps and recovery images is added as
  gate rules in T3.
- **claude-gates: retire.**

## 4. Bypass-path audit

Correction to the ticket's premise: cc-safe-setup's own README (fetched 2026-10-28,
https://raw.githubusercontent.com/yurukusa/cc-safe-setup/main/README.md) gives a measured
table. User, project and local hooks **still fire under `--dangerously-skip-permissions`** ("the
guard refused, exit 2"). **None load** under `claude --restricted`, `claude --safe-mode`,
`CLAUDE_CODE_RESTRICTED=1` or `CLAUDE_CODE_SAFE_MODE=1`, and under safe mode Bash stays
available. A third-party summary also lists `--bare` as skipping hooks
(https://sathwick.xyz/blog/claude-hidden, fetched 2026-10-28). An open Claude Code bug reports
hooks being skipped after a background task completes under skip-permissions
(https://github.com/anthropics/claude-code/issues/47810, fetched 2026-10-28).

| Tool | Under `--dangerously-skip-permissions` | Under `--safe-mode`/`--restricted`/`--bare` |
|---|---|---|
| dcg | Fires (it is a PreToolUse hook; inferred from the table above, not tested) | Not loaded, so it fails **open** |
| cc-safe-setup | Fires (its README) | Not loaded, so it fails **open** |
| Squidbrake | Hook fires. `lockdown` uses managed settings to turn skip-permissions off. Gateway down → `block_risky` | Hook path not loaded (inferred). Its once-a-minute self-check would flag missing hooks, but only after the fact |
| fleet guard.sh/action_gate | Same as any hook | Same, so it fails **open** |

Fleet posture:

- fworker always launches with `--permission-mode acceptEdits` and `--settings`
  (`harness/fworker.py:2602-2603`) and never `--bare` (`:2560`). No launcher in `/home/lumen/bin`
  or `harness/` passes a bypass flag. A grep found only `probe_wq625.py`, which asserts its
  absence.
- What forbids bypass is a **static lint**: `harness/tests/test_settings_lint.py:27-44` bans the
  strings inside `harness/`. Nothing enforces it at runtime. `worker-settings.json` does not set
  `permissions.disableBypassPermissionsMode`. (That test file currently errors on collection in a
  bare `pytest` run: conftest cannot import `fworker` without `PYTHONPATH=harness`. Not
  investigated further.)
- **fleet-exec-policy does not forbid bypass modes.** It is the remote-exec broker's policy
  (nodes, verbs, argv) and has no permission-mode concept.
- Nested-launch bypass: `node *` and `npx *` are allowlisted, and neither guard.sh nor
  action_gate refuses a nested Claude CLI launch with `--safe-mode`, `--bare`, `--restricted` or
  the matching env vars. A worker could start a hookless child session. No such event has been
  seen; the path exists (T5).

## 5. Follow-up tickets

Filed 2026-10-28: T1 = wq#4746, T2 = wq#4747, T3 = wq#4751, T4 = wq#4748, T5 = wq#4749,
T6 = wq#4750. ISSUE-LOG row 1621 records the action_gate false positive on read-only greps hit
during this audit.

- **T1** action_gate: add a hardware write/erase/fuse class for esptool/espefuse, openocd,
  st-flash, stm32flash, avrdude, dfu-util, rkdeveloptool, upgrade_tool, sunxi-fel/xfel,
  mtkclient, flashrom erase and MTD utilities, plus `/dev/mtd*` and `/dev/mem` targets. Keep
  their read operations GREEN. Add tests.
- **T2** Gate hardware MCP tools: a PreToolUse matcher for `mcp__.*` that runs action_gate on
  hardware servers, plus a server-side energize-confirm token in the openocd, flashrom and
  plugin-flashrom MCP servers (hal-spec §9.2a). A boolean the model passes does not count.
  *Status (wq#4747):* landed. `harness/worker-settings.json`'s PreToolUse matcher is now
  `Bash|Edit|Write|mcp__.*`, routed to `action_gate.py hook` (`harness/worker-settings.json:147`).
  `classify_mcp_tool` (`harness/action_gate.py:901-933`) is RED by default for every energizing
  `mcp__<server>__<tool>` call — openocd `write_target_memory`/`flash_firmware`, flashrom
  `write_flash`/`erase_chip`, plugin-flashrom's energize ops, and serial-mcp writes — unless an
  operator-issued token verifies against `harness/energize_confirm.py` (`verify_token`); a
  model-supplied bool is never accepted. The three MCP servers themselves also refuse locally via
  `_require_confirm_token` shelling out to `energize_confirm.py verify`
  (`ops/wq3881-openocd-mcp/server.py:60-72`, `ops/wq3882-limpet-flashrom-mcp-a1/flashrom_ops.py:61-72`,
  `plugins/limpet-plugin-flashrom/mcp/server.py:51-`), so the gate holds even for a non-hook
  client. Tests: `harness/tests/test_energize_confirm.py`, `harness/tests/test_action_gate.py -k
  "mcp or energize or confirm"` (25 passed, 2026-10-28).
- **T3** Per-target evidence manifest for write-class actions (datasheet ref, flash layout,
  recovery image path plus sha256 verified at hook time). Write-protect known-good dumps. Add
  `.fworker-rules.yaml` with `hardware_class` for the Limpet/Telpo/ESP32 work repos.
- **T4** Judge floor: a hardware-class RED is never judge-allowed, whatever tier the judge assigns.
- **T5** Bypass hardening: `disableBypassPermissionsMode: "disable"` in worker-settings, a
  guard deny on nested Claude launches with hook-dropping flags or env, and an audit of the
  user-level settings and hooks for interactive `/home/lumen/re` sessions (needs a read grant on
  `~/.claude/settings.json` with masking).
  *Status (wq#4749):* the settings key and the guard.sh deny have landed
  (`harness/tests/test_wq4749_nested_hookless_launch.py`). A worker cannot run the audit:
  `capability_lint.DIR_DENY_PREFIXES` refuses a `/home/lumen/.claude` grant and guard.sh denies
  `*/.claude/*` reads. `harness/claude_settings_audit.py` is the masked audit tool, and running
  it on Lumen is a follow-up for the orchestrator lane.
- **T6** Squidbrake observe-mode pilot for interactive RE sessions with a hardware `rules.yaml`
  (depends on T2). *Status (wq#4750):* ruleset, pilot harness, report tool, and the
  observe→review decision gate authored under `docs/hardware-re-safety/squidbrake/`. The
  one-week observe run + review-mode decision is an interactive `/home/lumen/re` operator
  task (a headless worker is out of scope — it cannot be the human approver).
