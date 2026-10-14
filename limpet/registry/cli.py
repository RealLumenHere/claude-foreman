"""wq#3807 — ``limpet-registry`` command-line entry point.

Subcommands:
  build       rebuild the unified tools.json from the drop-in directory
  validate    check every drop-in manifest, exit nonzero on any problem
  list        list registered tools, optionally filtered by capability
  register    publish a tool's manifest into the drop-in directory
  unregister  remove a tool's manifest (on uninstall)
  import      seed the drop-in directory from tool-catalog.json
  watch       poll the drop-in directory and rebuild on change
  health      run each tool's health_check and report pass/fail

Run ``limpet-registry <subcommand> --help`` for options.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import health as health_mod
from . import register as register_mod
from .import_catalog import DEFAULT_CATALOG, import_catalog
from .registry import DEFAULT_MANIFEST, DEFAULT_TOOLS_DIR, load_tool_dir, rebuild
from .watch import watch


def _add_dir_args(parser, *, manifest=True):
    parser.add_argument("--tools-dir", default=DEFAULT_TOOLS_DIR,
                        help=f"drop-in directory (default: {DEFAULT_TOOLS_DIR})")
    if manifest:
        parser.add_argument("--manifest", default=DEFAULT_MANIFEST,
                            help=f"unified manifest path (default: {DEFAULT_MANIFEST})")


def _cmd_build(args) -> int:
    manifest, errors = rebuild(args.tools_dir, args.manifest, force=args.force)
    for err in errors:
        print(f"error: {err}", file=sys.stderr)
    print(f"{manifest['tool_count']} tool(s) -> {args.manifest} "
          f"({len(manifest['capabilities'])} distinct capabilities)")
    return 1 if errors else 0


def _cmd_validate(args) -> int:
    tools, errors = load_tool_dir(args.tools_dir)
    for err in errors:
        print(f"error: {err}", file=sys.stderr)
    print(f"{len(tools)} valid, {len(errors)} problem(s)")
    return 1 if errors else 0


def _cmd_list(args) -> int:
    tools, errors = load_tool_dir(args.tools_dir)
    for err in errors:
        print(f"error: {err}", file=sys.stderr)
    for tool in tools:
        if args.capability and args.capability not in tool["capabilities"]:
            continue
        caps = ",".join(tool["capabilities"])
        print(f"{tool['name']:<24} {tool['path']:<32} [{caps}]")
    return 0


def _cmd_register(args) -> int:
    dest = register_mod.register_from_file(args.source, args.tools_dir,
                                           overwrite=not args.no_overwrite)
    print(f"registered -> {dest}")
    return 0


def _cmd_unregister(args) -> int:
    removed = register_mod.unregister_tool(args.name, args.tools_dir)
    print(f"{'removed' if removed else 'not found'}: {args.name}")
    return 0 if removed else 1


def _cmd_import(args) -> int:
    written, errors = import_catalog(args.catalog, args.tools_dir,
                                     synth_health_check=not args.no_synth_health_check)
    for err in errors:
        print(f"skip: {err}", file=sys.stderr)
    print(f"imported {len(written)} tool(s), skipped {len(errors)}")
    return 0


def _cmd_watch(args) -> int:
    watch(args.tools_dir, args.manifest, interval=args.interval)
    return 0


def _cmd_health(args) -> int:
    tools, errors = load_tool_dir(args.tools_dir)
    for err in errors:
        print(f"error: {err}", file=sys.stderr)
    if args.tool:
        tools = [t for t in tools if t["name"] == args.tool]
        if not tools:
            print(f"no such tool: {args.tool}", file=sys.stderr)
            return 1
    results = health_mod.check_tools(tools, timeout=args.timeout)
    if args.json:
        print(json.dumps(results, indent=2))
    else:
        for r in results:
            status = "ok" if r["ok"] else "FAIL"
            tail = "" if r["ok"] else f"  ({r['reason']})"
            print(f"{status:<4} {r['name']}{tail}")
    return 0 if all(r["ok"] for r in results) else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="limpet-registry",
        description="LIMPET tool-registry manifest: build, validate, and query "
                    "the machine-readable catalog of installed RE tools.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("build", help="rebuild the unified manifest")
    _add_dir_args(p)
    p.add_argument("--force", action="store_true", help="rewrite even if unchanged")
    p.set_defaults(func=_cmd_build)

    p = sub.add_parser("validate", help="validate every drop-in manifest")
    _add_dir_args(p, manifest=False)
    p.set_defaults(func=_cmd_validate)

    p = sub.add_parser("list", help="list registered tools")
    _add_dir_args(p, manifest=False)
    p.add_argument("--capability", help="only tools with this capability")
    p.set_defaults(func=_cmd_list)

    p = sub.add_parser("register", help="publish a tool manifest into the drop-in dir")
    _add_dir_args(p, manifest=False)
    p.add_argument("source", help="path to the tool's YAML manifest")
    p.add_argument("--no-overwrite", action="store_true",
                   help="fail if the tool is already registered")
    p.set_defaults(func=_cmd_register)

    p = sub.add_parser("unregister", help="remove a tool manifest")
    _add_dir_args(p, manifest=False)
    p.add_argument("name", help="tool name to remove")
    p.set_defaults(func=_cmd_unregister)

    p = sub.add_parser("import", help="seed the drop-in dir from tool-catalog.json")
    _add_dir_args(p, manifest=False)
    p.add_argument("--catalog", default=DEFAULT_CATALOG,
                   help=f"catalog path (default: {DEFAULT_CATALOG})")
    p.add_argument("--no-synth-health-check", action="store_true",
                   help="do not derive a default health_check from path")
    p.set_defaults(func=_cmd_import)

    p = sub.add_parser("watch", help="poll the drop-in dir and rebuild on change")
    _add_dir_args(p)
    p.add_argument("--interval", type=float, default=2.0, help="poll seconds")
    p.set_defaults(func=_cmd_watch)

    p = sub.add_parser("health", help="run each tool's health_check")
    _add_dir_args(p, manifest=False)
    p.add_argument("--tool", help="only this tool")
    p.add_argument("--timeout", type=float, default=20.0, help="per-check seconds")
    p.add_argument("--json", action="store_true", help="emit JSON results")
    p.set_defaults(func=_cmd_health)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
