# wq#3903 — LIMPET agent chat interface (GUI mode) design

The local interface where an operator talks to the Limpet RE agent, watches it drive
tools, sees what hardware is attached, and knows which workspace it is acting in. This is
a **design-only** ticket — nothing here installs a UI framework, opens a port, or registers
an MCP server; those steps inherit the fleet gates. It builds directly on the Limpet
architecture (`design/wq3806_limpet_architecture.md`) and the plugin system
(`design/wq3810_limpet_plugin_system.md`), and does not restate them — it specifies only
the chat/GUI layer on top.

## 0. Constraints this design must respect (read first)

Four facts on the ground shape every decision. Ignoring them would be winging it.

1. **Limpet is an appliance operated from elsewhere, not a desktop.** The fleet's hard
   rule is that nodes have no local console and are driven by terminal commands issued from
   other machines (SSH). An M70q RE appliance sat in a shop is used the same way — an
   operator SSHes in, plugs a device into its USB ports, and points the agent. A GUI that
   *requires* a local display/keyboard would be unusable in the normal operating mode. The
   interface must therefore work **headless over SSH first**, with any graphical surface as
   an explicit opt-in, not the default. This is the single biggest constraint and it
   decides §3.

2. **The resource ceiling is real and already contended.** `design/wq3806_limpet_architecture.md:3`
   fixes the box at i5-14500 / 32GB / iGPU-only, and §6 of that doc notes a 9–14B resident
   model plus Ghidra/QEMU backends already sit "near the memory ceiling." A chat frontend
   that ships its own Chromium (Electron: a resident browser process per window, ~150MB+
   baseline and a full renderer) competes with the workload the box exists to run. The UI's
   job is to be a thin renderer, not a second heavyweight service.

3. **The agent is already defined; the UI does not re-implement it.** The agent core
   (`design/wq3806_limpet_architecture.md:232-256`) is the single MCP client to the
   meta-server and owns the tool loop, trust boundary, and inference. The chat interface is
   a **view onto an agent session**, not a new agent. It must not open its own MCP
   connections, call tools directly, or make its own trust decisions — every tool call it
   shows passed through the meta-server's trust gate already.

4. **Trust tiers and secrets must survive the UI, not be undone by it.** The meta-server
   refuses `community`-tier tool calls and returns a structured "promotion required" error
   (`design/wq3806_limpet_architecture.md:137-139`); the agent may *propose* a promotion but
   cannot self-promote (`:246-249`). The UI renders those states faithfully and never offers
   a one-click "enable and run" that would route around the §3 trust-gate. Tool output is
   already size-capped and written to the workspace by reference (`:144-147`); the UI shows
   the reference and a bounded preview, never inlines megabytes, and never renders a value
   that looks like a credential.

## 1. Scope — the four surfaces the ticket requires

The interface must show, in one coherent view:

- **Chat history** — the operator↔agent conversation, including the agent's reasoning
  summaries and final answers.
- **Tool invocations with results** — every meta-server tool call the agent makes, its
  arguments, trust tier, status, and a bounded result preview with a link to full evidence.
- **Hardware events** — the live HAL inventory (what is plugged in) and the hotplug/health
  event stream, including captured frames from the visual/KVM path.
- **Workspace context** — which investigation/workspace is active, its target identity, and
  the evidence/notes/git state that the session is accumulating.

Everything below exists to serve those four, plus the ticket's two cross-cutting
requirements: a **framework decision** (§3) and **LiteLLM model selection** (§5).

## 2. Architecture — one session service, thin clients over an event stream

The load-bearing decision: **the chat UI is not the agent and does not embed it.** A
long-lived `limpet-session` service hosts the agent loop (the MCP client of
`design/wq3806_limpet_architecture.md:112-131`); frontends are thin clients that subscribe
to that session's event stream and submit user turns to it.

```
operator ──(TUI, or opt-in web)── one thin client
                 │  local WebSocket / stdio: events down, user turns up
                 ▼
         limpet-session service        ← owns the agent loop; the ONLY MCP client
                 │  MCP (one endpoint)
                 ▼
         LIMPET META-SERVER            ← trust gate, routing, HAL, workspace (unchanged)
```

Why decouple, rather than the obvious "chat app that holds the agent in-process":

- **Survives UI disconnect.** An RE run (headless emulation, a Ghidra analysis pass) takes
  minutes to hours. If the agent lived inside the TUI, closing the SSH session or the window
  would kill the run. A detached session service means the operator reconnects and the
  trajectory is intact — the same reason fleet workers run detached from their launcher.
- **One session, many frontends.** The same session is viewable from the local TUI and,
  when enabled, the web surface, without running two agents or two MCP clients. Matches
  constraint §0.3 (one agent) exactly.
- **The event stream *is* the record.** Every event the UI renders is also what gets
  committed to `workspace.git` (`design/wq3806_limpet_architecture.md:216`) — the UI and the
  evidence log read the same stream, so what the operator saw is exactly what replays (§8).

Transport: a local WebSocket on `127.0.0.1` (loopback only, no external bind) for the web
surface, and a direct stdio/Unix-socket attach for the TUI when co-located over SSH. The
session service is started via detached `systemd-run` per the fleet's standing rule for
long-lived services, never foregrounded inside a UI process.

## 3. Framework decision — Textual TUI primary, Tauri web surface opt-in

**Recommendation: build the TUI first (Textual), add a thin Tauri web surface only for the
visual/KVM frame path. Do not build on Electron.**

| Option | Verdict | Why |
|---|---|---|
| **Textual (Python TUI)** | **Primary** | Runs over plain SSH with no display, X, or port — matches constraint §0.1 directly. Near-zero resident cost (§0.2). Same language as the registry/daemon (`limpet/daemon.py`), so it reuses the session service's Python event models with no FFI. Renders chat, tool tables, hardware/workspace panels natively; supports inline images on capable terminals (kitty/sixel) for frame previews. |
| **Tauri (web, OS webview)** | **Opt-in, frames only** | When a true image canvas is needed (Magewell capture / KVM `read_screen`, `design/wq3806_limpet_architecture.md:196`), Tauri uses the OS-provided webview instead of bundling Chromium — tens of MB, not Electron's ~150MB+ resident browser (§0.2). Served to a remote browser pointed at the loopback WebSocket; no local display required on the appliance. |
| **Electron** | **Rejected** | Ships and keeps resident a full Chromium per window — the exact second-heavyweight-service that constraint §0.2 forbids on a box already at its memory ceiling with Ghidra/QEMU. |

Both frontends consume the identical event protocol (§6), so the TUI is never a lesser
client — the web surface adds an image canvas, not new capability. A frame event renders as
a sixel/kitty inline image in the TUI and as a canvas in the web view; everything else is
text in both.

## 4. The four views in detail

### 4.1 Chat history

- **Turn model.** A session is an ordered list of turns: `user`, `agent_message`
  (reasoning summary + answer, streamed token-by-token as it arrives from LiteLLM),
  `tool_call` / `tool_result` (§4.2), and `system_event` (hardware/workspace, §4.3–4.4).
  All four render inline in one scrollback so causality is visible — the operator sees the
  question, the tools the agent reached for, what came back, and the conclusion in order.
- **Streaming.** Agent text streams as it is produced; tool calls appear the moment they are
  dispatched (status `running`) and update in place when the result returns. No turn is
  shown as complete until the session service marks it done.
- **Persistence.** The turn log is appended to the active workspace
  (`notes/session.jsonl`, committed into `workspace.git`), so chat history is part of the
  reproducible evidence bundle (§8), not UI-only state. Reconnecting a client replays the
  log from the workspace, then tails the live stream.

### 4.2 Tool invocations with results

This is where the meta-server's two-level surface and trust model
(`design/wq3806_limpet_architecture.md:134-159`) must be rendered honestly.

- **Call rendering.** Each tool call shows: plugin.tool name, trust tier badge
  (`core`/`vetted`/`community`), argument digest (full args on expand), status
  (`running`/`ok`/`error`/`refused`), and elapsed time. Router tools (`list_capabilities`,
  `open_target`, `run_tool`, `describe_tool`, `list_hardware`) render the same way as
  leaf tools.
- **Capped results by reference.** RE tools emit megabytes; per
  `design/wq3806_limpet_architecture.md:144-147` the result is written to
  `workspace/evidence/` and only referenced. The UI shows a **bounded preview** (first N
  lines / a thumbnail) plus the evidence path and content hash, with an "open in workspace"
  affordance — it never inlines the full blob into chat scrollback (which is also the
  session's own context budget).
- **Trust states are visible, not actionable around.** A `community` tool call returns the
  meta-server's structured "promotion required" error (`:137-139`); the UI renders it as a
  distinct `refused` state showing *which* plugin and *why*, and surfaces the agent's
  promotion **proposal** (if it made one) as a read-only item routed to human/Minerva review
  — never a button that promotes and runs. This is the §0.4 line.
- **Untrusted-output hygiene.** Tool results derive from untrusted targets (firmware, APKs).
  The UI treats all result text as data: no terminal escape passthrough, no clickable
  auto-exec, no markdown that could trigger a fetch. Rendered inert, like the fleet's
  pasted-content rule.

### 4.3 Hardware events

Driven by the HAL (`design/wq3806_limpet_architecture.md:180-204`).

- **Inventory panel.** A persistent panel lists currently-attached devices from
  `list_hardware` (probe/capture/analyzer/SDR/programmer class, matched plugin, health
  state). This is the "hardware awareness" differentiator made visible.
- **Hotplug/health stream.** udev hotplug and `[health]` transitions arrive as
  `hardware_event`s and render both in the panel (live state) and inline in chat
  (`system_event`, so "ST-Link attached" is visible in trajectory order). A newly attached
  device the agent can now use appears without the operator asking.
- **Frames.** Visual capture (`capture_frame` / `read_screen`) arrives as `frame` events —
  inline image in the web canvas, sixel/kitty preview in the TUI, full frame written to
  `workspace/evidence/` by reference like any other large result.

### 4.4 Workspace context

Driven by the workspace manager (`design/wq3806_limpet_architecture.md:206-230`).

- **Active-workspace header.** Always-visible: current `<target-id>`, target identity +
  hashes from `manifest.json`, bound hardware, and `workspace.git` state (dirty/committed,
  last commit). The operator never has to guess which investigation a tool just acted on.
- **Evidence/notes browser.** A panel tree over `evidence/` and `notes/` with previews, so
  outputs referenced from chat (§4.2) are one click from their full form.
- **Switching.** `open_target` creates/selects a workspace; the UI re-subscribes the session
  to that workspace's stream. One agent session acts in one workspace at a time, preserving
  the isolation guarantee (`:224-226`) — the UI makes the active one unmistakable rather than
  letting tools land in an ambiguous context.

## 5. LiteLLM routing & model selection

The agent core infers via Ollama behind LiteLLM, with the option to route hard steps to a
fleet/cloud model (`design/wq3806_limpet_architecture.md:233-239`). The interface exposes
that routing as **operator-controlled model selection**, grounded in the fleet's existing
LiteLLM gateway and model standard (`research/WQ3798_M80S_LLM_MODEL_SELECTION.md`,
`research/models.yaml`, `research/MODEL_ROUTING_AUDIT.md`).

- **Model picker populated from the gateway, not hard-coded.** The session service queries
  the LiteLLM gateway's `/v1/models` and shows only models the appliance's gateway key can
  actually call (local group — e.g. the `qwen`/`llama` local-worker models in
  `research/models.yaml` — plus any fleet/cloud routes the key is scoped for). Hard-coding a
  model name is forbidden: `design/wq3806_limpet_architecture.md:239` explicitly says "do not
  hard-code 14B," and the fleet's own `keenable`/LiteLLM failures show how a stale assumed
  model/token wastes a run.
- **Two selection scopes.** (a) **Session default** — the resident model (9B default per
  `design/wq3806_limpet_architecture.md:238`), chosen in the picker. (b) **Per-step
  override** — the operator (or the agent, proposing) can route one hard step to a larger
  fleet model for that turn only, then fall back. Each turn records which model answered it,
  shown next to the `agent_message`, so the trajectory says what produced each conclusion.
- **Local-vs-fleet and cost awareness.** The picker marks each model `local` (genuine local
  compute, no account pool) vs. `fleet`/`cloud` (consumes a shared pool) using the pool
  taxonomy in `research/models.yaml`. Routing off-box is a visible, deliberate choice — the
  default stays local so the appliance's "runs on the box" property holds.
- **Failure rendering.** A routing failure (gateway down, model unavailable, 401 like the
  `keenable` server this session saw) surfaces as a distinct turn state naming the model and
  the gateway error, with retry/reselect — never a silent hang. Per fleet instinct #8, a
  model name the operator typed that returns 404 is evidence the name was wrong, not that
  the gateway is broken; the UI shows the gateway's own enumerated list to prevent that.

## 6. Event protocol (the contract between session service and clients)

One newline-delimited JSON event stream, append-only, mirrored to `notes/session.jsonl`.
Event kinds (payload sketches, not final schema):

- `user_turn` — `{text, ts}` (client → service).
- `agent_message` — `{role:"agent", text_delta|text, model, done, ts}` (streamed).
- `tool_call` — `{id, plugin, tool, args_digest, trust, status:"running", ts}`.
- `tool_result` — `{id, status:"ok|error|refused", preview, evidence_path, hash, ts}`.
- `hardware_event` — `{action:"attach|detach|health", device, class, health, ts}`.
- `frame` — `{source, evidence_path, thumb, ts}`.
- `workspace_event` — `{target_id, manifest, git_state, ts}`.
- `model_event` — `{available:[...], selected, scope:"session|step", ts}`.
- `error` — `{where, message, ts}` (routing, gateway, trust-gate, transport).

Clients are pure functions of this stream: given the log + live tail, any frontend renders
the same four views. Adding a frontend never requires a new backend path.

## 7. Security

- **No trust decisions in the UI.** Every tool call shown already passed the meta-server
  gate; the UI cannot enable, promote, or invoke a `community` tool. Promotion proposals are
  read-only, routed to review (§0.4, `design/wq3806_limpet_architecture.md:246-249`).
- **Loopback only.** The WebSocket binds `127.0.0.1`; remote access is via SSH tunnel, not an
  exposed port. No master key, token, or model credential is ever embedded in the web
  surface or sent to a client — the client talks only to the local session service.
- **Mask before render.** Any event text is passed through the fleet masking filter before
  display; a result or log line that matches a credential pattern renders masked, same
  discipline as fleet command output.
- **Untrusted output rendered inert** (§4.2): no escape passthrough, no auto-fetch, no
  auto-exec from tool/target-derived text.

## 8. Persistence & replay

Because the UI and the evidence log consume the same event stream (§2), a session is
replayable: `notes/session.jsonl` + the referenced `evidence/` files, committed into
`workspace.git`, reconstruct exactly what the operator saw. This folds the chat transcript
into the signed evidence bundle (`design/wq3806_limpet_architecture.md:227-229`) with no
separate record — the conversation is part of the RE deliverable, and "what the agent did"
and "what the operator watched" are the same artifact.

## 9. Build order (for the provisioning ticket, not this one)

1. Session service: agent loop + event stream + `notes/session.jsonl` persistence (no UI).
2. Event protocol (§6) frozen as the client contract.
3. Textual TUI: chat + tool table + hardware + workspace panels against a real `core`-tier
   session (§3 primary). Demonstrable over SSH before any web work.
4. LiteLLM model picker from `/v1/models` + per-turn model attribution (§5).
5. Tauri web surface for the frame/KVM path (§3 opt-in), reusing the same event stream.
6. Replay mode: reopen a committed `session.jsonl` as a read-only trajectory (§8).

Step 3 is the "usable appliance" milestone and is reachable with the TUI alone — the
graphical surface is for frames, not for basic operation.

## 10. Open items / decisions deferred to provisioning

- Confirm the LiteLLM gateway endpoint and the key's model scope on the real Limpet box;
  populate the picker from the live `/v1/models` (do not assume `research/models.yaml` rows
  are all reachable from this appliance's key).
- Decide terminal image protocol support policy (sixel vs kitty) for the TUI frame preview
  based on the operator's actual terminal.
- Confirm whether the web surface is ever needed in practice, or whether KVM frames are
  better served by writing to `evidence/` and viewing out-of-band — if the latter, §3's
  web surface can be dropped entirely and the appliance stays TUI-only.
- Prior-art search for comparable agent-chat/tool-render UIs (Textual chat apps, MCP
  Inspector, Open WebUI tool rendering) could not be run this session: `fleet-search web`
  returned all engines blocked and `fleet-search github` returned 401 (see wq#2595 for the
  Brave key, and the missing GitHub token). Re-run before implementation to confirm the §3
  framework choice against current community practice.
