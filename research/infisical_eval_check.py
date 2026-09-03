#!/usr/bin/env python3
"""wq#1303 — verifies the factual premise of INFISICAL_EVAL.md still holds:
Vaultwarden (not flat .secrets/ files) is the fleet's existing secret-of-record,
and no Infisical deployment already exists to duplicate."""
import subprocess
import sys

FLEET_ROOT = "/home/lumen"


def grep_count(pattern: str, root: str) -> int:
    result = subprocess.run(
        ["grep", "-rIl", pattern, root,
         "--exclude-dir=.git", "--exclude-dir=node_modules",
         "--exclude-dir=_runs", "--exclude-dir=events", "--exclude-dir=logs"],
        capture_output=True, text=True,
    )
    return len([l for l in result.stdout.splitlines() if l])


def main() -> int:
    checks = []

    vw_migrator_exists = subprocess.run(
        ["test", "-f", "/home/lumen/bin/vw-migrate-secrets"]
    ).returncode == 0
    checks.append(("vw-migrate-secrets present (fleet already has a vault loader)", vw_migrator_exists))

    vw_hits = grep_count("vaultwarden", "/home/lumen/foreman/reports")
    checks.append((f"vaultwarden referenced in ops reports (found in {vw_hits} file(s))", vw_hits > 0))

    infisical_hits = grep_count("infisical", "/home/lumen/foreman") + grep_count("infisical", "/home/lumen/bin")
    checks.append((f"no existing Infisical deployment/config in fleet (found {infisical_hits} file(s))", infisical_hits == 0))

    ok = True
    for label, passed in checks:
        print(f"{'PASS' if passed else 'FAIL'}: {label}")
        ok = ok and passed
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
