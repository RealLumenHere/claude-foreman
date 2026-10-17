"""Enumerate every license-shaped blob in a GitHub recursive-tree dump.

For per-component license/SBOM sweeps of repos that vendor upstream projects
wholesale. wq#4088 audited a 20,827-entry repo by listing its tree correctly and
then reconciling that listing by hand: 7 of 19 nested LICENSE files made it into
the table and the tail was summarised as "each permissive", which hid a complete
78-file GPL-3.0 project (ISSUE-LOG #1383). Account for every hit by name instead.

Usage:
    curl -s "https://api.github.com/repos/<owner>/<repo>/git/trees/<sha>?recursive=1" -o tree.json
    python3 -I tools/license-sweep/tree_license_scan.py tree.json [path/prefix/ ...]

Prints the tree's truncation flag and entry count (a truncated tree cannot be
swept at all), then one line per license-shaped blob, then counts. Any extra
arguments are path prefixes whose entries are counted separately — use them to
size a suspected vendored subtree.
"""

import json
import sys

LICENSE_STEMS = ("license", "licence", "copying")


def license_blobs(tree):
    for node in tree:
        if node["type"] != "blob":
            continue
        base = node["path"].rsplit("/", 1)[-1].lower()
        if base.split(".", 1)[0] in LICENSE_STEMS:
            yield node["path"]


def main(argv):
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    with open(argv[1], encoding="utf-8") as fh:
        tree = json.load(fh)
    entries = tree["tree"]
    print("truncated:", tree.get("truncated"), "entries:", len(entries))

    hits = sorted(license_blobs(entries))
    for path in hits:
        print(path)
    nested = [path for path in hits if "/" in path]
    print("license-shaped blobs:", len(hits), "(root:", len(hits) - len(nested),
          "nested:", len(nested), ")")
    print("distinct nested directories:",
          len({path.rsplit("/", 1)[0] for path in nested}))

    for prefix in argv[2:]:
        print(
            "entries under", prefix + ":",
            sum(1 for node in entries if node["path"].startswith(prefix)),
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
