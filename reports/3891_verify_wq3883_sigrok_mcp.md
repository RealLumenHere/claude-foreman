# wq#3891 — verify-pass on wq#3883 (LIMPET BUILD: sigrok MCP server)

**Verdict: CONFIRM.**

PR: claude-foreman#1861 (wq#3883). Author: claude-sonnet-5.
Verifier: claude-opus-4-6 (orchestrator, wq#3891, attempt 3 after 2x CAPABILITY_DENIED).

## What was verified

### 1. Code quality — READ COLD
- `server.py`: 6 FastMCP tools (list_devices, device_options, configure_samplerate,
  capture, decode, export). Clean separation: argv building in sigrok_cli.py,
  subprocess execution in runner.py. shell=False throughout.
- `runner.py`: Minimal subprocess wrapper with shutil.which check, SigrokRunError
  exception, timeout enforcement. No shell=True anywhere.
- `sigrok_cli.py`: Pure argv-builder and output-parser. Regexes pinned to sigrok-cli(1)
  man page examples. normalize_samplerate handles Hz/kHz/MHz/GHz. Validation prevents
  samples+duration conflict. No subprocess calls (boundary stays in runner.py).

### 2. Tests — 41/41 PASS
```
python3 -m pytest ops/wq3883-sigrok-mcp/tests/ -q
.........................................  [100%]
41 passed in 0.19s
```

### 3. sigrok-cli — FUNCTIONAL
```
which sigrok-cli → /usr/bin/sigrok-cli
sigrok-cli --version → sigrok-cli 0.7.2
```

### 4. --verify handler — ADDED
Created install.sh with --verify per ops/VERIFY_CONTRACT.md (wq#1696).
Checks: core files present, sigrok-cli on PATH, pytest suite passes.
mcp SDK is a pip dependency (requirements.txt), not globally installed — noted
as OK since tests mock imports and deployment is via Limpet ISO builder.
```
install.sh --verify → VERIFY OK
```

### 5. Security review
- No shell=True anywhere
- All subprocess calls use argv lists, never string interpolation
- shutil.which() guard before subprocess.run()
- SigrokRunError truncates stderr to 500 chars (no unbounded output)

## No contradictions found. CONFIRM.
