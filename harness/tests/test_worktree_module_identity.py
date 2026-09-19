"""wq#1991 (ISSUE-LOG #411): no live module may put the PRIMARY checkout on sys.path.

`despag/admission.py` and `despag/backends/claude_max.py` hardcoded
`/home/lumen/foreman/{governor,dispatcher,harness}` and inserted them at position 0 at
import time. Importing despag from a worktree therefore put the primary tree AHEAD of
the worktree's own directories, and every later bare `import X` in that process cached
the PRIMARY copy in sys.modules under that name. wq#1676's `test_derive_probe_unit`
passed alone and failed in-suite with `module liveness_clear has no attribute
derive_probe` for exactly this reason: 16 modules resolved from the wrong tree.

Two independent locks, because either alone is escapable:
  - a static lint, so the shape cannot be reintroduced by a new file that no test imports;
  - a live subprocess assert, so an indirect route (a copied constant, an env default)
    that the lint cannot see still fails.

Note the literal `/home/lumen/foreman` is fine as test DATA — fixture repo paths,
primary_tree_guard's guarded-tree registry, guard.sh deny-list arguments. The audit for
this ticket found 8 of the 9 named test files were data-only and correct as written;
only fake_claude.py's GUARD_SH was a real defect (it ran the primary tree's guard.sh).
"""
import ast
import json
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
HARNESS = os.path.dirname(HERE)
REPO = os.path.dirname(HARNESS)

sys.path.insert(0, HARNESS)
sys.path.insert(0, HERE)  # fake_claude.py lives here and is otherwise only ever exec'd

PRIMARY = "/home/lumen/foreman"

# Live production trees only. pilots/, reports/ and ops/ hold archived one-off scripts
# that nothing imports; they are not on any run's import path, so a stale literal there
# cannot shadow a worktree module.
LIVE_DIRS = ("despag", "harness", "dispatcher", "governor", "orchestrator")


def _live_sources():
    for top in LIVE_DIRS:
        root = os.path.join(REPO, top)
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            for fn in filenames:
                if fn.endswith(".py"):
                    yield os.path.join(dirpath, fn)


def _sys_path_literals(tree):
    """Yield (lineno, literal) for every sys.path.insert/append whose argument resolves
    to a string constant. Three shapes, all of which a naive grep for
    `sys.path.insert(0, "/home/...")` misses:
      - inline:    sys.path.insert(0, "/home/lumen/foreman/harness")
      - via name:  _D = "/home/..."           ; sys.path.insert(0, _D)
      - via loop:  for _d in (_A, _B, _C):    ; sys.path.insert(0, _d)   <- ISSUE-LOG #426
    The loop form is the one despag/admission.py actually used, so a detector that
    stops at the name form is a no-op against the original bug.
    """
    consts = {}

    def resolve(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return [node.value]
        if isinstance(node, ast.Name):
            return list(consts.get(node.id, ()))
        if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            return [lit for e in node.elts for lit in resolve(e)]
        return []

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for lit in resolve(node.value):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        consts.setdefault(t.id, []).append(lit)
    for node in ast.walk(tree):
        if isinstance(node, ast.For) and isinstance(node.target, ast.Name):
            for lit in resolve(node.iter):
                consts.setdefault(node.target.id, []).append(lit)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in ("insert", "append"):
            continue
        target = node.func.value
        if not (isinstance(target, ast.Attribute) and target.attr == "path"
                and isinstance(target.value, ast.Name) and target.value.id == "sys"):
            continue
        for arg in node.args:
            for lit in resolve(arg):
                yield node.lineno, lit


@pytest.mark.parametrize("src", sorted(_live_sources()),
                         ids=lambda p: os.path.relpath(p, REPO))
def test_no_live_module_hardcodes_the_primary_tree_on_sys_path(src):
    tree = ast.parse(open(src).read(), filename=src)
    offenders = [(ln, lit) for ln, lit in _sys_path_literals(tree)
                 if lit == PRIMARY or lit.startswith(PRIMARY + os.sep)]
    assert not offenders, (
        f"{os.path.relpath(src, REPO)} puts the primary checkout on sys.path: "
        f"{offenders}. Derive it from __file__ "
        "(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) — a literal "
        "shadows the running worktree's modules for the whole process (wq#1991).")


@pytest.mark.parametrize("src,expected", [
    ('import sys\nsys.path.insert(0, "/home/lumen/foreman/harness")\n',
     ["/home/lumen/foreman/harness"]),
    ('import sys\n_D = "/home/lumen/foreman/governor"\nsys.path.insert(0, _D)\n',
     ["/home/lumen/foreman/governor"]),
    ('import sys\nsys.path.append("/home/lumen/foreman")\n', ["/home/lumen/foreman"]),
    # despag/admission.py's original shape, which a name-only detector walks straight past
    ('import sys\n_G = "/home/lumen/foreman/governor"\n_H = "/home/lumen/foreman/harness"\n'
     'for _d in (_G, _H):\n    sys.path.insert(0, _d)\n',
     ["/home/lumen/foreman/governor", "/home/lumen/foreman/harness"]),
    # the fixed shape, and the legitimate out-of-repo dirs, must stay clean
    ('import os, sys\nsys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n', []),
    ('import sys\nsys.path.insert(0, "/home/lumen/bin")\n', []),
    ('import sys\nsys.path.insert(0, "/home/lumen/minerva")\n', []),
])
def test_lint_detector_catches_every_offending_shape(src, expected):
    """The detector itself, so a refactor cannot quietly turn the lint into a no-op that
    passes on every file because it stopped matching anything."""
    lits = [lit for _, lit in _sys_path_literals(ast.parse(src))
            if lit == PRIMARY or lit.startswith(PRIMARY + os.sep)]
    assert lits == expected


_LEAK_PROBE = r"""
import json, os, sys
REPO = sys.argv[1]
sys.path.insert(0, os.path.join(REPO, "harness"))
sys.path.insert(0, REPO)
import despag.app  # noqa: F401 — pulls admission + claude_max, the two original offenders
MINE = os.path.realpath(REPO) + os.sep


def foreign(p):
    r = os.path.realpath(p)
    return r.startswith("/home/lumen/foreman/") and not r.startswith(MINE)


leaks = sorted(name for name, mod in sys.modules.items()
               if getattr(mod, "__file__", None) and foreign(mod.__file__))
bad_path = [p for p in sys.path if foreign(p)]
print("WQ1991 " + json.dumps({"modules": leaks, "sys_path": bad_path}))
"""


@pytest.mark.skipif(os.path.realpath(REPO) == os.path.realpath(PRIMARY),
                    reason="running IN the primary tree — nothing to shadow")
def test_importing_despag_resolves_no_module_from_the_primary_tree():
    """The live half: import the real app in a clean interpreter rooted at THIS tree and
    assert nothing came from the primary checkout. Catches routes the AST lint cannot
    see (a constant copied in from elsewhere, an env-var default, a runtime insert)."""
    proc = subprocess.run([sys.executable, "-c", _LEAK_PROBE, REPO],
                          capture_output=True, text=True, timeout=180)
    assert proc.returncode == 0, proc.stderr[-2000:]
    line = [ln for ln in proc.stdout.splitlines() if ln.startswith("WQ1991 ")]
    assert line, f"probe printed no result:\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}"
    got = json.loads(line[-1][len("WQ1991 "):])
    assert got == {"modules": [], "sys_path": []}, (
        f"importing despag leaked the primary tree into this worktree's process: {got}")


def test_fake_claude_guard_sh_is_this_trees_copy(monkeypatch):
    """fake_claude ran /home/lumen/foreman/harness/guard.sh by default, so a worktree's
    guard.sh changes were never the ones under test."""
    monkeypatch.delenv("FAKE_CLAUDE_GUARD_SH", raising=False)
    import importlib

    import fake_claude
    importlib.reload(fake_claude)
    assert fake_claude.GUARD_SH == os.path.join(HARNESS, "guard.sh")
    assert os.path.exists(fake_claude.GUARD_SH), fake_claude.GUARD_SH
