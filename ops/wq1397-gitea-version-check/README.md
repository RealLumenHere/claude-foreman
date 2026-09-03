# wq#1397 — Gitea version check (CVE-2026-66877)

## Root gap

The fleet's Gitea (host Anubis, `http://100.121.17.5:3000`) was bumped to `1.27.3` to fix
`CVE-2026-66877` (minerva proposal `p-cve`, hand-carried into a ticket per
`ops/wq1396-minerva-proposal-promoter/README.md`), but nothing kept checking that it *stayed*
patched — a rollback, a host rebuild from an older snapshot, or a manual pull of an old
release would silently reintroduce the CVE with no signal.

Verified live at time of writing:

```
$ curl -s http://100.121.17.5:3000/api/v1/version
{"version":"1.27.3"}
```

## What this adds

`gitea_version_check.py` — queries the live instance's own version endpoint
(`GET /api/v1/version`, unauthenticated, read-only, no credential involved) and compares it
against the CVE-patched floor (`1.27.3` by default). Complements
`ops/wq859-renovate-gitea` (which bumps image/package tags *inside* repos) — this checks the
Gitea application binary itself.

Transition-based alerting (`kb/ISSUE-LOG.md` #10 pattern, same as
`ops/wq1298-gitleaks-detect`): only pages on a change in vulnerable/safe status via
`ntfy-send`, so a standing regression pages once and a fix gets an explicit recovery notice
instead of going quiet.

- `gitea-version-check.service` / `.timer` — daily oneshot, `OnBootSec=10min`,
  `OnUnitActiveSec=24h`.
- `install.sh` — idempotent deploy (`lumen-run start --root -- bash
  ops/wq1397-gitea-version-check/install.sh`).
- `test_gitea_version_check.py` — offline tests (mocked HTTP + ntfy, no network):
  `python3 test_gitea_version_check.py`.

## Usage

```
gitea_version_check.py run [--url URL] [--min-version X.Y.Z] [--state PATH] [--dry-run]
```

Exit 0 when the live version is at/above the floor, exit 1 when vulnerable or unreachable.
