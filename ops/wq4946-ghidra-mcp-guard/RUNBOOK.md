# ghidra-lumen behind lumen_mcp_guard (wq#4946)

Audit F6 (`/home/lumen/docs/security/mcp-audit-2026-11.md`, 2026-11-12): the local-scope `ghidra-lumen`
registration in `/data/lumen/archive/re` ran `/opt/ghidra-mcp/bridge-stdio.sh` directly, with all 222
bridge tools exposed. This bundle puts it behind the shared guard from `ops/wq4514-mcp-install-batch`.

## Deploy

```
ssh lumen@100.117.66.102 'cp -a /home/lumen/.local/share/mcp/_guard/lumen_mcp_guard.py /home/lumen/.local/share/mcp/_guard/lumen_mcp_guard.py.bak.$(date +%Y%m%d_%H%M%S) && install -m 0644 /home/lumen/foreman/ops/wq4514-mcp-install-batch/lumen_mcp_guard.py /home/lumen/.local/share/mcp/_guard/lumen_mcp_guard.py && bash /home/lumen/foreman/ops/wq4946-ghidra-mcp-guard/install.sh'
```

1. The shared guard must carry `--path-arg` (this branch). Copying only the guard file is enough: the
   other launchers do not use the new flag, so their behaviour is unchanged. Running the full
   wq4514 `install.sh` also works but reinstalls every server.
2. `install.sh` (this dir) writes `~/.local/share/mcp/ghidra-lumen/launch.sh`, backs up
   `~/.claude.json` to `/home/lumen/backups/claude.json.pre-wq4946.*`, re-registers `ghidra-lumen` at local
   scope for `/data/lumen/archive/re`, then runs `--verify`.
3. Sessions already open in `/data/lumen/archive/re` keep the old tool surface until restarted.

Rollback: `cd /data/lumen/archive/re && claude mcp remove -s local ghidra-lumen && claude mcp add -s local ghidra-lumen /opt/ghidra-mcp/bridge-stdio.sh`.

## Policy

Allowlist: `ghidra-allow.txt`, 202 of the 222 tools of the installed bridge. Fail closed: a tool a
later bridge release adds stays hidden until someone reviews it and adds a line.

Exposed: static analysis, listing, decompile, xrefs, search, data types, and the annotation tools
(rename, comment, retype, tag, bookmark). Those write only inside the Ghidra program database.
Also exposed: `emulate_function` and `emulate_hash_batch`. They run Ghidra's P-code emulator inside
the JVM with a step cap, not the target on the host.

Hidden (20):

| Tools | Why |
|---|---|
| `run_ghidra_script`, `run_script_inline`, `list_scripts` | Arbitrary Java on the host. The server also refuses them while `GHIDRA_MCP_ALLOW_SCRIPTS` is unset; the guard is the second layer. |
| `connect_instance`, `list_instances` | Switch the bridge to another Ghidra instance (UDS discovery), which may be a GUI Ghidra with scripts on and no file root. One driver per session (rea-mcp-eval sec 4). |
| `search_tools`, `check_tools`, `list_tool_groups`, `load_tool_group`, `unload_tool_group` | Lazy-load meta tools. `search_tools` returns names and descriptions of hidden tools. The launcher runs the bridge `--no-lazy`, so nothing needs them. |
| `export_program`, `archive_project`, `restore_project`, `create_project`, `import_program`, `delete_file` | Write or delete host paths. |
| `checkin_program`, `archive_ingest_function`, `archive_ingest_program` | Write to off-host services (shared Ghidra Server, bsim Postgres doc archive). |
| `suggest_field_names` | "AI-assisted": backend and egress not reviewed. |

Path pins (`--path-arg`): `load_program.file`, `import_file.file_path` and `open_project.path` must be
absolute paths whose realpath is under `/home/lumen/re` (which resolves to `/data/lumen/archive/re`).
The server enforces `GHIDRA_MCP_FILE_ROOT` too, but only when it was started by `run-headless.sh`, and
the bridge talks to whatever listens on `127.0.0.1:8089`.

Definition pins (`--pins`, wq#4941): `ghidra-pins.json` holds the sha256 of each of the 202 exposed tools,
cut from the 2026-11-12 security-gate baseline (`mcp-tool-pins-2026-11.json`, `ghidra-lumen@unguarded`).
A tool whose description or schema changes is hidden and refused, and the guard logs a `DRIFT` row.
After a bridge upgrade, regenerate the pins (security-gate `snap.py`) only once the new definitions
have been read. Then re-run the cross-server name check (wq#4945,
`ops/wq4945-mcp-name-collisions/README.md`): a new tool name that another server also exposes fails it.

Output passes through `/home/lumen/bin/mask-secrets`. If the masker fails, the result is withheld.
A hardcoded credential in a target binary will show up as `MASKED`. To see the real bytes, use
`read_memory` on its address and decode it outside the transcript.

## Evidence

- `test_ghidra_guard.py`: allowlist shape, generated launcher, the real bridge behind this branch's
  guard (202 listed, 10 denials by the guard, 2 allowed calls reach the bridge), and drift
  (`--list-backend`, `missing_upstream` empty).
- `probe_guarded.py LAUNCHER` is the acceptance probe. On the raw bridge it reports 14 FAILs, which is
  the check that the probe can fail. Its out-of-root inputs are paths that do not exist or hold
  nothing secret, so pointing it at an unguarded bridge is harmless.
