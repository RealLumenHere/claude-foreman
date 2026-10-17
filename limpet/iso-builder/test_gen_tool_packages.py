import subprocess

import gen_tool_packages as g


def fake_runner(stdout_by_path, returncode=0):
    def run(cmd, capture_output, text, timeout):
        path = cmd[-1]
        out = stdout_by_path.get(path)
        if out is None:
            return subprocess.CompletedProcess(cmd, 1, "", "no path with that name\n")
        return subprocess.CompletedProcess(cmd, returncode, out, "")
    return run


def test_core_tool_with_known_package_resolves_to_apt():
    tool = {"name": "binwalk", "trust": "core", "path": "/usr/bin/binwalk"}
    runner = fake_runner({"/usr/bin/binwalk": "binwalk: /usr/bin/binwalk\n"})
    res = g.resolve(tool, runner=runner)
    assert res.method == "apt"
    assert res.package == "binwalk"


def test_community_tool_never_baked_regardless_of_path():
    tool = {"name": "fwtriage", "trust": "community", "path": "/usr/bin/fwtriage"}
    res = g.resolve(tool, runner=fake_runner({}))
    assert res.method == "unresolved"
    assert "not promoted" in res.reason


def test_core_tool_with_no_catalog_path_is_unresolved_not_guessed():
    tool = {"name": "mystery", "trust": "core", "path": None}
    res = g.resolve(tool, runner=fake_runner({}))
    assert res.method == "unresolved"
    assert "no verified path" in res.reason


def test_core_tool_whose_path_dpkg_does_not_own_is_unresolved():
    tool = {"name": "unblob", "trust": "core", "path": "/usr/local/bin/unblob"}
    res = g.resolve(tool, runner=fake_runner({}))
    assert res.method == "unresolved"
    assert "dpkg does not know" in res.reason


def test_core_tool_barred_from_bundling_is_not_baked_in(): # wq#4048
    tool = {"name": "fat2", "trust": "core", "path": "/usr/bin/fat2",
            "license_risk": "non-osi-commercial-restricted",
            "redistribution_posture": "external-invoke"}
    res = g.resolve(tool, runner=fake_runner({"/usr/bin/fat2": "fat2: /usr/bin/fat2\n"}))
    assert res.method == "unresolved"
    assert "forbids bundling" in res.reason


def test_core_tool_marked_bundle_still_resolves(): # wq#4048
    tool = {"name": "tshark", "trust": "core", "path": "/usr/bin/tshark",
            "license_risk": "copyleft-source-offer", "redistribution_posture": "bundle"}
    res = g.resolve(tool, runner=fake_runner({"/usr/bin/tshark": "tshark: /usr/bin/tshark\n"}))
    assert res.method == "apt"
    assert res.package == "tshark"


def test_declared_apt_package_wins_over_unowned_path(): # wq#4081
    tool = {"name": "rkdeveloptool", "trust": "core", "path": "/usr/local/sbin/rkdeveloptool",
            "install_method": "apt", "package": "rkdeveloptool"}
    res = g.resolve(tool, runner=fake_runner({}))
    assert res.method == "apt"
    assert res.package == "rkdeveloptool"


def test_declared_non_apt_method_is_unresolved_even_if_dpkg_owns_path(): # wq#4081
    tool = {"name": "unblob", "trust": "core", "path": "/usr/bin/unblob",
            "install_method": "pip", "package": "unblob"}
    res = g.resolve(tool, runner=fake_runner({"/usr/bin/unblob": "unblob: /usr/bin/unblob\n"}))
    assert res.method == "unresolved"
    assert "install_method 'pip'" in res.reason


def test_real_catalog_bakes_declared_apt_names_not_tool_names(): # wq#4081
    manifest = g.build_manifest(g.load_tools(), runner=fake_runner({}))
    assert {"heimdall-flash", "rkdeveloptool", "qemu-system-arm"} <= set(manifest["apt_packages"])
    assert "heimdall" not in manifest["apt_packages"]


def test_dpkg_query_owner_handles_multi_package_output():
    runner = fake_runner({"/usr/bin/x": "pkg-a, pkg-b: /usr/bin/x\n"})
    assert g._dpkg_query_owner("/usr/bin/x", runner=runner) == "pkg-a"


def test_build_manifest_partitions_real_catalog_and_matches_totals():
    tools = g.load_tools()
    manifest = g.build_manifest(tools, runner=fake_runner({}))
    assert manifest["totals"]["catalog"] == len(tools)
    assert manifest["totals"]["baked"] + len(
        [t for t in tools if t.get("trust") not in g.BAKE_TIERS]
    ) <= manifest["totals"]["catalog"]
    # Every community-trust tool in the real catalog must be unresolved -- never baked.
    unresolved_names = {u["name"] for u in manifest["unresolved"]}
    for t in tools:
        if t.get("trust") == "community":
            assert t["name"] in unresolved_names


# --- wq#4097: cargo method (binwalk v3) ---
def _cargo_tool(**kw):
    t = {"name": "binwalk", "trust": "core", "path": "/usr/local/bin/binwalk",
         "install_method": "cargo", "package": "binwalk", "version": "3.1.0"}
    t.update(kw)
    return t


def test_cargo_tool_with_recipe_and_pin_resolves():
    res = g.resolve(_cargo_tool())
    assert (res.method, res.package, res.version) == ("cargo", "binwalk", "3.1.0")


def test_cargo_tool_without_version_pin_is_unresolved():
    assert g.resolve(_cargo_tool(version=None)).method == "unresolved"


def test_cargo_version_must_be_plain_x_y_z():
    assert g.resolve(_cargo_tool(version="3.1.0; rm -rf /")).method == "unresolved"
    assert g.resolve(_cargo_tool(version="^3.1")).method == "unresolved"


def test_cargo_crate_without_verified_recipe_is_unresolved_not_built():
    res = g.resolve(_cargo_tool(name="othertool", package="othertool"))
    assert res.method == "unresolved" and "recipe" in res.reason


def test_manifest_carries_cargo_crate_and_its_runtime_apt():
    m = g.build_manifest([_cargo_tool()])
    assert m["cargo_crates"] == [{"name": "binwalk", "crate": "binwalk", "version": "3.1.0",
                                  "build_apt": ["libfontconfig1-dev"],
                                  "runtime_apt": ["libfontconfig1", "libfreetype6"], "bin": "binwalk"}]
    assert "libfontconfig1" in m["apt_packages"] and "libfontconfig1-dev" not in m["apt_packages"]
    assert m["totals"]["cargo"] == 1


def test_real_catalog_ships_binwalk_v3_via_cargo():
    tools = {t["name"]: t for t in g.load_tools()}
    res = g.resolve(tools["binwalk"])
    assert (res.method, res.version) == ("cargo", "3.1.0")
    assert tools["binwalk"]["install_method"] != "pip"  # PyPI binwalk is a dead 2.1.0 stub


# --- wq#4116: pip method, PyPI release (unblob, frida-tools) ---
def _pip_tool(**kw):
    t = {"name": "unblob", "trust": "core", "path": "/usr/local/bin/unblob",
         "install_method": "pip", "package": "unblob", "version": "26.6.4"}
    t.update(kw)
    return t


def test_pip_tool_with_recipe_and_pin_resolves():
    res = g.resolve(_pip_tool())
    assert (res.method, res.package, res.version) == ("pip", "unblob", "26.6.4")


def test_pip_tool_without_version_pin_is_unresolved():
    assert g.resolve(_pip_tool(version=None)).method == "unresolved"


def test_pip_version_must_be_plain_x_y_z():
    assert g.resolve(_pip_tool(version="26.6.4; rm -rf /")).method == "unresolved"
    assert g.resolve(_pip_tool(version="^26.6")).method == "unresolved"


def test_pip_package_without_verified_recipe_is_unresolved_not_installed():
    res = g.resolve(_pip_tool(name="othertool", package="othertool"))
    assert res.method == "unresolved" and "recipe" in res.reason


def test_manifest_carries_pip_package_and_its_sha256():
    m = g.build_manifest([_pip_tool()])
    assert m["pip_packages"] == [{
        "name": "unblob", "package": "unblob", "version": "26.6.4",
        "sha256": g.PIP_RECIPES["unblob"]["sha256"], "bin": "unblob", "runtime_apt": [],
    }]
    assert m["totals"]["pip"] == 1


# --- wq#4116: pip method, git+ source (mtkclient, edl) ---
_COMMIT = "7cb40e727727dfe3faa065eefb29b16ed29bbdf1"


def _pip_git_tool(**kw):
    t = {"name": "mtkclient", "trust": "core", "path": "/opt/re-venv/bin/mtk",
         "install_method": "pip", "package": "git+https://github.com/bkerler/mtkclient",
         "commit": _COMMIT}
    t.update(kw)
    return t


def test_pip_git_tool_with_recipe_and_commit_pin_resolves():
    res = g.resolve(_pip_git_tool())
    assert (res.method, res.package, res.commit) == (
        "pip-git", "git+https://github.com/bkerler/mtkclient", _COMMIT)


def test_pip_git_tool_without_commit_pin_is_unresolved():
    res = g.resolve(_pip_git_tool(commit=None))
    assert res.method == "unresolved" and "40-hex commit" in res.reason


def test_pip_git_commit_must_be_40_hex_not_a_branch():
    assert g.resolve(_pip_git_tool(commit="main")).method == "unresolved"
    assert g.resolve(_pip_git_tool(commit=_COMMIT + "x")).method == "unresolved"


def test_pip_git_tool_without_verified_recipe_is_unresolved_not_installed():
    res = g.resolve(_pip_git_tool(name="othertool",
                                   package="git+https://github.com/example/othertool"))
    assert res.method == "unresolved" and "recipe" in res.reason


def test_manifest_carries_pip_git_package_and_its_commit():
    m = g.build_manifest([_pip_git_tool()])
    assert m["pip_git_packages"] == [{
        "name": "mtkclient", "url": "git+https://github.com/bkerler/mtkclient",
        "commit": _COMMIT, "bin": "mtk", "build_apt": [], "runtime_apt": ["libxcb-cursor0"],
    }]
    assert "libxcb-cursor0" in m["apt_packages"]
    assert m["totals"]["pip"] == 1


# --- wq#4116: binary method (ghidra) ---
def _binary_tool(**kw):
    t = {"name": "ghidra", "trust": "core", "path": "/opt/ghidra/support/analyzeHeadless",
         "install_method": "binary", "package": "ghidra_12.1.4_PUBLIC_20260921.zip"}
    t.update(kw)
    return t


def test_binary_tool_with_recipe_resolves():
    res = g.resolve(_binary_tool())
    assert (res.method, res.package) == ("binary", "ghidra_12.1.4_PUBLIC_20260921.zip")


def test_binary_tool_without_verified_recipe_is_unresolved_not_fetched():
    res = g.resolve(_binary_tool(package="ghidra_99.0.0_PUBLIC_99999999.zip"))
    assert res.method == "unresolved" and "recipe" in res.reason


def test_binary_tool_with_no_declared_artifact_is_unresolved():
    res = g.resolve(_binary_tool(package=None))
    assert res.method == "unresolved"


def test_manifest_carries_binary_artifact_and_its_sha256():
    m = g.build_manifest([_binary_tool()])
    assert m["binary_artifacts"] == [{
        "name": "ghidra", "artifact": "ghidra_12.1.4_PUBLIC_20260921.zip",
        "url": g.BINARY_RECIPES["ghidra_12.1.4_PUBLIC_20260921.zip"]["url"],
        "sha256": g.BINARY_RECIPES["ghidra_12.1.4_PUBLIC_20260921.zip"]["sha256"],
        "extract_to": "/opt/ghidra", "runtime_apt": ["openjdk-21-jre-headless"],
    }]
    assert "openjdk-21-jre-headless" in m["apt_packages"]
    assert m["totals"]["binary"] == 1


# --- wq#4116: the real catalog's 5 core tools that build_manifest previously
# reported unresolved (wq#4097's build_manifest run, 2026-10-16) now resolve ---
def test_real_catalog_resolves_the_five_previously_unresolved_core_tools():
    tools = {t["name"]: t for t in g.load_tools()}
    for name, method in (("unblob", "pip"), ("frida", "pip"),
                          ("mtkclient", "pip-git"), ("edl", "pip-git"),
                          ("ghidra", "binary")):
        res = g.resolve(tools[name])
        assert res.method == method, f"{name}: expected {method}, got {res.method} ({res.reason})"
