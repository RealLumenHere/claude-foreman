#!/usr/bin/env python3
"""agents_apply — lint + apply for harness/agents.yaml (wq#1301, foreman pattern D).

kubectl-apply-shaped on purpose (kb/fleet-architecture-ideas.md pattern D): `lint`
validates the spec (schema + drift against research/models.yaml routing) and exits
nonzero on any problem; `apply` lints, then writes a content-addressed lock file
(harness/agents.lock.json) recording one spec_hash per archetype plus an overall
hash, so a reviewer/PR diff shows exactly what changed and CI can fail a PR that
edited agents.yaml without regenerating the lock.

Usage:
    agents_apply.py lint
    agents_apply.py apply [--out PATH] [--check]   # --check: fail if apply would
                                                     # change the on-disk lock file
"""
import argparse
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agents_spec

HARNESS_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_LOCK_PATH = os.path.join(HARNESS_DIR, "agents.lock.json")


def _archetype_hash(arch):
    blob = json.dumps({
        "primary_tuple": arch.primary_tuple,
        "max_turns": arch.max_turns,
        "wall_clock_timeout_s": arch.wall_clock_timeout_s,
        "prompt_template": arch.prompt_template,
        "add_dirs": arch.add_dirs,
        "local_allowed": arch.local_allowed,
        "local_max_turns": arch.local_max_turns,
    }, sort_keys=True).encode()
    return hashlib.sha256(blob).hexdigest()


def render_lock(spec):
    archetypes = {}
    for name in spec.names():
        archetypes[name] = {"spec_hash": _archetype_hash(spec.get(name))}
    overall = hashlib.sha256(
        json.dumps(archetypes, sort_keys=True).encode()).hexdigest()
    return {"source": os.path.relpath(spec.path, HARNESS_DIR), "overall_hash": overall,
            "archetypes": archetypes}


def cmd_lint(args):
    spec = agents_spec.load(force=True)
    problems = agents_spec.lint(spec)
    if problems:
        for p in problems:
            print(f"PROBLEM: {p}", file=sys.stderr)
        print(f"agents.yaml: {len(problems)} problem(s)", file=sys.stderr)
        return 1
    print(f"agents.yaml: clean ({len(spec.names())} archetypes)")
    return 0


def cmd_apply(args):
    rc = cmd_lint(args)
    if rc != 0:
        return rc
    spec = agents_spec.load()
    lock = render_lock(spec)
    out_path = args.out or DEFAULT_LOCK_PATH
    new_blob = json.dumps(lock, indent=2, sort_keys=True) + "\n"

    old_blob = None
    if os.path.isfile(out_path):
        with open(out_path) as f:
            old_blob = f.read()

    if args.check:
        if old_blob != new_blob:
            print(f"agents.lock.json is stale — run `agents_apply.py apply` "
                  f"(no --check) to regenerate", file=sys.stderr)
            return 1
        print("agents.lock.json: up to date")
        return 0

    if old_blob == new_blob:
        print(f"agents.lock.json: unchanged ({lock['overall_hash'][:12]})")
        return 0

    tmp = out_path + ".tmp"
    with open(tmp, "w") as f:
        f.write(new_blob)
    os.replace(tmp, out_path)
    print(f"agents.lock.json: applied ({lock['overall_hash'][:12]}) -> {out_path}")
    return 0


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("lint").set_defaults(func=cmd_lint)
    ap = sub.add_parser("apply")
    ap.add_argument("--out", default=None)
    ap.add_argument("--check", action="store_true",
                     help="fail if apply would change the on-disk lock file, without writing")
    ap.set_defaults(func=cmd_apply)
    args = p.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
