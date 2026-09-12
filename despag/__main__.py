"""wq#1805: `python3 -m despag` runs the discovery/harness/model surface on
127.0.0.1:$DESPAG_PORT (default 8790). Loopback only — TLS/exposure is a
deploy-time concern, not this process's.

wq#1810: `python3 -m despag reap` runs the workspace TTL reaper once and exits
(despag.workspace.reap_stale) — the CLI half of C.3's "reaper runs at
session-create time and via a despag reap CLI"; the other half runs inline in
despag.workspace.ensure_workspace on every session's first task.
"""
import asyncio
import os
import sys


def _reap() -> int:
    from despag.backing.control_sqlite import ControlStore
    from despag.workspace import data_dir, reap_stale

    control = ControlStore(str(data_dir() / "control.db"))
    reaped = asyncio.run(reap_stale(control))
    for sid in reaped:
        print(sid)
    print(f"reaped {len(reaped)} workspace(s)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "reap":
        sys.exit(_reap())
    import uvicorn
    port = int(os.environ.get("DESPAG_PORT", "8790"))
    uvicorn.run("despag.app:app", host="127.0.0.1", port=port, log_level="info")
