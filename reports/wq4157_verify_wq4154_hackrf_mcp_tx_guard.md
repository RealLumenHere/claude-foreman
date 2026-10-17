# wq#4157 — verify-pass of wq#4154 LIMPET: hackrf-mcp SDR plugin (RX-only v1, pin d9fa2ee6)

**Verdict: CONFIRMED.** The mandatory claim under test — `hackrf_transfer
direction="tx"` is refused at the Limpet proxy layer, not merely ungated — holds
against every bypass I could construct, including ones not in the shipped test
suite. Two real-but-non-blocking gaps are noted below; neither lets TX through.

Diff verified: commit `b591e95e` on this branch (10 files, `plugins/limpet-plugin-hackrf-mcp/`).
PR URL in the ticket (`.../lumen/claude-foreman/pulls/2102`) 404s against this gitea
instance under both `claude-foreman` and `foreman` repo paths — noting per instinct 8
rather than guessing a third path; the merged commit in this repo is the authoritative
artifact and is what I verified.

## What wq#4154 claimed

- Wraps `thehappydinoa/hackrf-mcp` (MIT), pinned to `d9fa2ee6…` (no tags/PyPI).
- `hackrf_transfer` accepts `direction="rx"|"tx"`; upstream only warns about TX in
  its docstring (`destructiveHint=False`). A standalone stdio proxy,
  `mcp/hackrf_tx_guard.py`, sits in front of the real server and answers any
  `tools/call` for `hackrf_transfer` with `arguments.direction=="tx"` locally with
  an error, never forwarding it upstream. Everything else is forwarded unmodified.
- `hackrf_clock`/`hackrf_operacake`/`hackrf_biast` are energize-gated (not proxy-refused);
  read-only + offline IQ tools are ungated.
- Declares a plugin conflict with `gnuradio-mcp` (same physical HackRF device).

## What I checked independently of the diff's own narration

1. **Upstream source, not the PR's paraphrase of it.** Cloned
   `github.com/thehappydinoa/hackrf-mcp` fresh and checked out `d9fa2ee6bf58acd28e50a1cad98a4458e0e2ef67`
   directly (not trusting the prior eval report's line citations blindly). Confirmed:
   - `direction: Literal["rx", "tx"]` at `server.py:161`, and the implementation is
     `if direction == "rx": ... else: # tx branch` (`server.py:224-233`) — so upstream's
     own Python logic would treat *any* non-`"rx"` string as TX if it ever reached the
     function body. This makes the guard's exact-match-on-`"tx"` check load-bearing: if
     upstream accepted an unvalidated non-`"rx"`/non-`"tx"` value and routed it to TX, a
     guard that only checks `== "tx"` would miss it.
   - The three firmware-touching tools (`hackrf_debug`, `hackrf_cpldjtag`, `hackrf_spiflash`)
     are confirmed commented out at `server.py:710-845`, never registered.
   - The "one hackrf_* command at a time" docstring claim is real, at `server.py:16-19`
     (cited as `:25-26` in the PR — off by a few lines in the instructions block, same
     file, not a fabricated claim).
   - `cli.py:31-51`: `subprocess.run([path, *args], ...)` — argv list, no `shell=True`,
     matching the "no shell injection" claim.
2. **Closed the exact gap the point above opens: is `Literal["rx","tx"]` actually
   enforced before upstream's function body runs, or is it just a type hint?** Installed
   `fastmcp` (the dependency this server actually uses, `pyproject.toml`) into a scratch
   venv and called the decorated tool through `FastMCP.call_tool` with
   `direction="TX"`, `"transmit"`, `"txx"`, `""`, `"tx "`. All five were **rejected by
   pydantic** (`literal_error`, "Input should be 'rx' or 'tx'") before the function body
   ever executed; `"rx"` and `"tx"` were the only two values that reached it. So the
   theoretical "any non-rx value hits the else/TX branch" issue in upstream's own code is
   unreachable via the real MCP protocol — pydantic's `Literal` validation closes it one
   layer below the guard. This directly falsifies the most promising bypass idea
   (case variants / junk values slipping past the guard's exact string match and still
   transmitting upstream): they're blocked a second time, independently, by upstream
   itself.
3. **Adversarially probed `is_blocked_tx_call`/`run_proxy` beyond the shipped test
   file** (`tests/test_hackrf_tx_guard.py` — ran as shipped, 7/7 pass), using a fake
   upstream that echoes a reachability marker so a bypass is directly observable:
   - Duplicate JSON key (`"direction": "rx", "direction": "tx"` on one line) — Python's
     `json.loads` keeps the last value, guard correctly sees `"tx"` and blocks it. No
     bypass.
   - `arguments.direction = "TX"` (case variant) — the guard's exact-match check does
     *not* block it and forwards the line upstream. In the real deployment this is
     caught by point 2 above (pydantic rejects it with a validation error, no CLI
     invocation, no RF). Confirmed non-exploitable, but it means the guard's own
     TX-refusal is not actually what stops a case-variant call in production — upstream's
     schema validation is doing that job. Worth knowing if upstream ever loosens its
     type to `str`.
   - A batched JSON-RPC array (`[{...tools/call hackrf_transfer tx...}]`) and a bare
     JSON scalar (`42`) on one line — both are valid JSON, so they skip the
     `except json.JSONDecodeError` branch, and `is_blocked_tx_call(request)` calls
     `request.get(...)` on a `list`/`int`, raising an unhandled `AttributeError` that
     propagates out of `run_proxy` and kills the whole proxy process.

## Observations noted, not contradictions

- **The batch/scalar crash is a real, reproduced defect, but it is not a TX bypass —
  it fails closed.** The malformed line is never forwarded upstream (the crash happens
  before that write), so no transmission occurs; the entire plugin (RX included) just
  goes down and needs a restart. This also makes the module docstring's claim "every
  other line … is forwarded unmodified in both directions" literally false for
  non-object JSON, and it's a gap in the test suite (every shipped test uses a
  single-dict message). Given MCP's current protocol revision has no batching and a
  compliant client never emits a top-level array/scalar JSON-RPC message on this
  transport, this requires a non-conforming or actively adversarial client to trigger —
  but it's a one-line fix (`isinstance(request, dict)` guard, or catch `AttributeError`
  alongside `JSONDecodeError`) and worth a fast-follow given it's in the hot path of
  every tool call.
- **The `gnuradio-mcp` conflict is declared one-directionally.** `hackrf-mcp`'s
  `limpet-plugin.toml` adds `[dependencies].conflicts = ["gnuradio-mcp"]`, but
  `plugins/limpet-plugin-gnuradio-mcp/limpet-plugin.toml` has no reciprocal
  `conflicts = ["hackrf-mcp"]`. Whether this matters depends on whether Limpet's plugin
  registry checks conflicts symmetrically (any active plugin's conflict list vs. the
  activating plugin) or only checks the *activating* plugin's own list — if the latter,
  activating `hackrf-mcp` first then `gnuradio-mcp` second would not be blocked, even
  though the two must never hold the device open together per both plugins' own stated
  rationale. **I could not verify which it is**: `limpet/*.py` (the registry) is outside
  this worker's allowed directories, same access gap already logged in
  `reports/wq4145_limpet_verify_hackrf_mcp_eval_buspirate_mcp.md` for a different file in
  the same tree. Flagging as unverified rather than guessing (instinct: never guess a
  root cause, verify it) — whoever wires `limpet/manifest.py`'s conflict check should
  confirm symmetry or add the reciprocal entry to `gnuradio-mcp`.
- `[plugin].manifest_version`, `category = "sdr"`, `[health].check` follow the same
  not-yet-wired-into-the-registry TOML shape already noted as a batch-wide (not
  wq#4154-specific) convention gap in `reports/wq4150_verify_wq4139_limpet_embedded_debugger_mcp.md`.
  Not a regression introduced here.
- `1d50:6089` (operating) / `1d50:cc15` (DFU) are the correct, publicly documented
  HackRF One USB VID:PIDs; matches both this plugin's udev rules and the prior
  wq#4145 report's citation.

## What would have broken this verdict (and didn't)

- If a case-variant, duplicate-key, or malformed-but-valid direction value had reached
  the real `hackrf_transfer` CLI invocation with `-t` (TX), that would contradict the
  "mandatory" refusal — none did; every path either gets blocked at the guard or
  rejected by pydantic one layer down.
- If the shipped test suite were padded to look thorough without covering the actual
  filter logic, the adversarial probes above (duplicate keys, batch arrays, case
  variants) would have found a gap the tests missed — they did find one (the crash),
  but it's a robustness bug, not a safety-claim violation.
- If the upstream pin, tool list, or commented-out dangerous-tool claims were invented,
  a fresh clone-and-grep at the pinned SHA would have caught it (instinct 8) — it
  didn't; `server.py:161,224-233,710-845` and `cli.py:31-51` all match the PR's citations.

No escalation to judgment needed — board this as verified, with the two noted gaps
(proxy crash on non-dict JSON; one-directional plugin conflict) called out for a
fast-follow, not blocking this ticket.
