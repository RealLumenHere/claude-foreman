# wq#2585 — redeploy token-engine to pick up wq#2566/wq#2567

wq#2578's verify-pass ran the new `check_deploy_drift.py` probe (wq#2567) live
against the running daemon and got `repo == installed`, both at main's tip
(`4d6b9c9`), but `running self_sha256=None`: the daemon process predated the
`ping` self-fingerprint field entirely, so it also predated wq#2566's
`SOCK_DIR` x-ACL fix and wq#2561's socket ACL. Same drift class as ISSUE-LOG
rows 595/695/697/698 and the wq#2333 redeploy in this same directory tree —
merged is not deployed until the unit restarts.

## What was done

Redeployed through the sanctioned root path, detached via `lumen-run` per
instinct 5, rather than restarting the unit directly:

    /home/lumen/bin/lumen-run start --root -- \
        bash /home/lumen/foreman/token-engine/install.sh /home/lumen/foreman/token-engine

`install.sh` is idempotent (`token-engine/install.sh:12-13`) and ends in a
`systemctl restart token-engine.service`, so the running process now loads the
file the installer just wrote.

## Evidence

Before this run (wq#2578, quoted in the ticket): `repo==installed`
(`4d6b9c9`), `running self_sha256=None`.

After this run, `check_deploy_drift.py` (`token-engine/check_deploy_drift.py`):

```
repo:      6fb2080e598e5ce4e95ff278fc4a880c393b7bd7909c2417468e2e7157d3d98d
installed: 6fb2080e598e5ce4e95ff278fc4a880c393b7bd7909c2417468e2e7157d3d98d
running:   6fb2080e598e5ce4e95ff278fc4a880c393b7bd7909c2417468e2e7157d3d98d
OK: repo == installed == running
```

Exit code 0. `systemctl status token-engine` confirms a fresh process
(`Active: active (running) since Fri 2026-09-25 04:26:34 UTC`, `Main PID:
353483`), i.e. the restart the installer performed, not a stale process that
happened to already match.
