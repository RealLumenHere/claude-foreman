"""wq#3946 stage 4 helper: turn limpet/tool-catalog.json into an install manifest
for the ISO build.

The catalog (limpet/tool-catalog.json) records each tool's trust tier, its
on-disk path on the reference Maksoor RE workstation where verified, and (since
wq#4081) a verified install_method + package. A declared apt package is used
as-is. A declared `cargo` method (wq#4097) needs a pinned crate version AND a
verified build recipe in CARGO_RECIPES; anything else non-apt is reported
unresolved, because this stage installs apt packages and recipe-backed cargo
crates only. An entry with no declared apt package falls back to
`dpkg -S <path>`, the way a human auditing the reference workstation would.
Guessing a package name from a tool name would violate the no-guessing read
doctrine, so an unresolved tool is reported, never silently skipped or
fabricated.

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
import re
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

# wq#4097: cargo crates that have a VERIFIED build recipe. Built in an
# ubuntu:<release> container (matches the ISO's glibc) so no Rust toolchain
# lands in the ISO layer; only the binary is copied in. A crate absent from this
# table is reported unresolved, never built on a guess.
#   binwalk 3.1.0: built + smoke-tested (scan and -e extraction) 2026-10-16 in
#   ubuntu:26.04 with cargo/rustc 1.93.1 in 51s; `cargo install --locked` fails
#   without libfontconfig1-dev (yeslogic-fontconfig-sys); ldd needs only libs
#   already in the Xubuntu 26.04 desktop manifest (libfontconfig1, libfreetype6 ...).
CARGO_RECIPES = {
    "binwalk": {"build_apt": ["libfontconfig1-dev"],
                "runtime_apt": ["libfontconfig1", "libfreetype6"],
                "bin": "binwalk"},
}
_CRATE_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


@dataclasses.dataclass(frozen=True)
class Resolution:
    name: str
    trust: str
    method: str  # "apt" | "cargo" | "unresolved"
    package: str | None
    reason: str | None
    version: str | None = None  # pinned crate version (cargo only)


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
    method = tool.get("install_method")
    declared = tool.get("package")
    if method == "apt" and declared:
        return Resolution(name, trust, "apt", declared, None)
    if method == "cargo":
        version = tool.get("version")
        if not (declared and _CRATE_RE.match(declared) and version and _VERSION_RE.match(version)):
            return Resolution(name, trust, "unresolved", None,
                              f"install_method 'cargo' needs a crate name and a pinned "
                              f"X.Y.Z version (got package {declared!r}, version {version!r})")
        if declared not in CARGO_RECIPES:
            return Resolution(name, trust, "unresolved", None,
                              f"no verified cargo build recipe for crate {declared!r} "
                              "(add one to CARGO_RECIPES after an actual build; never guessed)")
        return Resolution(name, trust, "cargo", declared, None, version)
    if method and method != "apt":
        return Resolution(name, trust, "unresolved", None,
                           f"install_method '{method}' (package {declared!r}): "
                           "stage4 installs apt packages only")
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
    cargo = [r for r in resolutions if r.method == "cargo"]
    cargo_crates = [{"name": r.name, "crate": r.package, "version": r.version,
                     "build_apt": CARGO_RECIPES[r.package]["build_apt"],
                     "runtime_apt": CARGO_RECIPES[r.package]["runtime_apt"],
                     "bin": CARGO_RECIPES[r.package]["bin"]} for r in cargo]
    apt_packages = sorted({r.package for r in resolutions if r.method == "apt"}
                          | {p for c in cargo_crates for p in c["runtime_apt"]})
    unresolved = [dataclasses.asdict(r) for r in resolutions if r.method == "unresolved"]
    return {
        "apt_packages": apt_packages,
        "cargo_crates": cargo_crates,
        "unresolved": unresolved,
        "totals": {
            "catalog": len(tools),
            "baked": len(apt_packages),
            "unresolved": len(unresolved),
            "cargo": len(cargo_crates),
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
