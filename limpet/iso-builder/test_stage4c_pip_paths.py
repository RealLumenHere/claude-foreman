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

wq#4676 adds a second test with a stronger fake (FAKE_DOCKER_EXEC): it *runs* the
generated `bash -euc` script instead of bypassing it, so defects inside that string can
fail a test at all. The defect that motivated it (shipped by wq#4672, caught by wq#4675's
verify pass): `[ -n "$(ls ~/.local/bin/ ...)" ]` was written with a bare `$(` and bare
`"` inside the one big double-quoted string build.sh hands to `docker run ... bash -euc`,
so build.sh's *own* shell ran the `ls` against the *build host's* home at command-assembly
time -- before the container started -- and the unescaped quotes let the result's words
spill into docker's argv. The test plants two files in the host-side `$HOME/.local/bin`
(the un-probed case: wq#4672's fallback only looks accidentally harmless when the build
user's own ~/.local/bin is empty) and fakes `pipx` as a pipx that ignores PIPX_BIN_DIR, so
the fallback copy is the only route from the container's `$HOME/.local/bin` to the
bind-mounted `/usr/local/bin`.
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

# wq#4676: unlike FAKE_DOCKER, this one executes the generated `bash -euc` script, with
# the container's absolute paths rewritten to their bind-mount sources and $HOME pointed
# at a per-invocation stand-in container home. Everything the script shells out to
# (apt-get, pip, pipx, sha256sum) is faked on PATH; bash itself is real, which is the
# point -- the string build.sh assembles is parsed and run as written.
FAKE_DOCKER_EXEC = r"""#!/usr/bin/env python3
import json, os, pathlib, subprocess, sys

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

if "/opt/re-venv" in mounts:  # the pip-git call; wq#4601's test already covers its wiring
    p = pathlib.Path(mounts["/opt/re-venv"]) / "bin" / os.environ["FAKE_GIT_BIN"]
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("#!/usr/bin/env python3\n")
    p.chmod(0o755)
    sys.exit(0)

b = argv.index("bash")
flags, script, spilled = argv[b + 1], argv[b + 2], argv[b + 3:]

sandbox = pathlib.Path(os.environ["FAKE_CONTAINER_ROOT"])
home = sandbox / "root"
(home / ".local" / "bin").mkdir(parents=True, exist_ok=True)
(sandbox / "tmp").mkdir(parents=True, exist_ok=True)
for container_path, host_path in (
    ("/usr/local/share/pipx", mounts["/usr/local/share/pipx"]),
    ("/usr/local/bin", mounts["/usr/local/bin"]),
    ("/tmp/dl", str(sandbox / "tmp" / "dl")),
):
    script = script.replace(container_path, host_path)
(sandbox / "rewritten-script").write_text(script)

env = dict(
    os.environ,
    HOME=str(home),
    PATH=os.environ["FAKE_CONTAINER_BIN"] + os.pathsep + os.environ["PATH"],
)
# $0 plus whatever the (buggy) unquoted substitution spilled past the script argument.
r = subprocess.run(["bash", flags, script, "fake-docker"] + spilled, env=env)
sys.exit(r.returncode)
"""

FAKE_APT_GET = "#!/bin/sh\nexit 0\n"

FAKE_PIP = r"""#!/usr/bin/env python3
import pathlib, sys

argv = sys.argv[1:]
dest = pathlib.Path(argv[argv.index("--dest") + 1])
dest.mkdir(parents=True, exist_ok=True)
(dest / "footool-1.0.0-py3-none-any.whl").write_text("not a real wheel\n")
"""

# Matches MANIFEST's pinned sha256 so stage4c's verify step passes.
FAKE_SHA256SUM = r"""#!/usr/bin/env python3
import sys

for p in sys.argv[1:]:
    print("0" * 64 + "  " + p)
"""

# A pipx that does NOT honour PIPX_BIN_DIR -- the misbehaviour wq#4672's fallback copy
# exists for. The app lands in the container's $HOME/.local/bin, so the only route to the
# bind-mounted /usr/local/bin is the fallback, and only if its guard really runs in here.
FAKE_PIPX = r"""#!/usr/bin/env python3
import os, pathlib, sys

if sys.argv[1:2] != ["install"]:
    sys.exit(0)
d = pathlib.Path(os.environ["HOME"]) / ".local" / "bin"
d.mkdir(parents=True, exist_ok=True)
p = d / os.environ["FAKE_PIPX_BIN"]
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


def test_build_sh_parses():
    r = subprocess.run(["bash", "-n", str(BUILD)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_stage4c_pipx_fallback_runs_inside_the_container_not_on_the_build_host(tmp_path):
    """wq#4676: the fallback's guard must be evaluated by the container's shell against
    the container's $HOME, not by build.sh's own shell against the build host's."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _exe(bin_dir / "docker", FAKE_DOCKER_EXEC)

    container_bin = tmp_path / "container-bin"
    container_bin.mkdir()
    _exe(container_bin / "apt-get", FAKE_APT_GET)
    _exe(container_bin / "pip", FAKE_PIP)
    _exe(container_bin / "pipx", FAKE_PIPX)
    _exe(container_bin / "sha256sum", FAKE_SHA256SUM)

    # The build host's own ~/.local/bin, deliberately NOT empty: wq#4672's bare-$( form
    # only looks harmless when it is. Two entries, so a host-side substitution also
    # word-splits and spills past docker's script argument.
    host_local_bin = tmp_path / ".local" / "bin"
    host_local_bin.mkdir(parents=True)
    (host_local_bin / "host-marker-one").write_text("build host, not container\n")
    (host_local_bin / "host-marker-two").write_text("build host, not container\n")

    work = tmp_path / "work"
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(MANIFEST))
    log_path = tmp_path / "docker-invocations.jsonl"
    container_root = tmp_path / "container-root"

    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin:/usr/sbin:/sbin",
        "LIMPET_ISO_WORKDIR": str(work),
        "BUILD": str(BUILD),
        "HOME": str(tmp_path),
        "FAKE_DOCKER_LOG": str(log_path),
        "FAKE_CONTAINER_ROOT": str(container_root),
        "FAKE_CONTAINER_BIN": str(container_bin),
        "FAKE_PIPX_BIN": "footool",
        "FAKE_GIT_BIN": "bartool",
    }

    r = subprocess.run(
        ["bash", "-c", 'source "$BUILD"; stage4c_build_pip_tools "$1"', "_", str(manifest_path)],
        env=env, capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr

    pipx_argv = [json.loads(line) for line in log_path.read_text().splitlines()][0]
    b = pipx_argv.index("bash")
    # Exactly flags + script: an unescaped `"` ends build.sh's string early and lets the
    # substitution's words become extra docker arguments.
    assert pipx_argv[b:] == [pipx_argv[b], "-euc", pipx_argv[b + 2]], pipx_argv[b:]

    script = pipx_argv[b + 2]
    assert "$(ls ~/.local/bin/ 2>/dev/null)" in script  # deferred to the inner shell
    assert "host-marker" not in script  # ... and not already expanded on the build host

    # The fake pipx ignored PIPX_BIN_DIR, so the fallback copy is what delivered the app.
    assert (container_root / "root/.local/bin/footool").exists()
    out = work / "pip-out"
    assert (out / "usr/local/bin/footool").exists()
    assert os.access(out / "usr/local/bin/footool", os.X_OK)
    assert (work / "chroot/usr/local/bin/footool").exists()
    # Nothing from the build host's ~/.local/bin was copied into the image.
    assert not (out / "usr/local/bin/host-marker-one").exists()
