#!/usr/bin/env python3
"""read_dir_relocate.py — wq#2118 (research/BROKER_GRANT_COVERAGE.md §3.2).

The history half of wq#2116. `action_gate._stow()` now keeps credential-shaped AMBER
pre-images out of the foreman read grant; this moves the ones that were already written.
`<root>/_runs` is inside a worker `--add-dir` root, so every credential-shaped artifact
under it (`*-home_lumen_agent_.env`, gatus config copies, git-state diffs, fleet
transcripts) is readable by the worker's own uid even though the allow list refuses the
source path by name.

Relocate, do not destroy: each artifact `read_dir_sweep` flags under `<root>/_runs` moves
to SNAP_VAULT (0700 dir, 0600 file) and a path-only `<name>.held-out` pointer — the same
one `_stow()` writes — takes its place, so the record survives and the grant stops
reaching it. Detection is read_dir_sweep's BASENAME_GLOBS + PATTERNS (one list, three
consumers); nothing here prints a value, only paths and counts.

    python3 harness/read_dir_relocate.py <root> [<root>...]            # dry run
    python3 harness/read_dir_relocate.py --apply <root> [<root>...]    # move + pointer
    python3 harness/read_dir_relocate.py --verify <root> [<root>...]   # pointers -> 0600 files
"""
import os
import shutil
import stat
import sys

HARNESS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HARNESS_DIR)
import read_dir_sweep as rds  # noqa: E402

SNAP_VAULT = os.environ.get("FOREMAN_GATE_SNAP_VAULT",
                            os.path.expanduser("~/.secrets/gate-snapshots"))
RUNS_DIR = "_runs"
HELD_OUT_SUFFIX = ".held-out"


def flagged_files(root):
    """Sorted paths under `<root>/_runs` that read_dir_sweep flags. A symlink is never
    followed or moved: the sweep does not read one, and its target is not ours."""
    runs = os.path.join(root, RUNS_DIR)
    if not os.path.isdir(runs):
        return []
    hits, _ = rds.sweep_root(runs)
    return sorted({p for p, _, _, _ in hits if not os.path.islink(p)})


def vault_name(root, path):
    """Collision-free flat vault name: the path under `root` with `/` -> `__`."""
    rel = os.path.relpath(path, root)
    return os.path.basename(os.path.normpath(root)) + "__" + rel.replace(os.sep, "__")


def relocate_one(root, path, vault=None):
    """Move `path` into the vault at 0600 and leave a path-only pointer. Returns the
    vault path. Refuses to overwrite an existing vault file."""
    vault = vault or SNAP_VAULT
    os.makedirs(vault, exist_ok=True, mode=0o700)
    os.chmod(vault, 0o700)
    dst = os.path.join(vault, vault_name(root, path))
    if os.path.lexists(dst):
        raise FileExistsError(dst)
    shutil.move(path, dst)
    os.chmod(dst, 0o600)
    with open(path + HELD_OUT_SUFFIX, "w") as f:
        f.write(f"credential-shaped artifact {path} moved out of the read grant\n"
                f"vault (0600): {dst}\n")
    return dst


def relocate(roots, apply=False, vault=None):
    """[(src, dst_or_None, error_or_None)] per flagged file; dst is set only when applied."""
    out = []
    for root in roots:
        for path in flagged_files(root):
            if not apply:
                out.append((path, None, None))
                continue
            try:
                out.append((path, relocate_one(root, path, vault), None))
            except OSError as e:
                out.append((path, None, f"{type(e).__name__}: {e}"))
    return out


def verify(roots):
    """[(pointer, vault_path_or_None, problem_or_None)] for every `.held-out` pointer under
    `<root>/_runs`: the vault file it names must exist, be a regular file, and be 0600.
    Path-only — no content is opened."""
    out = []
    for root in roots:
        for dp, dn, fn in os.walk(os.path.join(root, RUNS_DIR)):
            dn.sort()
            for f in sorted(x for x in fn if x.endswith(HELD_OUT_SUFFIX)):
                ptr = os.path.join(dp, f)
                try:
                    with open(ptr) as fh:
                        lines = [ln for ln in fh.read().splitlines() if ln.startswith("vault (0600): ")]
                except OSError as e:
                    out.append((ptr, None, f"unreadable pointer: {e}"))
                    continue
                if not lines:
                    out.append((ptr, None, "no vault line"))
                    continue
                dst = lines[0][len("vault (0600): "):]
                try:
                    st = os.lstat(dst)
                except OSError:
                    out.append((ptr, dst, "vault file missing"))
                    continue
                mode = stat.S_IMODE(st.st_mode)
                if not stat.S_ISREG(st.st_mode) or mode != 0o600:
                    out.append((ptr, dst, f"not a regular 0600 file (mode {mode:o})"))
                else:
                    out.append((ptr, dst, None))
    return out


def _main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(
        description="move credential-shaped <root>/_runs artifacts into the 0600 vault")
    ap.add_argument("roots", nargs="+")
    ap.add_argument("--apply", action="store_true", help="move (default is a dry run)")
    ap.add_argument("--verify", action="store_true",
                    help="check every .held-out pointer resolves to a 0600 vault file")
    ap.add_argument("--max-lines", type=int, default=20, help="paths shown (dry run)")
    a = ap.parse_args(argv)
    if a.verify:
        rows = verify(a.roots)
        bad = [r for r in rows if r[2]]
        vault_mode = stat.S_IMODE(os.stat(SNAP_VAULT).st_mode) if os.path.isdir(SNAP_VAULT) else None
        print(f"read_dir_relocate: {len(rows) - len(bad)} of {len(rows)} pointers resolve to a 0600 "
              f"vault file in {SNAP_VAULT} (dir mode {vault_mode and format(vault_mode, 'o')})")
        for ptr, dst, why in bad[:a.max_lines]:
            print(f"  {ptr} -> {dst}: {why}")
        return 1 if bad or vault_mode != 0o700 else 0
    rows = relocate(a.roots, apply=a.apply)
    moved = [r for r in rows if r[1]]
    failed = [r for r in rows if r[2]]
    verb = "moved" if a.apply else "would move"
    print(f"read_dir_relocate: {verb} {len(moved) if a.apply else len(rows)} "
          f"of {len(rows)} flagged files -> {SNAP_VAULT}, {len(failed)} failed")
    shown = failed if a.apply else rows[:a.max_lines]
    for src, dst, err in shown:
        print(f"  {src}" + (f" !! {err}" if err else ""))
    if not a.apply and len(rows) > a.max_lines:
        print(f"  … {len(rows) - a.max_lines} more")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_main())
