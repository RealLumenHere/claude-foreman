#!/usr/bin/env python3
"""wq#4445: reconcile the shipped selfheal.yaml seed with the live operator copy.

install.sh seeds /home/lumen/agents/fleet-selfheal/selfheal.yaml only when the live
file is absent (the live copy is the operator's to edit), so keys added to the repo
seed after first deploy never reached the live config: wq#4439 found live
nodes.notes = [hathor, nephthys] against the seed's 7 entries, with --verify green
the whole time because it only checked the file existed.

Contract (class fix, not instance):
  --check    read-only. One `MISS <keypath>` line per seed key path missing from
             live, exit 1. Exit 0 when every seed key path exists live. Exit 2 when
             either file is unreadable or not valid YAML. Live-only keys are the
             operator's and print as `INFO live-only: <keypath>` without failing.
  (default)  merge every missing key into live: mapping keys recursively, plus set
             membership for the operator lists nodes.ignore / nodes.sleepers.
             Existing live keys are NEVER overwritten — on values the operator
             always wins. Backs the live file up first, round-trips with
             ruamel.yaml so the operator's comments survive, and validates that
             the merged result parses before an atomic replace. No restart needed:
             the sweep re-reads the file on every run.
  --dry-run  print what the merge would add; write nothing.

Comments attached to seed entries ride along where ruamel carries them (mapping
values); plain list members are appended bare — their rationale lives in the repo
seed, which stays the documented copy.
"""
import argparse
import os
import shutil
import sys
import time

import yaml
from ruamel.yaml import YAML

# The only sequences whose MEMBERSHIP is part of the shipped contract. Other lists
# (guards.timers, crashloop.never_stop, ...) are compared as whole keys only: the
# key must exist live, its contents are the operator's.
MERGE_LIST_PATHS = {("nodes", "ignore"), ("nodes", "sleepers")}


def _fmt(path):
    return ".".join(str(p) for p in path)


def missing_in(seed, live, path=()):
    """Key paths present in `seed` but absent in `live`.

    Returns [(path_tuple, kind)]: kind "key" for a mapping key (merge copies the
    whole seed subtree), "member" for a MERGE_LIST_PATHS list entry. Does not
    recurse into a missing key — copying the subtree covers its children. Where
    live reshaped a mapping into something else, the subtree is the operator's
    and is left alone.
    """
    out = []
    if isinstance(seed, dict):
        if not isinstance(live, dict):
            return out
        for k, v in seed.items():
            if k not in live:
                out.append((path + (k,), "key"))
            else:
                out.extend(missing_in(v, live[k], path + (k,)))
    elif path in MERGE_LIST_PATHS and isinstance(seed, list):
        live_items = live if isinstance(live, list) else []
        for item in seed:
            if item not in live_items:
                out.append((path + (str(item),), "member"))
    return out


def live_only(seed, live, path=()):
    """Key paths in live that the seed does not ship (informational only)."""
    out = []
    if isinstance(live, dict) and isinstance(seed, dict):
        for k, v in live.items():
            if k not in seed:
                out.append(path + (k,))
            else:
                out.extend(live_only(seed[k], v, path + (k,)))
    return out


def _nav(doc, path):
    for p in path:
        doc = doc[p]
    return doc


def apply_merge(seed_doc, live_doc, missing):
    """Copy each missing entry from seed_doc into live_doc, in place."""
    added = []
    for path, kind in missing:
        parent = _nav(live_doc, path[:-1])
        if kind == "key":
            parent[path[-1]] = _nav(seed_doc, path)
            added.append(path)
        else:  # member of a MERGE_LIST_PATHS list; path[:-1] is the list itself
            seq = parent
            if seq is None:
                grand = _nav(live_doc, path[:-2])
                grand[path[-2]] = []
                seq = grand[path[-2]]
            if not isinstance(seq, list):
                print(f"WARN not a list live, skipping: {_fmt(path[:-1])}")
                continue
            seq.append(path[-1])
            added.append(path)
    return added


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--seed", required=True)
    ap.add_argument("--live", required=True)
    ap.add_argument("--check", action="store_true", help="read-only drift check")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    rt = YAML()
    rt.preserve_quotes = True
    try:
        with open(args.seed) as f:
            seed_doc = rt.load(f)
    except Exception as e:
        print(f"ERR seed unreadable or invalid YAML: {args.seed}: {e}")
        return 2
    try:
        with open(args.live) as f:
            live_doc = rt.load(f)
    except Exception as e:
        print(f"ERR live unreadable or invalid YAML: {args.live}: {e}")
        return 2
    if not isinstance(seed_doc, dict) or not isinstance(live_doc, dict):
        print("ERR seed or live is not a YAML mapping")
        return 2

    missing = missing_in(seed_doc, live_doc)
    for p in live_only(seed_doc, live_doc):
        print(f"INFO live-only: {_fmt(p)}")

    if args.check:
        for path, _ in missing:
            print(f"MISS seed key not live: {_fmt(path)}")
        return 1 if missing else 0

    if not missing:
        print("nothing to merge: live has every seed key")
        return 0
    if args.dry_run:
        for path, _ in missing:
            print(f"would add: {_fmt(path)}")
        return 0

    backup = f"{args.live}.bak.{time.strftime('%Y%m%dT%H%M%S')}"
    shutil.copy2(args.live, backup)

    added = apply_merge(seed_doc, live_doc, missing)
    tmp = f"{args.live}.tmp.{os.getpid()}"
    try:
        with open(tmp, "w") as f:
            rt.dump(live_doc, f)
        with open(tmp) as f:
            yaml.safe_load(f)  # merged result must still parse
        shutil.copymode(args.live, tmp)
        os.replace(tmp, args.live)
    except Exception as e:
        print(f"ERR merged result invalid, live untouched (backup {backup}): {e}")
        if os.path.exists(tmp):
            os.unlink(tmp)
        return 2
    for path in added:
        print(f"added: {_fmt(path)}")
    print(f"backup: {backup}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
