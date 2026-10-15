#!/usr/bin/env python3
"""wq#3967 LIMPET OFRAK plugin CLI: the subprocess entry point the meta-server's
cli_wrapper (ops/wq3808-limpet-mcp-meta-server-a1/cli_wrapper.py) invokes per
tool. One subcommand per registered cli_tools entry; each prints a single JSON
object to stdout and exits 0, or prints an error to stderr and exits 1.
"""
from __future__ import annotations

import argparse
import json
import sys

import ofrak_ops


def _emit(result: dict) -> None:
    json.dump(result, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ofrak_cli.py")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("unpack")
    p.add_argument("input_path")
    p.add_argument("workdir")

    p = sub.add_parser("list-resources")
    p.add_argument("workdir")
    p.add_argument("--tag", default=None)

    p = sub.add_parser("patch-bytes")
    p.add_argument("workdir")
    p.add_argument("--offset", type=int, required=True)
    p.add_argument("--hex", dest="hex_bytes", required=True)
    p.add_argument("--resource-id", default=None)

    p = sub.add_parser("patch-strings")
    p.add_argument("workdir")
    p.add_argument("--old", dest="old_string", required=True)
    p.add_argument("--new", dest="new_string", required=True)
    p.add_argument("--resource-id", default=None)

    p = sub.add_parser("repack")
    p.add_argument("workdir")
    p.add_argument("output_path")

    args = parser.parse_args(argv)

    try:
        if args.command == "unpack":
            result = ofrak_ops.unpack(args.input_path, args.workdir)
        elif args.command == "list-resources":
            result = ofrak_ops.list_resources(args.workdir, tag=args.tag)
        elif args.command == "patch-bytes":
            result = ofrak_ops.patch_bytes(
                args.workdir, args.offset, args.hex_bytes, resource_id=args.resource_id
            )
        elif args.command == "patch-strings":
            result = ofrak_ops.patch_strings(
                args.workdir, args.old_string, args.new_string, resource_id=args.resource_id
            )
        elif args.command == "repack":
            result = ofrak_ops.repack(args.workdir, args.output_path)
        else:
            raise ofrak_ops.OfrakOpsError(f"unknown command {args.command!r}")
    except ofrak_ops.OfrakOpsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    _emit(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
