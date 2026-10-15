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
