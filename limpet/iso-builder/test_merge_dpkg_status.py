"""wq#4558 / wq#4557: three-way merge of the live layer's dpkg database.

The live layer ships its own full var/lib/dpkg/status and sits on top of the overlay,
so without a merge it shadows the target layer: packages the build adds look
uninstalled and packages it removes look installed.

Falsifiable (wq#2630): each rule has a test that fails if that rule is dropped (see
the PR body for the mutation checks), including both fail-closed branches.
"""
import pytest

import iso_layers as il


def st(pkg, version="1.0", *, status="install ok installed", prio="optional", depends=None, arch="amd64"):
    lines = [f"Package: {pkg}", f"Status: {status}", f"Priority: {prio}", "Section: misc",
             f"Architecture: {arch}", f"Version: {version}"]
    if depends:
        lines.append(f"Depends: {depends}")
    return "\n".join(lines)


def doc(*stanzas):
    return "\n\n".join(stanzas) + "\n"


def names(text):
    return {k[0] for k in il.parse_stanzas(text)}


def version(text, pkg):
    block = next(v for k, v in il.parse_stanzas(text).items() if k[0] == pkg)
    return next(line.split(": ", 1)[1] for line in block.splitlines() if line.startswith("Version:"))


def test_package_added_by_the_build_is_visible():
    out = il.merge_dpkg_status(doc(st("a")), doc(st("a")), doc(st("a"), st("tmux")))
    assert "tmux" in names(out)


def test_package_removed_by_the_build_is_dropped_even_though_live_lists_it():
    out = il.merge_dpkg_status(doc(st("a"), st("cups")), doc(st("a"), st("cups")), doc(st("a")))
    assert "cups" not in names(out)
    assert "a" in names(out)


def test_untouched_package_takes_the_live_layers_metadata():
    out = il.merge_dpkg_status(doc(st("apt", prio="required")), doc(st("apt", prio="important")), doc(st("apt", prio="required")))
    assert "Priority: important" in out


def test_untouched_package_the_live_layer_removed_is_dropped():
    out = il.merge_dpkg_status(doc(st("dracut-core")), doc(st("other")), doc(st("dracut-core")))
    assert "dracut-core" not in names(out)


def test_package_the_build_changed_takes_the_modified_stanza():
    out = il.merge_dpkg_status(doc(st("jq", "1.8.1-4ubuntu1")), doc(st("jq", "1.8.1-4ubuntu1", prio="important")),
                               doc(st("jq", "1.8.1-4ubuntu2")))
    assert version(out, "jq") == "1.8.1-4ubuntu2"


def test_package_changed_by_both_build_and_live_layer_fails_closed():
    with pytest.raises(il.MergeConflict, match="changed by the build and by the live layer"):
        il.merge_dpkg_status(doc(st("jq", "1")), doc(st("jq", "2")), doc(st("jq", "3")))


def test_live_only_package_is_kept():
    out = il.merge_dpkg_status(doc(st("a")), doc(st("a"), st("casper")), doc(st("a")))
    assert "casper" in names(out)


def test_live_only_package_depending_on_a_removed_package_fails_closed():
    with pytest.raises(il.MergeConflict, match="removed"):
        il.merge_dpkg_status(doc(st("cups")), doc(st("cups"), st("live-tool", depends="cups")), doc())


def test_live_only_dependency_with_a_surviving_alternative_is_fine():
    out = il.merge_dpkg_status(doc(st("cups"), st("lpr")), doc(st("cups"), st("lpr"), st("live-tool", depends="cups | lpr")),
                               doc(st("lpr")))
    assert {"lpr", "live-tool"} <= names(out)


def test_added_by_build_but_different_in_live_layer_fails_closed():
    with pytest.raises(il.MergeConflict, match="added by the build"):
        il.merge_dpkg_status(doc(st("a")), doc(st("a"), st("x", "1")), doc(st("a"), st("x", "2")))


def test_same_package_in_two_architectures_is_kept_apart():
    base = doc(st("libc6", arch="amd64"), st("libc6", arch="i386"))
    out = il.merge_dpkg_status(base, base, doc(st("libc6", arch="amd64")))
    assert [k[1] for k in il.parse_stanzas(out)] == ["amd64"]


def test_merge_is_deterministic_and_idempotent_from_the_originals():
    base, live, mod = doc(st("a"), st("cups")), doc(st("a", prio="x"), st("cups"), st("casper")), doc(st("a"), st("tmux"))
    first = il.merge_dpkg_status(base, live, mod)
    assert il.merge_dpkg_status(base, live, mod) == first


def test_malformed_input_fails_closed():
    with pytest.raises(il.MergeConflict, match="without a Package"):
        il.merge_dpkg_status("Status: install ok installed\n", "", "")
    with pytest.raises(il.MergeConflict, match="duplicate"):
        il.merge_dpkg_status(doc(st("a"), st("a")), "", "")


def auto(pkg):
    return f"Package: {pkg}\nArchitecture: amd64\nAuto-Installed: 1"


def test_extended_states_follow_the_merged_status():
    status = il.merge_dpkg_status(doc(st("a"), st("cups")), doc(st("a"), st("cups")), doc(st("a"), st("socat")))
    out = il.merge_extended_states(doc(auto("cups")), doc(auto("cups")), doc(auto("socat")), status)
    assert names(out) == {"socat"}  # the build's new mark is kept, the removed package's mark is dropped


def test_extended_states_empty_when_nothing_is_marked():
    assert il.merge_extended_states("", "", "", doc(st("a"))) == ""


def test_cli_writes_the_merged_file(tmp_path):
    for name, text in (("base", doc(st("a"), st("cups"))), ("live", doc(st("a"), st("cups"), st("casper"))), ("mod", doc(st("a"), st("tmux")))):
        (tmp_path / name).write_text(text)
    out = tmp_path / "out"
    il.main(["merge-dpkg-status", "--base", str(tmp_path / "base"), "--live", str(tmp_path / "live"),
             "--modified", str(tmp_path / "mod"), "--out", str(out)])
    assert names(out.read_text()) == {"a", "tmux", "casper"}


def test_cli_conflict_exits_nonzero_and_writes_nothing(tmp_path):
    for name, text in (("base", doc(st("jq", "1"))), ("live", doc(st("jq", "2"))), ("mod", doc(st("jq", "3")))):
        (tmp_path / name).write_text(text)
    out = tmp_path / "out"
    with pytest.raises(SystemExit) as exc:
        il.main(["merge-dpkg-status", "--base", str(tmp_path / "base"), "--live", str(tmp_path / "live"),
                 "--modified", str(tmp_path / "mod"), "--out", str(out)])
    assert exc.value.code == 1
    assert not out.exists()


def test_extended_states_drop_marks_for_packages_the_merged_status_does_not_list():
    status = doc(st("a"))
    out = il.merge_extended_states(doc(auto("a")), doc(auto("a"), auto("ghost")), doc(auto("a")), status)
    assert names(out) == {"a"}
