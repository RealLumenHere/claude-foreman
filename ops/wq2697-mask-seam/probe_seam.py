"""wq#2697 acceptance probe: bin/mask-secrets old vs new over prose nobody wrote for it.

Input is kb/ISSUE-LOG.md pinned at de4e158 (the rev wq#2638's probe scored), read with
`git show`, so a re-run sees the same bytes. Two measurements per script:

  plain   - the log as-is. The new boundary must not change a single output byte
            (no new false positive from admitting `MASKED` as a boundary).
  seamed  - every line with `<residue><live value>` appended, for each residue x shape.
            Counts lines whose live body survives masking (a leak).

Prints counts only; fixtures are concatenated so no credential shape sits in source.
Usage: python3 probe_seam.py OLD_SCRIPT NEW_SCRIPT
"""
import subprocess
import sys

KB = "/home/lumen/kb"
REV = "de4e158"

LIVE = {
    "github": "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8",
    "aws": "AKIA" + "Q" * 16,
    "slack": "xoxb-" + "12345678901",
    "sk-key": "sk-ant-" + "abcdefghijklmnopqrstuvwx",
}
RESIDUES = ["ghp_MASKED", "MASKED"]


def run(script, text):
    return subprocess.run(["bash", script], input=text, capture_output=True,
                          text=True, check=True).stdout


def main(old, new):
    log = subprocess.run(["git", "-C", KB, "show", REV + ":ISSUE-LOG.md"],
                         capture_output=True, text=True, check=True).stdout
    lines = log.splitlines()
    print("input: ISSUE-LOG.md@%s, %d lines" % (REV, len(lines)))
    plain = {name: run(path, log) for name, path in (("old", old), ("new", new))}
    print("plain: output byte-identical old vs new:", plain["old"] == plain["new"])
    seamed = []
    for residue in RESIDUES:
        for shape, value in LIVE.items():
            seamed += [(shape, value, line + " " + residue + value) for line in lines]
    text = "\n".join(s for _, _, s in seamed) + "\n"
    for name, path in (("old", old), ("new", new)):
        out = run(path, text).splitlines()
        leaked = sum(1 for (_, value, _), o in zip(seamed, out) if value[-12:] in o)
        print("seamed %s: %d/%d lines leak the live body" % (name, leaked, len(seamed)))


if __name__ == "__main__":
    main(*sys.argv[1:3])
