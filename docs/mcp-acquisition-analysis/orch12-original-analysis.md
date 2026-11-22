# orch-12 MCP acquisition analysis: original text (recovered)

_Recovered 2026-11-22 under wq#4909._

## Provenance

- **Source:** claude.ai chat "Orchestator 12" (conversation `ca755740`), message 345, sent by Frank at 2026-11-05T10:01:31Z as a pasted attachment. orch-12 answered "Strong analysis" (msg 346) and filed wq#4873-4888 from it. The text reads as the output of an external research agent (ChatGPT bridge input, `fleet-intel/standing-orders/chatgpt-bridge.md`), not orch-12's own words.
- **Where it was found:** the full claude.ai export that wq#5040 put on Lumen on 2026-11-17: `/home/lumen/scratch/claude-export/conversations-000/conversations.json`. wq#4886 (2026-11-05) could not find it because chats were only reachable through recent_chats/conversation_search, and the chat-sweep (wq#4983) read only the first and last messages of each chat.
- **Fidelity:** the wording is unchanged. Formatting is lightly cleaned: tab-separated tables became markdown tables, the web-UI citation chips ("GitHub", "GitHub+1", "Claude Help Center") and repository cards were removed, and the trailing "Monitor Lumen acquisition candidates" UI button was dropped. A mask-secrets pass found no credentials.
- **Merged into:** `/home/lumen/docs/mcp-acquisition-analysis/README.md` (the wq#4886 index). That file now cites this one for the original ranking, stages, Ghidra tiers and filesystem profiles.

---

Let's go deeper. I'm going to treat this as an architecture decision, not a shopping exercise.

The central question is:

What new capabilities can Lumen gain by adding a small number of well-chosen components to its existing infrastructure?

I would start with hardware access, MCP security, and reverse-engineering automation. Those have the strongest connection to the work Lumen is already doing. Filesystem and database access come next; email/calendar can wait unless they solve a concrete workflow problem.

## Finding 1: Hardware control has a much better starting point than building a custom MCP server

I found a project that is unusually close to what Lumen needs:

**labgrid-mcp** (GitHub repository). Strong candidate for a controlled pilot. 47 MCP tools · Apache-2.0 · 28 commits in the repository snapshot I inspected.

It connects AI agents to labgrid rather than directly exposing every USB device on a host. Its capabilities include:
- Device discovery, acquisition, reservation, and release.
- Power control and power cycling.
- Serial console access and captured boot logs.
- USB and SD-card mux control.
- SSH access to acquired devices.
- Fastboot, DFU, and image flashing as opt-in operations.
- Background flash jobs with status and log polling.

It also has a demo lab with simulated hardware, so the initial integration can be tested without connecting or risking one of your physical devices.

Why this matters: labgrid already supplies resource ownership and device arbitration. That is a much better foundation than allowing multiple agents to independently open /dev/ttyUSB0, issue commands, or flash the same board.

There is an important caveat: labgrid-mcp delegates authentication to the network. Its documentation explicitly describes VPN or SSH-tunnel protection rather than built-in per-user authentication. It also exposes arbitrary SSH commands and potentially destructive flash operations when enabled.

My proposed policy:

| Operation | Initial Lumen permission |
|---|---|
| Discover hardware | Allowed |
| Read device information | Allowed |
| Acquire/release device | Approval or controlled lease |
| Read serial console | Allowed |
| Send serial commands | Approval initially |
| Power cycle | Approval initially |
| Change USB/SD mux | Approval |
| SSH commands | Disabled initially |
| Flash/write image | Disabled |
| Delete lab resources | Disabled |

This is a P0 pilot candidate, not a recommendation to deploy all 47 tools immediately.

## Finding 2: Filesystem access is a straightforward acquisition—but permissions matter more than the MCP server

**Official MCP Filesystem Server** (GitHub source). Recommended.

The official server provides file reading and writing, directory operations, searching, and metadata access. It supports directory restrictions supplied at launch. The current documentation recommends fixed command-line directory scopes rather than relying on the older Roots mechanism.

For Lumen, I would deploy it as separate permission profiles:

| Profile | Access | Intended use |
|---|---|---|
| Research | Read-only project directories | Inspect documentation, source, firmware analysis results |
| Developer | Read/write assigned worktree | Coding agents |
| Firmware analyst | Read-only original firmware; writable analysis output | RE agents |
| Fleet operator | Specific service/configuration directories | Approved operations only |

The important part is that the filesystem server must not be able to escape its assigned directories. I would use container read-only mounts for research profiles and separate writable worktrees for coding agents.

Decision: P1 — acquire and pilot. This is a relatively small addition that could eliminate a lot of ad-hoc SSH and shell access. I would not give it unrestricted access to /home/lumen.

## Finding 3: Do not replace the Lumen MCP gateway yet

There are several interesting gateway projects, but your existing mcp_gateway.py makes replacing it premature.

Three projects are worth comparing against its implementation:

| Project | What it offers | My assessment |
|---|---|---|
| Docker MCP Gateway | Container isolation, secrets handling, OAuth, gateway authentication, logging and tool discovery | Best security reference and possible complementary runtime |
| IBM ContextForge | Centralized registry, REST/gRPC-to-MCP, governance, authentication, tracing and federation | Powerful, but potentially excessive for Lumen |
| nick-pape/mcp-gateway | JWT authentication, per-tool allowlists, approval gates and identity-specific routing | Interesting design, but pre-1.0 and not my production choice |

Docker's gateway documentation describes several controls worth checking in Lumen: bearer authentication for HTTP transports, isolated server containers, secret scoping, path-mount restrictions, optional network blocking, and tool-call logging that avoids recording raw argument values by default.

ContextForge adds broader federation and observability capabilities, including OpenTelemetry. That is useful in a large multi-gateway environment, but may duplicate Lumen's existing services.

### The audit I would perform on mcp_gateway.py

| Control | What we need to establish |
|---|---|
| Authentication | Every remote client has an identifiable identity |
| Authorization | Permissions are assigned per tool, not just per server |
| Approval gates | Destructive tools require explicit authorization |
| Secret isolation | One server or agent cannot access another's credentials |
| Network boundaries | MCP backends cannot freely reach internal networks |
| Filesystem boundaries | Tools cannot escape approved project roots |
| Audit trail | Record identity, tool, outcome, and timestamp |
| Redaction | Never log credentials or sensitive tool arguments |
| Tool discovery | Denied tools are not exposed as usable capabilities |
| Failure handling | Gateway restart or backend failure does not silently bypass policy |

Decision: P0 — audit the existing gateway; P2 — consider replacing or extending it only if the audit identifies material gaps.

The gateway should become Lumen's policy enforcement point. I don't want each individual MCP server inventing its own authorization system.

## Finding 4: Ghidra automation is a significant gap in the supplied registry

You have a ghidra-re skill, but Ghidra itself does not appear in the active MCP registry you supplied.

That suggests Lumen may know how to perform reverse engineering without having a sufficiently direct, structured interface to the analysis engine.

**Ghidra MCP — bethington** (GitHub repository). High-value candidate. Apache-2.0 · 219 agent-visible tools in the inspected documentation.

This project is substantially more than a basic decompiler bridge. It advertises:
- Function decompilation and cross-reference analysis.
- Symbol and data-type modification.
- P-code emulation and data-flow analysis.
- Batch operations and analysis workflows.
- GUI and headless integration.
- Live debugger integration.

The repository documentation identifies a Ghidra 12.1.4 and Java 21 prerequisite for its current release. It also provides a health endpoint and a tool-schema endpoint, which would help us validate the integration automatically.

The concern is its broad authority. It exposes write operations, including renaming, typing, commenting, structure creation, and script execution.

I would not register all its tools directly with every Lumen agent.

Proposed exposure model:
- Analyst: read-only decompilation, strings, imports, exports, xrefs, call graphs.
- Senior analyst: annotations and controlled symbol/type changes.
- Specialist worker: script execution and P-code experimentation in disposable projects.
- Debugger agent: debugger tools only when explicitly assigned a target and session.

The gateway should enforce these boundaries even if the Ghidra server itself advertises more tools.

Decision: P1 — pilot on a disposable binary and Ghidra project.

### A second RE candidate: a broader investigation framework

**webdebug/reverse-engineering-mcp** (GitHub). This project is interesting because it goes beyond Ghidra. Its documentation describes workflows involving native binaries, Android packages, JavaScript, Electron, application behavior capture, evidence bundles, and comparisons between builds. It also uses external providers such as Ghidra, IDA, and Hopper rather than pretending to include all of those products.

This could complement your existing firmware and reverse-engineering skills, but I would first establish whether it duplicates your existing reverse-engineer-anything skill and 56 repositories.

Decision: P1 — evaluate for capability overlap; do not install alongside Ghidra MCP until we understand the overlap.

## Finding 5: DBHub is already the right acquisition; fix its access path

**DBHub** (GitHub). You already have DBHub 1.5.0 running for fleet-memory and fleet-state. It supports PostgreSQL, SQLite, MySQL, SQL Server, Oracle, and MariaDB, with schema discovery, SQL execution, optional query explanations, health checks, and configurable custom tools.

I would not add another database MCP server.

Instead, I would make three changes to the existing setup:
1. Separate database identities. Give agents read-only roles by default. Grant narrowly scoped writes only to the services that need them.
2. Resolve the 403. Determine whether the failure originates at the gateway, DBHub HTTP authentication, or the database connection. Those are different problems.
3. Expose purpose-built operations. For common memory operations, provide named tools such as search_fleet_facts, get_recent_episodes, and record_decision, instead of letting every agent construct arbitrary SQL.

For PostgreSQL, the intended model should be a read-only role for investigation and a separate narrowly authorized write path for approved memory operations. For SQLite, avoid giving multiple agents uncontrolled write access to the same database.

Decision: P0 — repair and validate the existing database integration. No new database connector.

## Finding 6: Hardware access alternatives—and why I would not use a basic serial MCP as the main solution

Two projects are worth understanding alongside labgrid.

| Project | Capability | Verdict |
|---|---|---|
| OpenOCD | JTAG/SWD debugging, programming, flash drivers, GDB/Tcl interfaces | Essential underlying tool, not an MCP broker |
| OpenHTF | Hardware test procedures, measurements, USB/ADB/Fastboot and serial plugs | Useful for repeatable test workflows |
| mcp-serial | Serial discovery, port access, command transmission, console capture | Limited experimental alternative |

OpenOCD already supports a broad range of JTAG adapters and target architectures, with GDB, Tcl, and telnet interfaces. It should be part of the hardware toolbox where supported by the device and debug probe.

OpenHTF is a different layer: it provides repeatable test execution and measurements, with plugs for ADB, Fastboot, USB, and serial capture. It complements labgrid rather than replacing its remote device ownership model.

The small mcp-serial project is worth watching, but the inspected repository snapshot showed only two commits. It can send arbitrary bytes to allowlisted serial ports, which is powerful but not a safe shared-device management architecture by itself.

### My recommended hardware architecture

```
Lumen agents
  Firmware analyst · Linux porting agent · Test agent · Debugger agent
        |
Lumen MCP gateway
  Identity · Tool permissions · Approval · Audit log
        |
labgrid-mcp / labgrid coordinator
  Device leases · Resource ownership · Console · Power · Mux · Flash jobs
        |                                   |
Hardware host A                       Hardware host B
  USB · UART · ADB · Fastboot · JTAG    USB · UART · SWD · Power · Reset
        |                                   |
Physical devices
  Miko 3 · SnapXT · RK3399 boards · future Limpet targets
```

This separates the agent's authority from the physical device. It also allows the hardware hosts to remain useful even if an agent crashes or its session is terminated.

One architectural rule: a hardware lease must be tied to a worker identity and have a recovery/expiry mechanism. A worker must not be able to leave a device acquired indefinitely after a crash.

## Finding 7: The missing plugins may be a registration or synchronization issue, not a missing directory

The current Claude plugin documentation describes account-synced plugins: installed plugins can synchronize into Claude Code at startup, provided Claude Code is sufficiently recent and the session is signed in with a supported account. Local plugin MCP servers run with the same local permissions as other programs.

Therefore, finding no files in /home/lumen/.claude/plugins/ does not conclusively establish that no plugins are installed or available.

For the blerbz auto-continue plugins, I would separately check:
- Claude Code version and sign-in method.
- Installed plugin and marketplace registration.
- Whether the plugin is account-synced or locally installed.
- Whether the plugin's hooks are registered and actually executing.
- Whether the plugin is compatible with the current Claude Code version.

I would not reinstall them until we know which of those failed.

Decision: P2 — diagnose persistence and registration. No replacement acquisition yet.

## Finding 8: Email/calendar should be an adapter project, not another major subsystem

Two candidates are worth keeping:
- gsuite-mcp — Google Workspace with Gmail, Calendar, Contacts, and Tasks; the repository describes 34 tools and an MIT license. Go implementation.
- personal-gmail-mcp — Gmail and Google Calendar with local OAuth and OS-keyring token storage.

But Lumen already has a mailbot service. Before adopting either, we should establish whether mailbot already provides a stable internal API.

My preference would be:
1. Keep mailbot as the canonical mail service.
2. Write a thin MCP adapter around its supported operations.
3. Start with read/search and draft creation.
4. Require approval for sending, deleting, forwarding, or changing calendar events.
5. Keep OAuth tokens and account identities isolated from general-purpose agents.

Decision: P2 — investigate the mailbot API first. No connector acquisition yet.

## Acquisition ranking — first pass

This is my current recommendation based on your inventory and the repository research. It is a priority assessment, not a claim that the candidates have been installed or tested on your fleet.

| Priority | Candidate / work | Decision | Reason |
|---|---|---|---|
| P0 | Gateway policy audit | Do now | Security boundary for every other acquisition |
| P0 | DBHub 403 remediation | Do now | Existing service is not fully accessible |
| P0 | Labgrid + labgrid-mcp pilot | Pilot | Highest-value new hardware capability |
| P1 | Official filesystem MCP | Acquire | Structured, scoped file access |
| P1 | Ghidra MCP | Pilot | Direct binary-analysis interface |
| P1 | REA framework | Evaluate | Potential end-to-end RE orchestration |
| P1 | OpenHTF | Evaluate | Repeatable hardware test procedures |
| P2 | Mailbot MCP adapter | Defer | Existing service may already solve the problem |
| P2 | Claude plugin persistence | Diagnose | Existing installations may be misregistered |
| P2 | ContextForge | Defer | Significant overlap with existing gateway |
| P2 | Docker MCP Gateway | Benchmark | Useful security reference and possible isolated runtime |
| P2 | mcp-serial | Watch | Too limited and immature for fleet-wide hardware control |

## Recommended rollout

I would implement this in five stages, with a test gate between each stage.

1. **Stage 0 — Establish the existing security boundary**
   - Review mcp_gateway.py.
   - Trace the fleet-memory 403.
   - Identify which agents can currently call destructive tools.
   - Confirm how secrets, identities, and audit logs are handled.

   No new installation until this is understood.
2. **Stage 1 — Simulated hardware pilot**

   Run the labgrid-mcp demo on an isolated development host:

   ```
   uvx labgrid-mcp demo
   ```

   Test discovery, acquisition, release, serial-console interaction, worker termination, and permission enforcement. Do not connect physical hardware yet.

   The project specifically provides this simulated lab for trying its interface.
3. **Stage 2 — Controlled physical hardware**

   Connect one expendable development board through a dedicated hardware host. Start with console access, device discovery, and power control. Add flashing only after approval and recovery procedures have been tested.
4. **Stage 3 — Filesystem and Ghidra**

   Add the scoped filesystem interface and Ghidra MCP to separate test agents. Test read-only access first, then controlled writes in disposable workspaces.
5. **Stage 4 — Reverse-engineering workflow integration**

   Compare your existing RE skills with REA, labgrid, Ghidra MCP, and OpenHTF. Build one complete workflow and measure how much work an agent can perform without human intervention.

## What I want to inspect next

The next useful step is to audit your actual gateway rather than speculate about replacing it.

Please provide:
1. /home/lumen/.../mcp_gateway.py — the actual gateway source, wherever it resides.
2. The DBHub launch configuration for fleet-memory, with credentials redacted.
3. The relevant gateway and DBHub service logs around the 403 response.

I want to establish whether the existing gateway can safely host the new filesystem, Ghidra, and labgrid integrations. That decision will determine whether we need any additional infrastructure at all.

My current recommendation: labgrid-mcp is the most interesting new discovery; gateway security and DBHub remediation are the most important existing-system improvements. I'd pursue those three before acquiring anything else.

I can also monitor the shortlisted projects for meaningful releases, security changes, and new integration capabilities each week.
