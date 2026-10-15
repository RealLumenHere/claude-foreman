"""wq#3946 stage 4 helper: turn limpet/tool-catalog.json into an install manifest
for the ISO build.

The catalog (limpet/tool-catalog.json) records each tool's trust tier and, for
tools already verified on the reference Maksoor RE workstation, its on-disk
path -- but it does not record an apt/pip/cargo package name (no such field
exists in any entry as of catalog_version 0.1.0-seed). Guessing a package name
from a tool name would violate the no-guessing read doctrine, so this module
resolves a package the same way a human auditing the reference workstation
would: `dpkg -S <path>` for an apt-owned binary. A tool resolves to pip/cargo
only when its own catalog entry says so explicitly (none do yet); otherwise an
unresolved tool is reported, never silently skipped or fabricated.

Per the catalog's own trust_tiers doctrine (tool-catalog.json provenance.trust_tiers):
"core = proven on fleet, in base image; vetted = passed TOOL_REGISTRY go/no-go;
community = present/known but disabled until human promotion". The ISO only
bakes in core/vetted tools; community stays out until promoted, mirroring
limpet/registry/import_catalog.py's existing skip-by-design for unresolved
entries.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import pathlib
import subprocess
import sys

# This dir is not an importable package (the hyphen in "iso-builder"), so reach the
# sibling module through the repo root rather than a relative import.
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
from limpet import license_policy  # noqa: E402

DEFAULT_CATALOG = pathlib.Path(__file__).parent.parent / "tool-catalog.json"
BAKE_TIERS = ("core", "vetted")


@dataclasses.dataclass(frozen=True)
class Resolution:
    name: str
    trust: str
    method: str  # "apt" | "unresolved"
    package: str | None
    reason: str | None


def load_tools(catalog_path: pathlib.Path = DEFAULT_CATALOG) -> list[dict]:
    with open(catalog_path, encoding="utf-8") as fh:
        data = json.load(fh)
    return data["tools"] if isinstance(data, dict) else data


def _dpkg_query_owner(path: str, runner=subprocess.run) -> str | None:
    """Return the apt package owning `path`, or None if dpkg can't say so.

    Isolated behind `runner` so tests never need a real dpkg database.
    """
    try:
        proc = runner(
            ["dpkg", "-S", path], capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0 or not proc.stdout:
        return None
    # dpkg -S output: "<package>[,<package>...]: <path>"
    return proc.stdout.split(":", 1)[0].split(",")[0].strip() or None


def resolve(tool: dict, runner=subprocess.run) -> Resolution:
    name = tool["name"]
    trust = tool.get("trust", "community")
    path = tool.get("path")
    if trust not in BAKE_TIERS:
        return Resolution(name, trust, "unresolved", None,
                           f"trust tier '{trust}' not promoted for base image")
    if not license_policy.is_bundleable(tool):
        # wq#4048: the ISO is the redistribution act, so a tool the catalog marks
        # external-invoke or blocked-pending-permission must not be baked into it.
        return Resolution(name, trust, "unresolved", None,
                           "redistribution_posture "
                           f"'{tool['redistribution_posture']}' forbids bundling "
                           "into the ISO (tool-catalog.json license_risk_policy, "
                           "wq#3959/wq#4048)")
    if not path:
        return Resolution(name, trust, "unresolved", None,
                           "no verified path in catalog (import_catalog.py "
                           "skips these by design; same rule applies here)")
    package = _dpkg_query_owner(path, runner=runner)
    if package is None:
        return Resolution(name, trust, "unresolved", None,
                           f"dpkg does not know a package owning {path!r} on "
                           "this build host")
    return Resolution(name, trust, "apt", package, None)


def build_manifest(tools: list[dict], runner=subprocess.run) -> dict:
    resolutions = [resolve(t, runner=runner) for t in tools]
    apt_packages = sorted({r.package for r in resolutions if r.method == "apt"})
    unresolved = [dataclasses.asdict(r) for r in resolutions if r.method == "unresolved"]
    return {
        "apt_packages": apt_packages,
        "unresolved": unresolved,
        "totals": {
            "catalog": len(tools),
            "baked": len(apt_packages),
            "unresolved": len(unresolved),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=pathlib.Path, default=DEFAULT_CATALOG)
    parser.add_argument("--out", type=pathlib.Path, default=None,
                         help="write JSON manifest here instead of stdout")
    args = parser.parse_args()
    manifest = build_manifest(load_tools(args.catalog))
    text = json.dumps(manifest, indent=2, sort_keys=True)
    if args.out:
        args.out.write_text(text + "\n")
    else:
        print(text)


if __name__ == "__main__":
    main()
