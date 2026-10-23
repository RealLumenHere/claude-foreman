"""wq#4601: stage4c built pipx/venv trees under an /out-prefixed staging path (e.g.
PIPX_HOME=/out/usr/local/share/pipx) and then cp -a'd them into $CHROOT (/usr/local/...).
pipx and `python3 -m venv` both bake PIPX_HOME/the venv dir's absolute path into every
console-script shebang and pipx app symlink at creation time, so every tool installed
this way had its interpreter pointed at a nonexistent /out/... path once copied into the
chroot -- the thing the comment above stage4c_build_pip_tools calls out as the invariant
that must hold ("the build path and the chroot-install path must be identical").

`docker` is faked (no real pip/pipx/network available in this sandbox); the fake only
records every invocation's argv and drops a dummy executable at the bind-mounted path, so
this test can assert on stage4c's own bind-mount wiring: before the fix the only `-v` flag
was `$out:/out` (the container-side path startswith /out); after the fix there are
dedicated mounts whose container-side path is the real final absolute path
(/usr/local/share/pipx, /usr/local/bin, /opt/re-venv). A revert to the /out-prefixed
staging trips this test (wq#2630: a probe that cannot fail is not a probe).
"""
import json
import os
import stat
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
BUILD = HERE / "build.sh"

FAKE_DOCKER = r"""#!/usr/bin/env python3
import json, os, pathlib, sys

argv = sys.argv[1:]
with open(os.environ["FAKE_DOCKER_LOG"], "a") as f:
    f.write(json.dumps(argv) + "\n")

mounts = {}
i = 0
while i < len(argv):
    if argv[i] == "-v":
        src, dst = argv[i + 1].split(":", 1)
        mounts[dst] = src
        i += 2
    else:
        i += 1

if "/usr/local/bin" in mounts:
    p = pathlib.Path(mounts["/usr/local/bin"]) / os.environ["FAKE_PIPX_BIN"]
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("#!/usr/bin/env python3\n")
    p.chmod(0o755)
if "/opt/re-venv" in mounts:
    p = pathlib.Path(mounts["/opt/re-venv"]) / "bin" / os.environ["FAKE_GIT_BIN"]
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("#!/usr/bin/env python3\n")
    p.chmod(0o755)
"""

MANIFEST = {
    "pip_packages": [
        {"name": "footool", "package": "footool", "version": "1.0.0",
         "sha256": "0" * 64, "bin": "footool"},
    ],
    "pip_git_packages": [
        {"name": "bartool", "url": "git+https://example.invalid/bar.git",
         "commit": "a" * 40, "bin": "bartool", "build_apt": []},
    ],
}


def _exe(path, body):
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def test_stage4c_mounts_real_target_paths_not_an_out_prefixed_stand_in(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _exe(bin_dir / "docker", FAKE_DOCKER)

    work = tmp_path / "work"
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(MANIFEST))
    log_path = tmp_path / "docker-invocations.jsonl"

    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin:/usr/sbin:/sbin",
        "LIMPET_ISO_WORKDIR": str(work),
        "BUILD": str(BUILD),
        "HOME": str(tmp_path),
        "FAKE_DOCKER_LOG": str(log_path),
        "FAKE_PIPX_BIN": "footool",
        "FAKE_GIT_BIN": "bartool",
    }

    r = subprocess.run(
        ["bash", "-c", 'source "$BUILD"; stage4c_build_pip_tools "$1"', "_", str(manifest_path)],
        env=env, capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr

    invocations = [json.loads(line) for line in log_path.read_text().splitlines()]
    assert len(invocations) == 2  # one pipx call, one pip-git call

    all_mounts = []
    for argv in invocations:
        i = 0
        while i < len(argv):
            if argv[i] == "-v":
                src, dst = argv[i + 1].split(":", 1)
                all_mounts.append((src, dst))
                i += 2
            else:
                i += 1

    out = work / "pip-out"
    dsts = {dst for _, dst in all_mounts}
    # The bug: a single `-v $out:/out` mount, and every absolute path baked into a
    # pipx/venv shebang pointed at /out/... once copied into $CHROOT.
    assert not any(dst == "/out" or dst.startswith("/out/") for _, dst in all_mounts)
    assert dsts == {"/usr/local/share/pipx", "/usr/local/bin", "/opt/re-venv"}
    assert (str(out / "usr/local/share/pipx"), "/usr/local/share/pipx") in all_mounts
    assert (str(out / "usr/local/bin"), "/usr/local/bin") in all_mounts
    assert (str(out / "opt/re-venv"), "/opt/re-venv") in all_mounts

    pipx_script = invocations[0][-1]
    assert "PIPX_HOME=/usr/local/share/pipx" in pipx_script
    assert "PIPX_BIN_DIR=/usr/local/bin" in pipx_script
    assert "/out" not in pipx_script

    chroot = work / "chroot"
    assert (chroot / "usr/local/bin/footool").exists()
    assert os.access(chroot / "usr/local/bin/footool", os.X_OK)
    assert (chroot / "opt/re-venv/bin/bartool").exists()
    assert os.access(chroot / "opt/re-venv/bin/bartool", os.X_OK)
