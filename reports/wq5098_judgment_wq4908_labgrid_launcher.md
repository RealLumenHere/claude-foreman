# wq#5098 judgment: wq#4908 labgrid-mcp launcher

Verdict: **CONTRADICTED** (escalated verify, run at claude-opus-5-5). Verifier: orch-verify-batch-5099,
2026-11-18 22:15 PST. Subject: PR #2640 (398792a7), `tools/labgrid_mcp/labgrid_mcp_launch.py`.
Parent wq#4908 is reopened.

## What holds

| Claim | Probe | Result |
|---|---|---|
| Refuses without identity | `env -u WQ_AGENT labgrid-mcp-launch check` | REFUSED |
| Identity must be live in wq | `WQ_AGENT=not-a-session-zz` | REFUSED |
| Unique per-worker LG_* | two live sessions | `LG_USERNAME`/`LG_HOSTNAME` = `<id>` / `wq-<id>`, distinct |
| Inherited LG_*/LABGRID_MCP_* scrubbed | `LABGRID_MCP_ALLOW=ssh,flash LG_USERNAME=evil` | ALLOW=`read`, USERNAME=own id |
| One server per identity | serve A, then serve B with the same id | B REFUSED (flock) |
| Different identities coexist | serve A (id 1) + check id 2 | ok |
| Coordinator loopback/tailnet only | `--coordinator 192.168.8.160:20408` | REFUSED |
| Acquire timeout <= 30 | `--acquire-timeout 120` | REFUSED |

## Defects

**D1 (blocking): the venv is not hermetic, and in a clean env the server does not start.**
`cmd_install` runs pip with the caller's environment. In a Commander shell
`PYTHONPATH=/home/lumen/opt/mcp-proxy/lib`, so `pip install --require-hashes --no-deps -r requirements.lock`
treats `mcp==1.30.0`, `idna`, `typing-extensions` and others as already satisfied and does not put
them in the venv. Result, reproduced on 2026-11-18:

- `env -u PYTHONPATH .../venv-0.1.5/bin/labgrid-mcp` → `ModuleNotFoundError: No module named 'mcp'`.
- `pytest tests` with PYTHONPATH unset: 4 live tests fail (`test_live_profile_tool_counts[*]`,
  `test_live_one_server_per_identity`, `queue.Empty`); with the Commander PYTHONPATH: 51 pass. The
  4908 author's "51 tests pass" only held because of the leaked path.
- `pip check` in that venv: idna and typing-extensions missing.

The same leak exists at run time. `build_env` drops only `LG_*`/`LABGRID_MCP_*`, so `PYTHONPATH` reaches
the labgrid-mcp child (`/proc/<pid>/environ` showed it). Even a correct venv then imports
`mcp`/`anyio`/`pydantic` from `/home/lumen/opt/mcp-proxy/lib` ahead of the hash-locked copies, which
defeats the `--require-hashes` pin.

Fix: run the pip subprocess and exec the server with `PYTHONPATH`, `PYTHONHOME`, `PYTHONSTARTUP`,
`PYTHONUSERBASE` removed (and `PYTHONNOUSERSITE=1`), plus a `pip check` in `check_venv`. Add a test that
installs and serves with a poisoned PYTHONPATH.

The verifier rebuilt the venv with `env -u PYTHONPATH ... install --force`: `pip check` clean, 51/51
pass in a clean env. That fixes the venv on disk now, not the code.

**D2 (moderate): the per-identity lock is keyed on an env-controlled directory.**
`hold_identity_lock` puts the flock under `$XDG_RUNTIME_DIR` or `/tmp`. With serve A holding
`/tmp/labgrid-mcp-launch/<id>.lock`, `XDG_RUNTIME_DIR=/tmp/xdg-bypass check` under the same id returned
`ok`. A systemd-run worker (`/run/user/1000`) and a Commander shell (no XDG → `/tmp`) differ in exactly
this way, so pilot F2 (two servers, one identity, both own the device) is reachable. Fix: use a fixed
path (`/run/user/<uid>/labgrid-mcp-launch` from `os.getuid()`, falling back to a fixed dir under
`~/.local/state`), never one taken from the environment.

## Not defects

- Any registered session can claim another session's id via WQ_AGENT; the flock stops a
  concurrent duplicate. Accepted for single-user Lumen and noted only.
