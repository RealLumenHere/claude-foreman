# wq#3386 — Rizin + Cutter RE framework on Lumen

Why: Cutter is Rizin's official GUI (a radare2 fork). The ticket asks for both on
Lumen; neither was present (`ops/wq2095-re-bench-tools` only lists radare2/ghidra/
cutter on maksoor, the RE workstation — not Lumen, and not rizin anywhere).

Apply (as root on Lumen; needrestart must be list-only — the installer checks):

    sudo bash ops/wq3386-rizin-cutter/install.sh

Verify (read-only, from Lumen): `bash ops/wq3386-rizin-cutter/install.sh --verify`

Facts worth knowing:
- This build worker's sandbox has no root path and `apt`/`apt-get`/`sudo` are not on
  its allowlist (`harness/worker-settings.json` has no `apt*` entry; `guard.sh`
  hard-denies the `sudo` verb for every worker). The install step above was written
  and reviewed but not executed by this worker — it needs an operator with root on
  Lumen, same posture as `ops/wq2844-netdata-macs` and `ops/wq3300-bes-mcp-failover-log-redaction`.
- Both packages are in the stock Ubuntu/Debian repos; no extra PPA or keyring needed.
- Rollback: `apt-get remove --purge rizin cutter`.
