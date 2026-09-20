"""wq#1697 fix 1: what the harness hands a worker, the worker holds.

`wq claim` tells every session "write ad-hoc files under /tmp/<agent>/" (bin/wq
SCRATCH_ROOT, wq#598), while a spawned worker's --add-dir set was its worktree plus the
static read roots. A worker that followed wq's own instruction was refused
(CAPABILITY_DENIED dir(/tmp/...), wq#1610/#1616/#1672) and the fix ticket parked. The
scratch dir is created by the harness for exactly one run, so it is granted for exactly
that run: implicit_grants() is the single list fworker hands the CLI and failure triage
treats as reachable.

lint_agreement() is the policy lint: it fails when wq's scratch rule and this module
disagree about where scratch lives, or when the scratch dir would itself be denied.
    python3 scratch_grant.py            # exit 1 + one line per disagreement
"""
import os
import re
import sys

HARNESS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HARNESS_DIR)
import capability_lint  # noqa: E402

# Same variable, same default as bin/wq's SCRATCH_ROOT — lint_agreement() proves it.
SCRATCH_ROOT = os.environ.get("WQ_SCRATCH_ROOT", "/tmp")
WQ_BIN_PATH = os.environ.get("FOREMAN_WQ_BIN", "/home/lumen/bin/wq")
_WQ_ROOT_RE = re.compile(
    r"^SCRATCH_ROOT\s*=\s*os\.environ\.get\(\s*[\"']WQ_SCRATCH_ROOT[\"']\s*,\s*[\"'](?P<root>[^\"']+)[\"']",
    re.M)


def scratch_dir(agent, root=None):
    """bin/wq: os.path.join(SCRATCH_ROOT, agent.replace("/", "_"))."""
    return os.path.join(root or SCRATCH_ROOT, agent.replace("/", "_"))


def ensure_scratch(agent, root=None):
    d = scratch_dir(agent, root)
    os.makedirs(d, mode=0o700, exist_ok=True)
    os.chmod(d, 0o700)  # exist_ok path may have pre-existed looser
    return d


def implicit_grants(worktree, agent, root=None):
    """The reach the harness itself creates for this run: worktree + wq scratch dir."""
    return [worktree, scratch_dir(agent, root)]


def wq_scratch_root(wq_path=None):
    try:
        with open(wq_path or WQ_BIN_PATH) as f:
            m = _WQ_ROOT_RE.search(f.read())
    except OSError:
        return None
    return m.group("root") if m else None


def lint_agreement(wq_path=None, root=None):
    """[] when wq's scratch rule and the harness grant agree, else one problem per line."""
    root = root or SCRATCH_ROOT
    problems = []
    theirs = wq_scratch_root(wq_path)
    if theirs is None:
        problems.append(f"cannot read SCRATCH_ROOT from {wq_path or WQ_BIN_PATH}")
    elif os.path.normpath(theirs) != os.path.normpath(root):
        problems.append(f"wq scratch root {theirs} != harness scratch root {root}")
    probe = scratch_dir("probe-agent", root)
    why = capability_lint.dir_denied(probe)
    if why:
        problems.append(f"scratch dir {probe} is on the deny-list: {why}")
    if probe not in implicit_grants("/wt", "probe-agent", root):
        problems.append(f"scratch dir {probe} is not in implicit_grants()")
    return problems


def main():
    problems = lint_agreement()
    for p in problems:
        print(f"scratch_grant lint: {p}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
