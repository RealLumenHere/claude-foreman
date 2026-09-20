## What
wq#2192: the wq#1613 state verifier overwrote live facts with values it never observed (bes.status RESTORED -> `inactive`; `*_version` / `os` -> `Linux`).

## Why
- `probe_os_kernel` ran `uname -s` for every predicate except `kernel`; the registry routed `ollama_version`, `*_version` and `os` to it.
- `probe_service_state` ran `systemctl is-active <fact value>` on free-text `status` / `*_status` values ("RESTORED" -> `inactive`).
- `reconcile` supersedes on any mismatch, so both wrote.

## How
- Probes return unknown (no write) for anything they cannot observe: `os_kernel` answers only `kernel`; `service_state` probes only unit-name values and only confirms.
- Registry drops the probe from `os`, `ollama_version`, `*_version`.
- `reconcile` treats a blank observation as unknown.
- `repair_clobbered_facts.py` closes surviving clobber rows and reopens the fact each superseded; idempotent; `install.sh` runs it after deploying.

## Testing
- `pytest ops/wq1613-minerva-verifier-registry`: 28 passed (13 new).
- Live `minerva.db`: repair applied to 9 rows (bes.status back to RESTORED, apep/seshat netdata_version, hathor/sia/isis/thoth status, lumen.logrotate_status, thor.magic_keyboard_status); 0 open `verify:os_kernel` / `verify:service_state` rows remain. isis/lumen ollama_version were already fixed by wq#1999-cleanup.

## Risk / rollback
Deployed copies (`/home/lumen/bin/lumen-verify`, `/home/lumen/minerva/verify/registry.yaml`) still run the old code until `install.sh` is run post-merge; the 06:00 sweep can re-clobber until then, and `install.sh` re-runs the repair. Rollback: revert the PR; reopened predecessor facts keep their original values and provenance.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
