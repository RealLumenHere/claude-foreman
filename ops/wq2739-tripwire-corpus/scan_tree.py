"""wq#2739: how far does the own-tripwire corpus fall short of the files that
actually carry a credential shape?

test_the_mask_suite_does_not_arm_its_own_tripwire used to guard eight paths
hand-listed in MASK_SUITE_SOURCES; since wq#2749 it walks every committed text file
of the checked-out tree. This is the same walk at a PINNED rev (and optionally a
subtree), so the answer is re-runnable rather than a snapshot of a dirty worktree.
With no prefix it scans the whole tree; install.sh --verify runs it at HEAD. It reports label/prefix/value_len per file and never a value —
printing the body is the exact harm the tripwire exists to catch.

    python3 ops/wq2739-tripwire-corpus/scan_tree.py /home/lumen/foreman fa8784d reports ops

wq#2981: `--staged` in place of `<rev>` runs the identical scan over the git INDEX
instead of a commit — what `lumen-pr commit` is about to land, before it lands. This
is the gate that stops the next report/doc file from arming the tripwire on main: a
verify/judgment report that quotes the value its own ticket is about is a credential
shape the moment it is staged, not just once it is committed (ISSUE-LOG #951).

    python3 ops/wq2739-tripwire-corpus/scan_tree.py /home/lumen/foreman --staged reports ops

Exits 1 when any file is an offender, so it can gate.
"""
import os
import subprocess
import sys


def blob(repo, rev, path):
    out = subprocess.run(["git", "-C", repo, "show", "%s:%s" % (rev, path)],
                         capture_output=True, check=True).stdout
    return out.decode("utf-8", "replace")


def staged_blob(repo, path):
    out = subprocess.run(["git", "-C", repo, "show", ":%s" % path],
                         capture_output=True, check=True).stdout
    return out.decode("utf-8", "replace")


def scan_offenders(masking, paths, text_of):
    """{path: sorted [(label, prefix, value_len), ...]} for every path that scans dirty."""
    offenders = {}
    for path in paths:
        text = text_of(path)
        if "\0" in text[:8000]:
            continue
        hits = masking.scan(text)[1]
        if hits:
            offenders[path] = sorted(
                {(h["label"], h["prefix"], h["value_len"]) for h in hits})
    return offenders


def report(offenders, label, scanned, prefixes):
    print("%s: scanned %d file(s) under %r" % (label, scanned, prefixes))
    print("offenders: %d" % len(offenders))
    for path in sorted(offenders):
        print("  %s -> %r" % (path, offenders[path]))
        print("    split each shape at the boundary its rule reads "
              "(`\"sk-\" + \"...\"`, `` `TOKEN=\"` + `...` `` in prose) "
              "before this lands")


def main(argv):
    if len(argv) < 3:
        sys.stderr.write("usage: scan_tree.py <repo> <rev>|--staged [<prefix> ...]\n")
        return 2
    repo, rev, prefixes = argv[1], argv[2], argv[3:]

    # the rules travel with the repo under test, not with whatever is on sys.path
    sys.path.insert(0, os.path.join(repo, "harness"))
    import masking

    if rev == "--staged":
        paths = subprocess.run(
            ["git", "-C", repo, "diff", "--cached", "--name-only",
             "--diff-filter=ACM", "--"] + prefixes,
            capture_output=True, text=True, check=True).stdout.split()
        offenders = scan_offenders(masking, paths, lambda p: staged_blob(repo, p))
        report(offenders, "staged", len(paths), prefixes)
        return 1 if offenders else 0

    paths = subprocess.run(
        ["git", "-C", repo, "ls-tree", "-r", "--name-only", rev] + prefixes,
        capture_output=True, text=True, check=True).stdout.split()
    offenders = scan_offenders(masking, paths, lambda p: blob(repo, rev, p))
    report(offenders, "rev %s" % rev, len(paths), prefixes)
    return 1 if offenders else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
