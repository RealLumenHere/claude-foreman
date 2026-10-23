"""wq#4558: stage 11 repacks the live layer with a dpkg database merged against the
modified target layer (research: reports/wq4557_live_layer_dpkg_consistency.md).

unsquashfs/mksquashfs/dpkg-query are faked (they need root or real squashfs files);
the merge itself (iso_layers.py) and the shell logic are the real code.

Falsifiable (wq#2630): the default-layer test fails if the repack is skipped, the
"is itself the live layer" and "no live layer" tests fail if the guards are removed
(they would run mksquashfs), the conflict test fails if the merge result is ignored,
and the idempotence test fails if the merge reads a previous repack.
"""
import os
import stat
import subprocess
import tarfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
BUILD = HERE / "build.sh"


def stanza(pkg, version="1.0", status="install ok installed", prio="optional"):
    return (f"Package: {pkg}\nStatus: {status}\nPriority: {prio}\nSection: misc\n"
            f"Architecture: amd64\nVersion: {version}")


def status_file(*stanzas):
    return "\n\n".join(stanzas) + "\n"


def auto(pkg):
    return f"Package: {pkg}\nArchitecture: amd64\nAuto-Installed: 1\n"


FAKE_UNSQUASHFS = r"""#!/usr/bin/env python3
import os, shutil, sys
fix = os.environ["FAKE_FIX"]
a = sys.argv[1:]
name = lambda p: os.path.basename(p)[: -len(".squashfs")]
if a[0] == "-d":
    shutil.copytree(os.path.join(fix, name(a[2])), a[1])
elif a[0] == "-cat":
    src = os.path.join(fix, name(a[1]), a[2])
    if not os.path.exists(src):
        sys.exit(1)
    sys.stdout.write(open(src).read())
elif a[0] == "-s":
    print("Compression xz\nBlock size 131072")
"""

FAKE_MKSQUASHFS = r"""#!/usr/bin/env python3
import os, sys, tarfile
src, dst, *rest = sys.argv[1:]
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write("mksquashfs " + " ".join(rest) + "\n")
with tarfile.open(dst, "w") as tar:
    tar.add(src, arcname=".")
"""

FAKE_DPKG_QUERY = r"""#!/usr/bin/env python3
import re, sys
adm = [a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--admindir=")][0]
rows = []
for blk in open(adm + "/status").read().split("\n\n"):
    p = re.search(r"^Package: (.+)$", blk, re.M)
    v = re.search(r"^Version: (.+)$", blk, re.M)
    if p:
        rows.append((p.group(1), v.group(1)))
for p, v in sorted(rows):
    print(f"{p}\t{v}")
"""


def _exec(path, body):
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _setup(tmp_path, *, base, live, modified, live_ext="", base_ext="", mod_ext="", with_live_squashfs=True,
           save_modified=True):
    bin_dir, fix = tmp_path / "bin", tmp_path / "fix"
    casper, changes, work = tmp_path / "casper", tmp_path / "changes", tmp_path / "work"
    bin_dir.mkdir()
    _exec(bin_dir / "unsquashfs", FAKE_UNSQUASHFS)
    _exec(bin_dir / "mksquashfs", FAKE_MKSQUASHFS)
    _exec(bin_dir / "dpkg-query", FAKE_DPKG_QUERY)
    _write(fix / "minimal.standard/var/lib/dpkg/status", base)
    _write(fix / "minimal.standard/var/lib/apt/extended_states", base_ext)
    _write(fix / "minimal.standard.live/var/lib/dpkg/status", live)
    _write(fix / "minimal.standard.live/var/lib/apt/extended_states", live_ext)
    _write(fix / "minimal.standard.live/usr/share/live-marker", "live\n")
    casper.mkdir()
    (casper / "minimal.standard.squashfs").write_bytes(b"")
    if with_live_squashfs:
        (casper / "minimal.standard.live.squashfs").write_bytes(b"")
    _write(casper / "minimal.standard.live.manifest.full",
           "casper\t1.0\nsnap:firefox\tstable\t8107\nsnap:ubuntu-desktop-bootstrap\t26.04/stable\t589\n")
    # what stage11 has already written for the (new) target layer
    _write(changes / "casper/minimal.standard.manifest.full", "a\t1.0\nsnap:firefox\tstable\t8107\n")
    work.mkdir()
    if save_modified:
        (work / "modified.dpkg-status").write_text(modified)
        (work / "modified.extended_states").write_text(mod_ext)
    return bin_dir, fix, casper, changes, work


def _run(tmp_path, layer="minimal.standard", **kw):
    bin_dir, fix, casper, changes, work = _setup(tmp_path, **kw)
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "FAKE_FIX": str(fix), "FAKE_LOG": str(tmp_path / "fake.log"),
           "LIMPET_ISO_WORKDIR": str(work), "BUILD": str(BUILD), "HOME": str(tmp_path)}
    result = subprocess.run(
        ["bash", "-c", 'source "$BUILD"; repack_live_layer "$@"', "_", layer, str(changes), str(casper)],
        env=env, capture_output=True, text=True)
    return result, changes, work


def _live_files(changes):
    out = {}
    pack = changes / "casper/minimal.standard.live.squashfs"
    if pack.exists():
        with tarfile.open(pack) as tar:
            for member in tar.getmembers():
                if member.isfile():
                    out[member.name.lstrip("./")] = tar.extractfile(member).read().decode()
    return out


BASE = status_file(stanza("a"), stanza("cups"), stanza("jq", "1"))
LIVE = status_file(stanza("a", prio="important"), stanza("cups"), stanza("jq", "1"), stanza("casper"))
MOD = status_file(stanza("a"), stanza("jq", "2"), stanza("tmux"))


def test_live_layer_is_repacked_with_a_merged_database(tmp_path):
    result, changes, _ = _run(tmp_path, base=BASE, live=LIVE, modified=MOD)

    assert result.returncode == 0, result.stderr
    files = _live_files(changes)
    status = files["var/lib/dpkg/status"]
    assert "Package: tmux" in status                      # added by the build: now visible
    assert "Package: cups" not in status                  # removed by the build: no longer "installed"
    assert "Package: casper" in status                    # live-only package kept
    assert "Version: 2" in status                         # the build's jq bump
    assert "Priority: important" in status                # live layer metadata kept for untouched packages
    assert files["usr/share/live-marker"] == "live\n"     # the rest of the live layer is untouched


def test_manifests_and_size_are_written_and_snap_records_survive(tmp_path):
    result, changes, _ = _run(tmp_path, base=BASE, live=LIVE, modified=MOD)

    assert result.returncode == 0, result.stderr
    full = (changes / "casper/minimal.standard.live.manifest.full").read_text().splitlines()
    assert "tmux\t1.0" in full and "casper\t1.0" in full
    assert not any(line.startswith("cups") for line in full)
    assert "snap:ubuntu-desktop-bootstrap\t26.04/stable\t589" in full   # not dpkg: carried over
    delta = (changes / "casper/minimal.standard.live.manifest").read_text()
    assert "+casper" in delta and "+snap:ubuntu-desktop-bootstrap" in delta
    assert "+snap:firefox" not in delta                   # already in the (new) parent: delta is against it
    assert (changes / "casper/minimal.standard.live.size").read_text().strip().isdigit()


def test_repack_keeps_the_original_compression_settings(tmp_path):
    result, _, _ = _run(tmp_path, base=BASE, live=LIVE, modified=MOD)

    assert result.returncode == 0, result.stderr
    log = (tmp_path / "fake.log").read_text()
    assert "-comp xz" in log and "-b 131072" in log and "-xattrs" in log and "-noappend" in log


def test_extended_states_follow_the_merged_database(tmp_path):
    result, changes, _ = _run(tmp_path, base=BASE, live=LIVE, modified=MOD,
                              base_ext=auto("cups"), live_ext=auto("cups"), mod_ext=auto("tmux"))

    assert result.returncode == 0, result.stderr
    ext = _live_files(changes)["var/lib/apt/extended_states"]
    assert "Package: tmux" in ext and "cups" not in ext


def test_merge_conflict_fails_closed_and_ships_no_live_layer(tmp_path):
    conflicting = status_file(stanza("a"), stanza("jq", "3"))
    result, changes, _ = _run(tmp_path, base=status_file(stanza("a"), stanza("jq", "1")),
                              live=status_file(stanza("a"), stanza("jq", "2")), modified=conflicting)

    assert result.returncode != 0
    assert "dpkg status merge" in result.stderr
    assert not (changes / "casper/minimal.standard.live.squashfs").exists()


def test_target_layer_that_is_itself_the_live_layer_is_a_no_op(tmp_path):
    result, changes, _ = _run(tmp_path, layer="minimal.standard.live", base=BASE, live=LIVE, modified=MOD)

    assert result.returncode == 0, result.stderr
    assert "nothing separate to repack" in result.stderr
    assert not (changes / "casper/minimal.standard.live.squashfs").exists()


def test_base_iso_without_a_live_layer_is_a_no_op(tmp_path):
    result, changes, _ = _run(tmp_path, base=BASE, live=LIVE, modified=MOD, with_live_squashfs=False)

    assert result.returncode == 0, result.stderr
    assert "nothing to repack" in result.stderr
    assert not (changes / "casper/minimal.standard.live.squashfs").exists()


def test_missing_saved_target_status_fails_closed(tmp_path):
    result, changes, _ = _run(tmp_path, base=BASE, live=LIVE, modified=MOD, save_modified=False)

    assert result.returncode != 0
    assert "modified.dpkg-status missing" in result.stderr
    assert not (changes / "casper/minimal.standard.live.squashfs").exists()


def test_rebuild_is_idempotent_because_it_merges_from_the_originals(tmp_path):
    first, changes, work = _run(tmp_path, base=BASE, live=LIVE, modified=MOD)
    assert first.returncode == 0, first.stderr
    one = _live_files(changes)["var/lib/dpkg/status"]
    # second run in the same workdir, as a rebuild would be: leftovers must not leak in
    env = {"PATH": f"{tmp_path / 'bin'}:/usr/bin:/bin", "FAKE_FIX": str(tmp_path / "fix"),
           "FAKE_LOG": str(tmp_path / "fake.log"), "LIMPET_ISO_WORKDIR": str(work), "BUILD": str(BUILD), "HOME": str(tmp_path)}
    again = subprocess.run(["bash", "-c", 'source "$BUILD"; repack_live_layer "$@"', "_", "minimal.standard",
                            str(changes), str(tmp_path / "casper")], env=env, capture_output=True, text=True)
    assert again.returncode == 0, again.stderr
    assert _live_files(changes)["var/lib/dpkg/status"] == one


def test_leftovers_from_a_crashed_earlier_run_do_not_leak_into_the_live_layer(tmp_path):
    bin_dir, fix, casper, changes, work = _setup(tmp_path, base=BASE, live=LIVE, modified=MOD)
    stale = work / "live-upper"
    _write(stale / "var/lib/dpkg/status", status_file(stanza("stale-package")))
    _write(stale / "leaked-file", "from a crashed run\n")
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "FAKE_FIX": str(fix), "FAKE_LOG": str(tmp_path / "fake.log"),
           "LIMPET_ISO_WORKDIR": str(work), "BUILD": str(BUILD), "HOME": str(tmp_path)}
    result = subprocess.run(["bash", "-c", 'source "$BUILD"; repack_live_layer "$@"', "_", "minimal.standard",
                             str(changes), str(casper)], env=env, capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    files = _live_files(changes)
    assert "leaked-file" not in files
    assert "stale-package" not in files["var/lib/dpkg/status"]


def _carry(tmp_path, original, regenerated):
    (tmp_path / "orig").write_text(original)
    (tmp_path / "new").write_text(regenerated)
    env = {"PATH": "/usr/bin:/bin", "LIMPET_ISO_WORKDIR": str(tmp_path / "w"), "BUILD": str(BUILD), "HOME": str(tmp_path)}
    result = subprocess.run(["bash", "-c", 'source "$BUILD"; carry_snap_records "$1" "$2"', "_",
                             str(tmp_path / "orig"), str(tmp_path / "new")], env=env, capture_output=True, text=True)
    return result, (tmp_path / "new").read_text()


def test_snap_records_are_appended_to_a_regenerated_manifest(tmp_path):
    result, out = _carry(tmp_path, "a\t1\nsnap:core24\tstable\t1587\nsnap:snapd\tstable\t26865\n", "a\t1\n")

    assert result.returncode == 0, result.stderr
    assert out == "a\t1\nsnap:core24\tstable\t1587\nsnap:snapd\tstable\t26865\n"


def test_a_manifest_without_snap_records_is_not_an_error(tmp_path):
    result, out = _carry(tmp_path, "a\t1\nb\t2\n", "a\t1\n")

    assert result.returncode == 0, result.stderr
    assert out == "a\t1\n"
