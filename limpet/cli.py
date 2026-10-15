"""`limpet` CLI -- the operator/agent front door to the registry.

Non-interactive, predictable, --json on every subcommand so an agent can
parse it. Subcommands map to the lifecycle:

  limpet validate <path>     check a limpet-plugin.toml before installing it
  limpet list                installed plugins + effective state (reads unified.json)
  limpet enable <name>       lifecycle step 4: toggle on,  then reconcile
  limpet disable <name>      lifecycle step 4: toggle off, then reconcile
  limpet health [name]       run health checks now (all, or one)
  limpet reconcile           scan + health + resolve + write unified.json
  limpet unified             print the last unified manifest

Exit 0 on success, 1 on a usage/not-found error, 2 on validation failure, so
scripts can branch on the code. Ref: design/wq3810_limpet_plugin_system.md.
"""
from __future__ import annotations

import argparse
import json
import sys

from manifest import ManifestError, parse_manifest
from registry import Registry


def _emit(obj, as_json):
    if as_json:
        print(json.dumps(obj, indent=2, sort_keys=True))


def _cmd_validate(args, reg):
    try:
        with open(args.path, encoding="utf-8") as fh:
            manifest = parse_manifest(fh.read())
    except ManifestError as exc:
        _emit({"path": args.path, "valid": False, "errors": exc.errors}, args.json)
        if not args.json:
            print(f"INVALID {args.path}:", file=sys.stderr)
            for err in exc.errors:
                print(f"  - {err}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"error: cannot read {args.path}: {exc}", file=sys.stderr)
        return 1
    _emit({"path": args.path, "valid": True, "name": manifest.name,
           "version": manifest.version, "kind": manifest.kind,
           "category": manifest.category}, args.json)
    if not args.json:
        print(f"OK {manifest.name} {manifest.version} ({manifest.kind})")
    return 0


def _cmd_list(args, reg):
    unified = reg.reconcile(run_health=not args.no_health)
    plugins = unified["plugins"]
    if args.category:
        plugins = {n: p for n, p in plugins.items() if p["category"] == args.category}
    shown = dict(unified, plugins=plugins)
    _emit(shown, args.json)
    if not args.json:
        if not plugins:
            print("no plugins installed" if not args.category
                  else f"no plugins in category {args.category!r}")
        for name, p in plugins.items():
            suffix = f"  ({p['reason']})" if p["reason"] else ""
            cat = f"/{p['category']}" if p["category"] else ""
            print(f"{p['state']:>9}  {name} {p['version']} [{p['kind']}{cat}]{suffix}")
        if not args.category:
            for path, errs in unified["rejected"].items():
                print(f"REJECTED {path}: {'; '.join(errs)}", file=sys.stderr)
    return 0


def _cmd_toggle(args, reg, value):
    report = reg.scan()
    if args.name not in report.loaded:
        installed = ", ".join(sorted(report.loaded)) or "(none)"
        print(f"error: no installed plugin named {args.name!r}. installed: {installed}",
              file=sys.stderr)
        return 1
    reg.set_enabled(args.name, value)
    unified = reg.reconcile(run_health=not args.no_health)
    _emit(unified["plugins"].get(args.name, {}), args.json)
    if not args.json:
        p = unified["plugins"].get(args.name, {})
        print(f"{args.name} -> {'enabled' if value else 'disabled'} "
              f"(effective: {p.get('state', '?')})")
    return 0


def _cmd_health(args, reg):
    report = reg.scan()
    only = None
    if args.name:
        if args.name not in report.loaded:
            print(f"error: no installed plugin named {args.name!r}", file=sys.stderr)
            return 1
        only = {args.name}
    results = reg.refresh_health(report.loaded, only=only)
    shown = {k: v for k, v in results.items() if only is None or k in only}
    _emit(shown, args.json)
    if not args.json:
        for name, ok in sorted(shown.items()):
            print(f"{'ok' if ok else 'FAIL':>4}  {name}")
    return 0


def _cmd_reconcile(args, reg):
    unified = reg.reconcile(run_health=not args.no_health)
    _emit(unified, args.json)
    if not args.json:
        print(f"reconciled {len(unified['plugins'])} plugin(s); "
              f"active: {', '.join(unified['start_order']) or '(none)'}")
    return 0


def _cmd_unified(args, reg):
    try:
        with open(reg.unified_path, encoding="utf-8") as fh:
            print(fh.read(), end="")
    except OSError:
        print("error: no unified manifest yet; run `limpet reconcile`", file=sys.stderr)
        return 1
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog="limpet", description="LIMPET plugin registry control")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--plugins-dir", help="override drop-in dir (default /etc/limpet/plugins.d)")
    p.add_argument("--state-dir", help="override state dir (default /var/lib/limpet)")
    p.add_argument("--no-health", action="store_true",
                   help="skip running health checks during reconcile")
    sub = p.add_subparsers(dest="command", required=True)

    sv = sub.add_parser("validate", help="validate a limpet-plugin.toml")
    sv.add_argument("path")
    sv.set_defaults(func=_cmd_validate)

    sl = sub.add_parser("list", help="list installed plugins + state")
    sl.add_argument("--category", help="show only plugins in this category (discovery filter)")
    sl.set_defaults(func=_cmd_list)

    se = sub.add_parser("enable", help="enable a plugin")
    se.add_argument("name")
    se.set_defaults(func=lambda a, r: _cmd_toggle(a, r, True))

    sd = sub.add_parser("disable", help="disable a plugin")
    sd.add_argument("name")
    sd.set_defaults(func=lambda a, r: _cmd_toggle(a, r, False))

    sh = sub.add_parser("health", help="run health checks now")
    sh.add_argument("name", nargs="?")
    sh.set_defaults(func=_cmd_health)

    sub.add_parser("reconcile", help="scan + health + resolve + write unified.json").set_defaults(
        func=_cmd_reconcile)
    sub.add_parser("unified", help="print the last unified manifest").set_defaults(
        func=_cmd_unified)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    reg = Registry(plugins_dir=args.plugins_dir, state_dir=args.state_dir)
    return args.func(args, reg)


if __name__ == "__main__":
    raise SystemExit(main())
