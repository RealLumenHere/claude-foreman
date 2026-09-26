"""wq#2739: how far does the own-tripwire corpus fall short of the files that
actually carry a credential shape?

test_the_mask_suite_does_not_arm_its_own_tripwire guards the eight paths
hand-listed in MASK_SUITE_SOURCES. This walks a whole subtree instead, at a
PINNED rev, so the answer is re-runnable rather than a snapshot of a dirty
worktree. It reports label/prefix/value_len per file and never a value —
printing the body is the exact harm the tripwire exists to catch.

    python3 ops/wq2739-tripwire-corpus/scan_tree.py /home/lumen/foreman fa8784d reports ops

Exits 1 when any file is an offender, so it can gate.
"""
import os
import subprocess
import sys


def blob(repo, rev, path):
    out = subprocess.run(["git", "-C", repo, "show", "%s:%s" % (rev, path)],
                         capture_output=True, check=True).stdout
    return out.decode("utf-8", "replace")


def main(argv):
    if len(argv) < 4:
        sys.stderr.write("usage: scan_tree.py <repo> <rev> <prefix> [<prefix> ...]\n")
        return 2
    repo, rev, prefixes = argv[1], argv[2], argv[3:]

    # the rules travel with the repo under test, not with whatever is on sys.path
    sys.path.insert(0, os.path.join(repo, "harness"))
    import masking

    paths = subprocess.run(
        ["git", "-C", repo, "ls-tree", "-r", "--name-only", rev] + prefixes,
        capture_output=True, text=True, check=True).stdout.split()

    offenders = {}
    for path in paths:
        hits = masking.scan(blob(repo, rev, path))[1]
        if hits:
            offenders[path] = sorted(
                {(h["label"], h["prefix"], h["value_len"]) for h in hits})

    print("rev %s: scanned %d file(s) under %r" % (rev, len(paths), prefixes))
    print("offenders: %d" % len(offenders))
    for path in sorted(offenders):
        print("  %s -> %r" % (path, offenders[path]))
    return 1 if offenders else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
