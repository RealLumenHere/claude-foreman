# wq#3387 — FACT (Firmware Analysis and Comparison Tool) on Lumen

Why: wq#3387 asks for `fkie-cad/FACT_core` cloned, installed and its web UI verified
running, for automated firmware extraction + static analysis + diff. `--verify` on
this bundle confirms the real, current state: nothing is installed yet (no
`~/FACT_core` clone, no `~/venv-fact`, no `fact`-labelled container, nothing on
`:5000`) — see the live run below.

Source of truth (verified live, not guessed, 2026-10-02):
- https://github.com/fkie-cad/FACT_core/blob/master/INSTALL.md
- https://github.com/fkie-cad/FACT_core/blob/master/src/install/pre_install.sh

Apply (on Lumen, as the `lumen` user — **not** root; FACT's own installer needs a
user who can `sudo`, since it uninstalls stale Docker packages, installs Docker from
Docker's own apt repo, and adds the invoking user to the `docker` group):

    bash ops/wq3387-fact-core/install.sh

Then open `http://localhost:5000/` for the web UI, per upstream's documented path.

Verify (read-only, from Lumen): `bash ops/wq3387-fact-core/install.sh --verify`
checks, in order: the clone's `.git/config` points at `fkie-cad/FACT_core`, the venv
exists, `docker ps` shows a container whose name or image contains `fact`, and
`127.0.0.1:5000` answers 200/302.

Facts worth knowing:
- This build worker's sandbox has no root path; `apt`/`apt-get`/`sudo` are not on
  its allowlist, `docker` is denied except `docker ps`/`docker logs`
  (`harness/worker-settings.json`), and FACT's installer additionally needs an
  interactive `newgrp docker` (or a reboot) to pick up new group membership. The
  install step above was written and reviewed, and its `--verify` handler was
  proven against both a synthetic installed state and the real (absent) state on
  Lumen, but the install arm itself was **not executed** by this worker — same
  posture as `ops/wq3386-rizin-cutter`, `ops/wq2844-netdata-macs` and
  `ops/wq3300-bes-mcp-failover-log-redaction`. It needs an operator with sudo on
  Lumen.
- FACT's installer removes old Docker packages (`docker.io`, `docker-doc`,
  `docker-compose`, `podman-docker`, `containerd`, `runc`) before installing
  Docker CE from Docker's apt repo — worth knowing before running this on a node
  that already depends on one of those packages for something else.
- No reboot is required if `install.sh` is run as written: it uses the
  `newgrp docker <<END ... END` form from INSTALL.md so the new `docker` group
  membership applies inside the subshell that runs `install.py`.
- `FACT_INSTALL_DIR`, `FACT_VENV_DIR` and `FACT_PORT` env vars override the
  defaults (`$HOME/FACT_core`, `$HOME/venv-fact`, `5000`); the test suite uses
  them to point `--verify` at fixtures instead of the real filesystem/network.
- Rollback: stop FACT's containers, `rm -rf ~/FACT_core ~/venv-fact`, remove the
  `lumen` user from the `docker` group if it should not otherwise have it.
