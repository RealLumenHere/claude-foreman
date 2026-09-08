"""wq#1537 — a primary's deployed file must be syncable to origin/<default> without moving
HEAD, so one unrelated in-flight ticket's uncommitted edits elsewhere in the tree can't hold
every OTHER file's deploy hostage indefinitely (the class `sync-live`, wq#1256, cannot cover:
it fast-forwards the whole tree and refuses on ANY dirty tracked file).

Built and reproduced against a throwaway git "remote" + "primary" pair, never the real
/home/lumen/bin — the tool is generic (repo/path/branch/remote are all parameters), so the
scratch fixture exercises the exact same code path the live deploy uses.

Covers:
  A. --check reports NOT IN SYNC when the primary's file predates a remote-only commit, and
     IN SYNC once applied (idempotent: a second --apply is a no-op).
  B. --apply refuses and leaves the live file untouched when it carries an uncommitted local
     edit — the one guard this tool exists to enforce.
  C. --apply preserves the target's executable bit and rejects a candidate that fails to
     parse before it ever reaches the live path.
  D. (wq#1544) a SECOND origin advance after a successful --apply still applies. --apply never
     moves HEAD, so its own prior write leaves the file permanently "modified" against HEAD;
     reading that as WIP made this a one-shot tool that self-locked (wq#1541's repro, and the
     state live /home/lumen/bin was already stuck in). B still has to refuse, so the two cases
     are asserted against the same fixture shape.
"""
import json
import os
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENSURE = os.path.join(REPO, "ops", "wq1537-primary-file-deploy-sync",
                      "ensure_primary_file_deploy_sync.py")


def git(cwd, *args, check=True):
    p = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)
    if check and p.returncode != 0:
        raise RuntimeError(f"git {args} failed: {p.stderr}")
    return p.stdout


def make_pair(tmp_path, script_body="#!/usr/bin/env bash\necho v1\n"):
    """A bare 'origin' + a 'primary' clone, both seeded with one committed file."""
    origin = tmp_path / "origin.git"
    primary = tmp_path / "primary"
    git(tmp_path, "init", "--bare", "-b", "main", str(origin))
    git(tmp_path, "clone", str(origin), str(primary))
    git(primary, "config", "user.email", "test@example.com")
    git(primary, "config", "user.name", "test")
    (primary / "tool").write_text(script_body)
    os.chmod(primary / "tool", 0o755)
    git(primary, "add", "tool")
    git(primary, "commit", "-m", "v1")
    git(primary, "push", "origin", "main")
    return origin, primary


def push_version(tmp_path, origin, body, msg):
    """Land a new version on origin from a SECOND clone — the primary's checkout never sees it
    until this tool runs (mirrors wq#1536 landing on origin/main while /home/lumen/bin sat on
    an older branch)."""
    other = tmp_path / "other"
    if other.exists():
        git(other, "pull", "origin", "main")
    else:
        git(tmp_path, "clone", str(origin), str(other))
        git(other, "config", "user.email", "test@example.com")
        git(other, "config", "user.name", "test")
    (other / "tool").write_text(body)
    git(other, "add", "tool")
    git(other, "commit", "-m", msg)
    git(other, "push", "origin", "main")


def args_for(primary):
    return ["--repo", str(primary), "--path", "tool", "--branch", "main", "--remote", "origin"]


def run(*args, env=None):
    return subprocess.run([sys.executable, ENSURE, *args], capture_output=True, text=True,
                          env={**os.environ, **(env or {})})


def test_check_and_apply_sync_to_origin(tmp_path):
    origin, primary = make_pair(tmp_path)
    push_version(tmp_path, origin, "#!/usr/bin/env bash\necho v2\n", "v2")

    common = args_for(primary)

    p = run("--check", *common)
    assert p.returncode == 1, p.stdout + p.stderr
    assert "NOT IN SYNC" in p.stdout

    p = run("--apply", *common)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "SYNCED" in p.stdout
    assert (primary / "tool").read_text() == "#!/usr/bin/env bash\necho v2\n"
    assert os.stat(primary / "tool").st_mode & 0o111  # exec bit preserved

    # Idempotent: HEAD never moved (still on main, still the original v1 commit) — only the
    # working-tree file content changed, exactly as intended.
    assert git(primary, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"

    p = run("--check", *common)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "IN SYNC" in p.stdout

    p = run("--apply", *common)
    assert p.returncode == 0
    assert "already" not in p.stdout.lower() or "IN SYNC" in p.stdout  # no-op, still success


def test_apply_refuses_when_live_file_is_locally_dirty(tmp_path):
    origin, primary = make_pair(tmp_path)
    push_version(tmp_path, origin, "#!/usr/bin/env bash\necho v2\n", "v2")

    # The primary has its OWN uncommitted edit to the same file — an in-flight ticket's WIP
    # that exists nowhere else and must never be silently overwritten.
    (primary / "tool").write_text("#!/usr/bin/env bash\necho WIP-do-not-clobber\n")

    common = args_for(primary)
    p = run("--apply", *common)
    assert p.returncode == 2, p.stdout + p.stderr
    assert "uncommitted local edit" in p.stderr
    assert (primary / "tool").read_text() == "#!/usr/bin/env bash\necho WIP-do-not-clobber\n"


def test_apply_rejects_unparseable_candidate(tmp_path):
    origin, primary = make_pair(tmp_path)
    push_version(tmp_path, origin, "#!/usr/bin/env bash\nif [ 1 -eq 1\necho unterminated\n",
                 "broken")

    common = args_for(primary)
    original = (primary / "tool").read_text()
    p = run("--apply", *common)
    assert p.returncode == 2, p.stdout + p.stderr
    assert (primary / "tool").read_text() == original


V2 = "#!/usr/bin/env bash\necho v2\n"
V3 = "#!/usr/bin/env bash\necho v3\n"


def test_second_origin_advance_still_applies_after_a_first_apply(tmp_path):
    """wq#1544: --apply must not self-lock on its own prior write."""
    origin, primary = make_pair(tmp_path)
    common = args_for(primary)

    push_version(tmp_path, origin, V2, "v2")
    p = run("--apply", *common)
    assert p.returncode == 0, p.stdout + p.stderr
    assert (primary / "tool").read_text() == V2

    # HEAD deliberately stayed put, so the file this tool just wrote IS modified against it —
    # dirty, but ours, and that distinction is the whole fix.
    assert git(primary, "status", "--porcelain", "--", "tool").strip()
    st = json.loads(run("--check", "--json", *common).stdout)
    assert st["dirty"] and not st["foreign_edit"], st

    push_version(tmp_path, origin, V3, "v3")
    p = run("--apply", *common)
    assert p.returncode == 0, p.stdout + p.stderr
    assert (primary / "tool").read_text() == V3

    # ...and the guard is still armed: a genuine third-party edit on top of a synced file is
    # refused exactly as before.
    (primary / "tool").write_text("#!/usr/bin/env bash\necho WIP-do-not-clobber\n")
    push_version(tmp_path, origin, "#!/usr/bin/env bash\necho v4\n", "v4")
    p = run("--apply", *common)
    assert p.returncode == 2, p.stdout + p.stderr
    assert "uncommitted local edit" in p.stderr
    assert (primary / "tool").read_text() == "#!/usr/bin/env bash\necho WIP-do-not-clobber\n"


def test_prior_write_is_recognized_from_the_sidecar_alone(tmp_path):
    """The remote-history lookup alone could explain a self-written file, so pin the sidecar
    independently: with the history window closed to one commit, only the recorded write can
    tell our own v2 output apart from somebody's WIP."""
    origin, primary = make_pair(tmp_path)
    common = args_for(primary)
    narrow = {"WQ1537_LOG_LIMIT": "1"}

    push_version(tmp_path, origin, V2, "v2")
    assert run("--apply", *common, env=narrow).returncode == 0

    push_version(tmp_path, origin, V3, "v3")
    st = json.loads(run("--check", "--json", *common, env=narrow).stdout)
    assert st["self_written"] and not st["in_remote_history"], st

    p = run("--apply", *common, env=narrow)
    assert p.returncode == 0, p.stdout + p.stderr
    assert (primary / "tool").read_text() == V3
