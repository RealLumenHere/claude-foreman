"""wq#4713: build.sh must resolve LIMPET_ROOT from the REAL location of the
script, not from the path it was invoked through.

The retired out-of-tree wrapper (/home/lumen/projects/limpet/iso-build/build.sh)
was a symlink into this repo. Because build.sh derived HERE from
`dirname "${BASH_SOURCE[0]}"` (the symlink's logical path), LIMPET_ROOT resolved
to /home/lumen/projects/limpet -- a foreign tree whose stale, untiered
tool-catalog.json (104 entries, 0 tiered) made stage4 bake 0 of 104 RE tools
(docs/build-tree-of-record.md). readlink -f on BASH_SOURCE[0] closes the class:
a symlinked invocation can never read a foreign repo's catalog again.

Falsifiable (wq#2630): the probe sources build.sh through a symlink that lives
in a DECOY tree carrying its own tool-catalog.json, i.e. an input from OUTSIDE
this repo. Before the fix LIMPET_ROOT is the decoy and TOOL_CATALOG points at
the decoy catalog; the asserts fail. After the fix both resolve to this repo.
"""
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
BUILD = HERE / "build.sh"
REAL_ROOT = BUILD.parent.parent  # foreman/limpet
REAL_CATALOG = REAL_ROOT / "tool-catalog.json"


def _probe(invoke_path):
    """Source build.sh via invoke_path (main is guarded off) and read the
    path vars it computed."""
    script = (
        f'source "{invoke_path}"\n'
        'echo "ROOT=$LIMPET_ROOT"\n'
        'echo "CAT=$TOOL_CATALOG"\n'
    )
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    out = dict(line.split("=", 1) for line in r.stdout.splitlines() if "=" in line)
    return Path(out["ROOT"]), Path(out["CAT"])


def test_direct_invocation_reads_this_repo():
    root, cat = _probe(BUILD)
    assert root == REAL_ROOT
    assert cat == REAL_CATALOG


def test_symlinked_invocation_from_foreign_tree_still_reads_this_repo(tmp_path):
    # Decoy out-of-tree layout mirroring the retired wrapper: a foreign
    # LIMPET_ROOT that carries its own catalog, reached via a symlink to the
    # real build.sh.
    decoy_root = tmp_path / "projects" / "limpet"
    decoy_iso_build = decoy_root / "iso-build"
    decoy_iso_build.mkdir(parents=True)
    decoy_catalog = decoy_root / "tool-catalog.json"
    decoy_catalog.write_text('{"tools": [], "decoy": true}\n')

    symlink = decoy_iso_build / "build.sh"
    symlink.symlink_to(BUILD)

    root, cat = _probe(symlink)
    assert root == REAL_ROOT, f"symlinked invocation leaked foreign root {root}"
    assert cat == REAL_CATALOG, f"symlinked invocation read foreign catalog {cat}"
    assert root != decoy_root
    assert cat != decoy_catalog
