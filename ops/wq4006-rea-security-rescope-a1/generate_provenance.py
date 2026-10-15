#!/usr/bin/env python3
"""wq#4006 — generate PROVENANCE.json for a pinned rea-agents install.

Records what /opt/ghidra-mcp/6.0.0/PROVENANCE already records for that tool:
exact version, npm registry integrity hash, install method, and when it was
captured. Run on the box that has the real npm install (install.sh does
this; it is not run from a sandboxed BUILD worker, which has no network/npm
access to the live ~/.npm-global tree).

Usage: generate_provenance.py <package> <version> <out_path>
"""
import datetime
import json
import subprocess
import sys


def main() -> int:
    if len(sys.argv) != 4:
        print(__doc__, file=sys.stderr)
        return 2
    package, version, out_path = sys.argv[1], sys.argv[2], sys.argv[3]
    spec = f"{package}@{version}"

    integrity = subprocess.run(
        ["npm", "view", spec, "dist.integrity"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    tarball = subprocess.run(
        ["npm", "view", spec, "dist.tarball"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    record = {
        "package": package,
        "version": version,
        "integrity": integrity,
        "tarball": tarball,
        "install_method": "npm install -g (global, ~/.npm-global prefix)",
        "captured_at": datetime.datetime.now(datetime.timezone.utc)
        .isoformat(timespec="seconds"),
        "ticket": "wq#4006",
        "self_update_blocked_by": "bin/rea-guard",
    }
    if not integrity or not tarball:
        print(f"ERR: npm returned empty integrity/tarball for {spec}", file=sys.stderr)
        return 1

    with open(out_path, "w") as fh:
        json.dump(record, fh, indent=2, sort_keys=True)
        fh.write("\n")
    print(f"wrote {out_path} for {spec}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
