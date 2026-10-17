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
