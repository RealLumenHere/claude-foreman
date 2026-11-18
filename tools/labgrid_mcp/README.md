# labgrid-mcp-launch (wq#4908)

Stdio launcher for `labgrid-mcp==0.1.5`. It closes the launcher-side findings of the wq#4875
pilot (`/home/lumen/docs/mcp-acquisition-analysis/labgrid-pilot-report.md`, F2/F4/F6/F7).
Installed as `/home/lumen/bin/labgrid-mcp-launch` (symlink to `labgrid_mcp_launch.py`).

| Check at `serve` | Refuses when |
|---|---|
| Identity | `WQ_AGENT` unset, not a registered wq session, and not the holder of a claimed ticket |
| One server per identity | another `labgrid-mcp` already holds `$XDG_RUNTIME_DIR/labgrid-mcp-launch/<id>.lock` |
| Coordinator | not a loopback or tailnet (100.64.0.0/10, fd7a:115c:a1e0::/48) IP literal or `localhost` |
| Acquire timeout | outside (0, 30] s |
| Venv | missing, not built from the current `requirements.lock`, or not labgrid-mcp 0.1.5 |

The server then runs with `LG_USERNAME=<WQ_AGENT>`, `LG_HOSTNAME=wq-<WQ_AGENT>` (labgrid owner
`wq-<id>/<id>`, which the lease janitor wq#4907 can map back to a wq worker) and every inherited
`LG_*`/`LABGRID_MCP_*` variable dropped.

| Profile | Env | Tools |
|---|---|---|
| observer | `LABGRID_MCP_READONLY=1`, `LABGRID_MCP_ALLOW=read` | 8 |
| analyst | `LABGRID_MCP_ALLOW=read,acquire,reservation,console` | 19 |
| bench | `LABGRID_MCP_ALLOW=read,acquire,reservation,power,io,mux,console` | 26 |

`ssh`, `flash`, `metadata` and `place_delete` are never allowed. The `acquire` category still
carries `release_place(kick=true)`, `release_from` and `allow_place`; those are denied by
`harness/action_gate.py`'s labgrid class (wq#4905), which only applies when the MCP server name
contains `labgrid`. `mcp-config` prints an entry named `labgrid`.

## Use

```
ssh lumen@100.117.66.102 '/home/lumen/bin/labgrid-mcp-launch install'
ssh lumen@100.117.66.102 '/home/lumen/bin/labgrid-mcp-launch mcp-config --profile analyst'
```

`install` builds `~/.local/share/labgrid-mcp-launch/venv-0.1.5` (override: `--venv` or
`LABGRID_MCP_LAUNCH_VENV`) with `pip --require-hashes --no-deps --only-binary :all:` from
`requirements.lock` (52 pins, CPython 3.14 x86_64). `check --profile <p>` runs every serve check
from inside a wq session and prints the env it would hand the server.

## Tests

```
python3 -m pytest -q tools/labgrid_mcp/tests
```

The live tests (tool counts per profile, one server per identity) skip unless the venv is built.
