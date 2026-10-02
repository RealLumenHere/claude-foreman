# wq#3387 — FACT (Firmware Analysis and Comparison Tool) on Lumen

Why: wq#3387 asks for `fkie-cad/FACT_core` cloned, installed and its web UI verified
running, for automated firmware extraction + static analysis + diff. The root-free
half of this is done for real: `/home/lumen/bin/FACT_core` is a live clone (shallow,
`origin` = `fkie-cad/FACT_core.git`) and `/home/lumen/bin/venv-fact` is a live venv,
both deployed by this worker (git clone / python3 -m venv need no root). `--verify`
confirms that plus the remaining gap: no `fact`-labelled container, nothing on `:5000`
— see the live run below.

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
exists, a `MANIFEST.txt` byte-identical to this bundle's own landed in the install
dir (proof the clone step above actually ran, not just that the script exists),
`docker ps` shows a container whose name or image contains `fact`, and
`127.0.0.1:5000` answers 200/302. Live run today (2026-10-02) against the real
`/home/lumen/bin/FACT_core` and `/home/lumen/bin/venv-fact` this worker just
deployed: `VERIFY FAIL (2)` — only the container and web-UI checks miss.

Facts worth knowing:
- This build worker's sandbox has no root path; the root-elevation verb is
  hard-denied by `guard.sh` and `docker` is denied except `docker ps`/`docker logs`
  (`harness/worker-settings.json`), and FACT's installer additionally needs an
  interactive `newgrp docker` (or a reboot) to pick up new group membership. This
  worker deployed everything that needs no root — the clone, the venv, the
  manifest marker — live on Lumen (see above), proven against both a synthetic
  installed state and the real state. The root-requiring remainder
  (`pre_install.sh` installing Docker, `install.py` bringing up the containers +
  web UI) was **not executed** by this worker — same posture as
  `ops/wq3386-rizin-cutter`, `ops/wq2844-netdata-macs` and
  `ops/wq3300-bes-mcp-failover-log-redaction`. It needs an operator with sudo on
  Lumen; tracked as a non-blocking physical/operator task, not a ticket blocker.
- FACT's installer removes old Docker packages (`docker.io`, `docker-doc`,
  `docker-compose`, `podman-docker`, `containerd`, `runc`) before installing
  Docker CE from Docker's apt repo — worth knowing before running this on a node
  that already depends on one of those packages for something else.
- No reboot is required if `install.sh` is run as written: it uses the
  `newgrp docker <<END ... END` form from INSTALL.md so the new `docker` group
  membership applies inside the subshell that runs `install.py`.
- `FACT_INSTALL_DIR`, `FACT_VENV_DIR` and `FACT_PORT` env vars override the
  defaults (`/home/lumen/bin/FACT_core`, `/home/lumen/bin/venv-fact`, `5000`,
  relocated under `bin/` since that is where this fleet keeps hand-installed
  tools and where this build worker's sandbox is allowed to write); the test
  suite uses them to point `--verify` at fixtures instead of the real
  filesystem/network.
- Rollback: stop FACT's containers, `rm -rf /home/lumen/bin/FACT_core
  /home/lumen/bin/venv-fact`, remove the `lumen` user from the `docker` group if
  it should not otherwise have it.
