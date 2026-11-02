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

# wq#4116: PyPI-hosted pip packages with a VERIFIED release. sha256 is the
# published digest of the exact wheel/sdist this build host installs (PyPI
# JSON API, checked against manylinux_2_17_x86_64 for a wheel, sdist
# otherwise), so a pip-side supply-chain substitution fails sha256 verification
# before anything lands in the chroot -- same guarantee driver_hook's
# dkms-sources.pin gives DKMS builds. "bin" is the console-script entry point
# stage4c checks for after install.
#   unblob 26.6.4: PyPI manylinux_2_17_x86_64 wheel, verified 2026-10-17.
#   frida-tools 14.11.0: catalog's old "17.18.0" pin does not exist on PyPI --
#   that number is frida-tools' pinned `frida` core dependency version
#   (pyproject: frida>=17.10.0,<18.0.0), not frida-tools' own release. 14.11.0
#   is frida-tools' real latest sdist (PyPI JSON API, verified 2026-10-17; see
#   kb/ISSUE-LOG.md). Corrected in tool-catalog.json alongside this recipe.
PIP_RECIPES = {
    "unblob": {"sha256": "0fc16e9c7dc92fcf86b8b61475700c9dd16e1dddacf34467574ea965a5d3584e",
               "bin": "unblob", "runtime_apt": []},
    "frida-tools": {"sha256": "b8c772e59502be43b76df9feccadd5d342b5ca325066dd0c2384e63fa88da921",
                     "bin": "frida", "runtime_apt": []},
}

# wq#4116: pip-from-git packages with a VERIFIED commit (no branches, mirrors
# driver_hook/dkms-sources.pin's own "pin <module> <url> <40-hex-commit>" rule).
# The commit itself is pinned on the catalog entry (tool-catalog.json's
# "commit" field), validated by _resolve_pip below; this table only carries
# the install recipe (runtime/build apt deps, verified console-script name).
#   mtkclient HEAD 7cb40e72..., edl HEAD 1cda1a61...: both `git ls-remote`,
#   2026-10-17 (neither project cuts PyPI releases -- see each entry's
#   install_evidence). libxcb-cursor0 is mtkclient's documented Ubuntu runtime
#   dep for its PySide6 import (README-INSTALL.md); liblzma-dev is edl's
#   documented apt dep (README.md) for building pylzma if no wheel matches.
PIP_GIT_RECIPES = {
    "mtkclient": {"bin": "mtk", "runtime_apt": ["libxcb-cursor0"], "build_apt": []},
    "edl": {"bin": "edl", "runtime_apt": [], "build_apt": ["liblzma-dev"]},
}

# wq#4116: sha256-verified binary release artifacts (ghidra). Digest is
# GitHub's own release-asset digest (releases/tags/<tag> API, attested at
# upload time), checked again by curl+sha256sum on the build host before
# unzip -- same "verify before it touches the chroot" rule as the cargo/pip
# recipes above. java_min is Ghidra's own Ghidra/application.properties
# application.java.min, which is why openjdk-21-jre-headless is the paired
# runtime_apt entry.
#   ghidra 12.1.4 (Ghidra_12.1.4_build): GitHub releases API, 2026-10-17.
BINARY_RECIPES = {
    "ghidra_12.1.4_PUBLIC_20260921.zip": {
        "url": "https://github.com/NationalSecurityAgency/ghidra/releases/download/"
               "Ghidra_12.1.4_build/ghidra_12.1.4_PUBLIC_20260921.zip",
        "sha256": "ddac49f903da9d5bac833e5cc79395098b9c33cfd3279be5f31bd00387d2d4db",
        "extract_to": "/opt/ghidra",
        "runtime_apt": ["openjdk-21-jre-headless"],
        # wq#4809: ghidraRun unpacked fine but was never linked onto PATH --
        # stage4d now symlinks /usr/local/bin/<bin> -> <extract_to>/<bin> for
        # every binary artifact that declares one.
        "bin": "ghidraRun",
    },
    # wq#4734: jadx release zip has no single top-level wrapper directory (its
    # entries are LICENSE, README.md, bin/, lib/ directly) -- sha256 is GitHub's
    # own release-asset digest, re-checked by curl+sha256sum on the build host,
    # 2026-10-27.
    "jadx-1.5.6.zip": {
        "url": "https://github.com/skylot/jadx/releases/download/v1.5.6/jadx-1.5.6.zip",
        "sha256": "545ea2be9c242511bc145755cf4bda2485ade42966e096f8b4d3da2a230e8974",
        "extract_to": "/opt/jadx",
        "runtime_apt": ["openjdk-21-jre-headless"],
        "bin": None,
    },
}

# wq#4734: tools with no packaged release -- fetched as a sha256-pinned GitLab
# archive of a single commit (same "verify before it touches the chroot" rule
# as BINARY_RECIPES), because the catalog's 'commit' field pins the exact
# revision rather than trusting a moving branch. "archive_name" is the repo
# slug GitLab uses to name the generated tarball
# (<repo>/-/archive/<commit>/<archive_name>-<commit>.tar.gz).
#   searchsploit (gitlab.com/exploit-database/exploitdb): apt's 'exploitdb'
#   package doesn't exist on Ubuntu 26.04 (Kali-only), and the project ships no
#   releases -- it's a bash script plus a CSV database, updated by commit, not
#   by tag. Pinned to GitLab HEAD f08907b1 as of 2026-10-27 (api/v4
#   .../repository/commits); sha256 is this build host's own curl+sha256sum of
#   that commit's -/archive endpoint, confirmed to contain the 'searchsploit'
#   script at the archive root. No extra runtime apt: the script only shells
#   out to grep/which, both already in the base image.
SOURCE_BUILD_RECIPES = {
    "searchsploit": {
        "archive_name": "exploitdb",
        "sha256": "370b8e481e70183c41fab2c0ffd487982c6a481909be31ca452ee1f60f7d36be",
        "extract_to": "/opt/exploitdb",
        "bin_src": "searchsploit",
        "bin": "/usr/bin/searchsploit",
        "runtime_apt": [],
    },
}
_CRATE_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


@dataclasses.dataclass(frozen=True)
class Resolution:
    name: str
    trust: str
    method: str  # "apt" | "cargo" | "pip" | "pip-git" | "binary" | "source-build" | "unresolved"
    package: str | None
    reason: str | None
    version: str | None = None  # pinned crate/pip version (cargo, pip only)
    commit: str | None = None  # pinned 40-hex commit (pip-git only)


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


def _resolve_pip(name: str, trust: str, declared: str | None,
                  version: str | None, commit: str | None) -> Resolution:
    if not declared:
        return Resolution(name, trust, "unresolved", None,
                           "install_method 'pip' needs a package name")
    if declared.startswith("git+"):
        if not (commit and _COMMIT_RE.match(commit)):
            return Resolution(name, trust, "unresolved", None,
                               f"install_method 'pip' from {declared!r} needs a pinned "
                               "40-hex commit in the catalog's 'commit' field (git+ URLs "
                               "are never installed off a branch, wq#4116)")
        if name not in PIP_GIT_RECIPES:
            return Resolution(name, trust, "unresolved", None,
                               f"no verified pip-git build recipe for {name!r} "
                               "(add one to PIP_GIT_RECIPES after an actual install; "
                               "never guessed)")
        return Resolution(name, trust, "pip-git", declared, None, commit=commit)
    if not (version and _VERSION_RE.match(version)):
        return Resolution(name, trust, "unresolved", None,
                           f"install_method 'pip' needs a pinned X.Y.Z version "
                           f"(got {version!r})")
    if declared not in PIP_RECIPES:
        return Resolution(name, trust, "unresolved", None,
                           f"no verified pip recipe for package {declared!r} "
                           "(add one to PIP_RECIPES after an actual install; never guessed)")
    return Resolution(name, trust, "pip", declared, None, version=version)


def _resolve_source_build(name: str, trust: str, declared: str | None,
                           commit: str | None) -> Resolution:
    if not declared:
        return Resolution(name, trust, "unresolved", None,
                           "install_method 'source-build' needs a repo url")
    if not (commit and _COMMIT_RE.match(commit)):
        return Resolution(name, trust, "unresolved", None,
                           f"install_method 'source-build' from {declared!r} needs a pinned "
                           "40-hex commit in the catalog's 'commit' field (no branches, "
                           "same rule as pip-git, wq#4116)")
    if name not in SOURCE_BUILD_RECIPES:
        return Resolution(name, trust, "unresolved", None,
                           f"no verified source-build recipe for {name!r} "
                           "(add one to SOURCE_BUILD_RECIPES after an actual sha256-verified "
                           "fetch; never guessed)")
    return Resolution(name, trust, "source-build", declared, None, commit=commit)


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
    if method == "pip":
        return _resolve_pip(name, trust, declared, tool.get("version"), tool.get("commit"))
    if method == "source-build":
        return _resolve_source_build(name, trust, declared, tool.get("commit"))
    if method == "binary":
        if not declared:
            return Resolution(name, trust, "unresolved", None,
                               "install_method 'binary' needs a package (release artifact filename)")
        if declared not in BINARY_RECIPES:
            return Resolution(name, trust, "unresolved", None,
                               f"no verified binary recipe for artifact {declared!r} "
                               "(add one to BINARY_RECIPES after an actual sha256-verified "
                               "fetch; never guessed)")
        return Resolution(name, trust, "binary", declared, None)
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

    pip = [r for r in resolutions if r.method == "pip"]
    pip_packages = [{"name": r.name, "package": r.package, "version": r.version,
                      "sha256": PIP_RECIPES[r.package]["sha256"],
                      "bin": PIP_RECIPES[r.package]["bin"],
                      "runtime_apt": PIP_RECIPES[r.package]["runtime_apt"]} for r in pip]

    pip_git = [r for r in resolutions if r.method == "pip-git"]
    pip_git_packages = [{"name": r.name, "url": r.package, "commit": r.commit,
                          "bin": PIP_GIT_RECIPES[r.name]["bin"],
                          "build_apt": PIP_GIT_RECIPES[r.name]["build_apt"],
                          "runtime_apt": PIP_GIT_RECIPES[r.name]["runtime_apt"]} for r in pip_git]

    binary = [r for r in resolutions if r.method == "binary"]
    binary_artifacts = [{"name": r.name, "artifact": r.package,
                          "url": BINARY_RECIPES[r.package]["url"],
                          "sha256": BINARY_RECIPES[r.package]["sha256"],
                          "extract_to": BINARY_RECIPES[r.package]["extract_to"],
                          "runtime_apt": BINARY_RECIPES[r.package]["runtime_apt"],
                          "bin": BINARY_RECIPES[r.package].get("bin")} for r in binary]

    source_build = [r for r in resolutions if r.method == "source-build"]
    source_build_tools = [{"name": r.name, "repo": r.package, "commit": r.commit,
                            "archive_name": SOURCE_BUILD_RECIPES[r.name]["archive_name"],
                            "sha256": SOURCE_BUILD_RECIPES[r.name]["sha256"],
                            "extract_to": SOURCE_BUILD_RECIPES[r.name]["extract_to"],
                            "bin_src": SOURCE_BUILD_RECIPES[r.name]["bin_src"],
                            "bin": SOURCE_BUILD_RECIPES[r.name]["bin"],
                            "runtime_apt": SOURCE_BUILD_RECIPES[r.name]["runtime_apt"]}
                           for r in source_build]

    apt_packages = sorted({r.package for r in resolutions if r.method == "apt"}
                          | {p for c in cargo_crates for p in c["runtime_apt"]}
                          | {p for c in pip_packages for p in c["runtime_apt"]}
                          | {p for c in pip_git_packages for p in c["runtime_apt"]}
                          | {p for c in binary_artifacts for p in c["runtime_apt"]}
                          | {p for c in source_build_tools for p in c["runtime_apt"]})
    unresolved = [dataclasses.asdict(r) for r in resolutions if r.method == "unresolved"]
    return {
        "apt_packages": apt_packages,
        "cargo_crates": cargo_crates,
        "pip_packages": pip_packages,
        "pip_git_packages": pip_git_packages,
        "binary_artifacts": binary_artifacts,
        "source_build_tools": source_build_tools,
        "unresolved": unresolved,
        "totals": {
            "catalog": len(tools),
            "baked": len(apt_packages),
            "unresolved": len(unresolved),
            "unresolved_core": len([r for r in resolutions
                                     if r.method == "unresolved" and r.trust == "core"]),
            "cargo": len(cargo_crates),
            "pip": len(pip_packages) + len(pip_git_packages),
            "binary": len(binary_artifacts),
            "source_build": len(source_build_tools),
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
