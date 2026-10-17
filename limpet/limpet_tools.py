"""`limpet-tools` CLI -- discovery, trust, and install for the plugin registry.

The operator/agent front door to docs/plugin-registry-spec.md (wq#4094),
sibling to cli.py (which owns the on-box lifecycle). Non-interactive,
--json everywhere, exit 0 ok / 1 usage-or-not-found / 2 trust-or-validation
failure, matching cli.py's convention.

  limpet-tools index add <git-url> --name N [--ceiling T]   register an index
  limpet-tools index remove|list|sync                       manage indexes
  limpet-tools search <terms> [--category --tag --tier --hardware]
  limpet-tools info <name>            full index record + local trust status
  limpet-tools install <name|git-url> [--rev SHA]
  limpet-tools verify <name>          re-verify an installed plugin now
  limpet-tools elevate <name> [--revoke]   per-plugin privilege grant (§7.2)

Install is fail-closed end to end: the name is checked against the strict
registry grammar BEFORE anything is written, the clone is checked out at the
pinned rev, the tree hash is recomputed and (for listed plugins) must match
the index record and verify against the tier's trust roots -- any failure
leaves nothing installed. A direct git URL installs at the `unlisted` floor
tier with a warning, sandboxed by tier policy until elevated.
Ref: docs/plugin-registry-spec.md §2-§6, registry_index.py, registry_trust.py.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

from manifest import ManifestError, parse_manifest
from plugin_registry import DEFAULT_PLUGINS_DIR, DEFAULT_STATE_DIR
import registry_index
import registry_trust


def _state_dir(args):
    return args.state_dir or os.environ.get("LIMPET_STATE_DIR") or DEFAULT_STATE_DIR


def _plugins_dir(args):
    return args.plugins_dir or os.environ.get("LIMPET_PLUGINS_DIR") or DEFAULT_PLUGINS_DIR


def _emit(obj, as_json):
    if as_json:
        print(json.dumps(obj, indent=2, sort_keys=True))


def _git(args_list, cwd=None):
    return subprocess.run(["git"] + args_list, cwd=cwd, capture_output=True,
                          text=True, timeout=300)


# ---- index management ----------------------------------------------------
def _cmd_index(args):
    state = _state_dir(args)
    if args.index_command == "add":
        try:
            entry = registry_index.add_index(state, args.name, args.git_url,
                                             ceiling=args.ceiling)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        synced, detail = registry_index.sync_index(state, entry)
        _emit({"added": entry, "synced": synced, "detail": detail}, args.json)
        if not args.json:
            print(f"added index {args.name!r} (ceiling {args.ceiling}); {detail}")
        return 0 if synced else 2
    if args.index_command == "remove":
        if not registry_index.remove_index(state, args.name):
            print(f"error: no index named {args.name!r}", file=sys.stderr)
            return 1
        _emit({"removed": args.name}, args.json)
        if not args.json:
            print(f"removed index {args.name!r}")
        return 0
    if args.index_command == "list":
        indexes = registry_index.load_config(state)
        _emit({"indexes": indexes}, args.json)
        if not args.json:
            for e in indexes:
                stamp = ("never synced" if not e.get("last_synced") else
                         time.strftime("%Y-%m-%d %H:%M",
                                       time.localtime(e["last_synced"])))
                print(f"{e['name']:<16} ceiling={e['ceiling']:<10} {stamp}  {e['git']}")
            if not indexes:
                print("no indexes configured; `limpet-tools index add <git-url> --name canonical`")
        return 0
    # sync
    results = {}
    for entry in registry_index.load_config(state):
        synced, detail = registry_index.sync_index(state, entry)
        results[entry["name"]] = {"synced": synced, "detail": detail}
        if not args.json:
            print(f"{entry['name']}: {detail}")
    _emit(results, args.json)
    return 0


def _synced_records(args):
    """Merged records, after an offline-tolerant refresh (spec §4.1: pull on
    search when online, else the last sync). --offline skips the pull."""
    state = _state_dir(args)
    if not getattr(args, "offline", False):
        for entry in registry_index.load_config(state):
            registry_index.sync_index(state, entry)
    return registry_index.load_records(state)


# ---- discovery -----------------------------------------------------------
def _cmd_search(args):
    records = registry_index.search(
        _synced_records(args), query=" ".join(args.terms),
        category=args.category, tag=args.tag, tier=args.tier,
        hardware=args.hardware)
    _emit({"results": records}, args.json)
    if not args.json:
        if not records:
            print("no plugins matched")
        for rec in records:
            signed = "signed" if rec.get("signature") else "UNSIGNED"
            print(f"{rec['tier']:>9}  {rec['name']:<20} {rec.get('version', '?'):<9} "
                  f"[{rec.get('category', '?')}] {signed}  {rec.get('description', '')}")
    return 0


def _cmd_info(args):
    rec = registry_index.info(_synced_records(args), args.name)
    if rec is None:
        print(f"error: {args.name!r} is not in any configured index", file=sys.stderr)
        return 1
    receipt = None
    if registry_trust.safe_name(args.name):
        try:
            receipt = registry_trust.load_receipt(_state_dir(args), args.name)
        except registry_trust.TrustError:
            receipt = None  # malformed receipt surfaces via `verify`, not here
    shown = dict(rec)
    shown["installed"] = receipt is not None
    if receipt is not None:
        shown["installed_rev"] = receipt.rev
        shown["installed_tier"] = receipt.tier
        shown["elevated"] = receipt.elevated
    _emit(shown, args.json)
    if not args.json:
        for key in ("name", "namespace", "version", "tier", "license", "git",
                    "rev", "description", "index"):
            print(f"{key:>12}: {rec.get(key, '')}")
        print(f"{'signature':>12}: "
              + ("present" if rec.get("signature") else "NONE (not installable as listed tier)"))
        print(f"{'installed':>12}: "
              + (f"yes (rev {receipt.rev[:12]}, tier {receipt.tier})" if receipt else "no"))
    return 0


# ---- install -------------------------------------------------------------
def _looks_like_git_url(target: str) -> bool:
    return "://" in target or target.endswith(".git") or ":" in target.split("/")[0]


def _clone_pinned(git_url: str, rev, dest_parent: str):
    """Clone into a temp dir under dest_parent and check out the pinned rev
    (or resolve HEAD when rev is None). Returns (tmp_tree, resolved_rev) or
    raises RuntimeError with the git failure."""
    tmp = tempfile.mkdtemp(dir=dest_parent, prefix=".install-")
    proc = _git(["clone", git_url, tmp])
    if proc.returncode != 0:
        shutil.rmtree(tmp, ignore_errors=True)
        raise RuntimeError(f"git clone failed: {proc.stderr.strip()[:300]}")
    if rev:
        proc = _git(["checkout", "--detach", rev], cwd=tmp)
        if proc.returncode != 0:
            shutil.rmtree(tmp, ignore_errors=True)
            raise RuntimeError(f"cannot check out pinned rev {rev}: {proc.stderr.strip()[:200]}")
    proc = _git(["rev-parse", "HEAD"], cwd=tmp)
    if proc.returncode != 0:
        shutil.rmtree(tmp, ignore_errors=True)
        raise RuntimeError("cannot resolve HEAD of clone")
    return tmp, proc.stdout.strip()


def _cmd_install(args):
    state = _state_dir(args)
    plugins_dir = _plugins_dir(args)
    rec, warnings = None, []

    if _looks_like_git_url(args.target):
        git_url, rev, tier = args.target, args.rev, "unlisted"
        warnings.append(
            "direct git install: this plugin is in no configured index -- it "
            "installs at the unlisted floor tier (sandboxed, no privilege "
            "until `limpet-tools elevate`); review its install hooks yourself")
    else:
        rec = registry_index.info(_synced_records(args), args.target)
        if rec is None:
            print(f"error: {args.target!r} is not in any configured index; "
                  "pass a git URL for a direct install", file=sys.stderr)
            return 1
        git_url, rev, tier = rec["git"], args.rev or rec["rev"], rec["tier"]
        if tier != "unlisted" and not rec.get("signature"):
            print(f"error: index lists {args.target!r} at tier {tier!r} with no "
                  "signature; refusing (trust is content-bound, spec §5.2)",
                  file=sys.stderr)
            return 2

    # Reject-unsafe-name BEFORE any write (spec §5.2.5).
    if rec is not None:
        name = rec["name"]
    else:
        # Derive the name from the repo basename for a direct URL install.
        name = args.name or os.path.basename(git_url.rstrip("/")).removesuffix(".git")
    if not registry_trust.safe_name(name):
        print(f"error: unsafe plugin name {name!r} "
              f"(must match {registry_trust.REGISTRY_NAME_RE.pattern}); "
              "nothing was written", file=sys.stderr)
        return 2

    store = os.path.join(state, "plugins")
    os.makedirs(store, exist_ok=True)
    try:
        tmp_tree, resolved = _clone_pinned(git_url, rev, store)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    try:
        if rev and resolved != rev and not resolved.startswith(rev):
            print(f"error: checkout resolved {resolved}, not pinned rev {rev}",
                  file=sys.stderr)
            return 2
        actual_hash = registry_trust.tree_hash(tmp_tree)

        sig_block = (rec or {}).get("signature") or {}
        if rec is not None:
            if sig_block.get("tree_hash") != actual_hash:
                print(f"error: tree hash of {resolved[:12]} is {actual_hash} but the "
                      f"index pinned {sig_block.get('tree_hash')!r}; refusing "
                      "(content was changed after signing)", file=sys.stderr)
                return 2
            payload = registry_trust.signing_payload(name, git_url, resolved, actual_hash)
            keys = registry_trust.roots_for_tier(
                tier, registry_trust.load_trust_roots(args.trust_dir),
                sig_block.get("pubkey", ""))
            try:
                ok = any(registry_trust.minisign_verify(payload, sig_block.get("sig", ""), key)
                         for key in keys)
            except registry_trust.TrustError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 2
            if not ok:
                print(f"error: signature does not verify against any tier-{tier} "
                      "trust root; refusing", file=sys.stderr)
                return 2

        manifest_path = os.path.join(tmp_tree, "limpet-plugin.toml")
        try:
            with open(manifest_path, encoding="utf-8") as fh:
                manifest = parse_manifest(fh.read())
        except OSError:
            print("error: repo has no limpet-plugin.toml at its root", file=sys.stderr)
            return 2
        except ManifestError as exc:
            print(f"error: invalid limpet-plugin.toml: {'; '.join(exc.errors)}",
                  file=sys.stderr)
            return 2
        if manifest.name != name:
            print(f"error: manifest [plugin].name {manifest.name!r} does not match "
                  f"the installed name {name!r}", file=sys.stderr)
            return 2

        # All gates passed -- commit: move the tree into place, write the
        # receipt, then the drop-in (the registry picks it up on reconcile).
        final_tree = os.path.join(store, name)
        if os.path.isdir(final_tree):
            shutil.rmtree(final_tree)
        os.replace(tmp_tree, final_tree)
        tmp_tree = None
        receipt = registry_trust.Receipt(
            name=name, git=git_url, rev=resolved, tree_hash=actual_hash,
            tier=tier, tree=final_tree,
            scheme=sig_block.get("scheme", "minisign"),
            pubkey_id=sig_block.get("pubkey_id", ""),
            pubkey=sig_block.get("pubkey", ""),
            sig=sig_block.get("sig", ""),
            installed_at=time.time())
        registry_trust.save_receipt(state, receipt)
        os.makedirs(plugins_dir, exist_ok=True)
        with open(os.path.join(final_tree, "limpet-plugin.toml"), encoding="utf-8") as src:
            manifest_text = src.read()
        with open(os.path.join(plugins_dir, f"{name}.toml"), "w", encoding="utf-8") as dst:
            dst.write(manifest_text)
    finally:
        if tmp_tree and os.path.isdir(tmp_tree):
            shutil.rmtree(tmp_tree, ignore_errors=True)

    _emit({"installed": name, "rev": resolved, "tier": tier,
           "tree_hash": actual_hash, "warnings": warnings}, args.json)
    if not args.json:
        for warning in warnings:
            print(f"warning: {warning}", file=sys.stderr)
        print(f"installed {name} at {resolved[:12]} (tier {tier}); "
              "run `limpet reconcile` to activate")
    return 0


# ---- verify / elevate ------------------------------------------------------
def _cmd_verify(args):
    state = _state_dir(args)
    if not registry_trust.safe_name(args.name):
        print(f"error: unsafe plugin name {args.name!r}", file=sys.stderr)
        return 1
    receipt = registry_trust.load_receipt(state, args.name)
    if receipt is None:
        print(f"error: {args.name!r} has no install receipt (hand-installed "
              "drop-ins have nothing to verify against)", file=sys.stderr)
        return 1
    try:
        errors = registry_trust.verify_receipt(receipt, trust_dir=args.trust_dir)
    except registry_trust.TrustError as exc:
        errors = [str(exc)]
    _emit({"name": args.name, "trusted": not errors, "tier": receipt.tier,
           "rev": receipt.rev, "errors": errors}, args.json)
    if not args.json:
        if errors:
            print(f"UNTRUSTED {args.name}:", file=sys.stderr)
            for err in errors:
                print(f"  - {err}", file=sys.stderr)
        else:
            print(f"ok {args.name} (tier {receipt.tier}, rev {receipt.rev[:12]})")
    return 2 if errors else 0


def _cmd_elevate(args):
    state = _state_dir(args)
    if not registry_trust.safe_name(args.name):
        print(f"error: unsafe plugin name {args.name!r}", file=sys.stderr)
        return 1
    receipt = registry_trust.load_receipt(state, args.name)
    if receipt is None:
        print(f"error: {args.name!r} has no install receipt; install it via "
              "limpet-tools first", file=sys.stderr)
        return 1
    receipt = dataclasses.replace(receipt, elevated=not args.revoke)
    registry_trust.save_receipt(state, receipt)
    verb = "revoked elevation for" if args.revoke else "elevated"
    _emit({"name": args.name, "elevated": receipt.elevated}, args.json)
    if not args.json:
        print(f"{verb} {args.name} (tier {receipt.tier}); privilege requests "
              + ("are refused again" if args.revoke else
                 "may now pass tier policy -- energize confirms still apply"))
    return 0


# ---- parser ---------------------------------------------------------------
def build_parser():
    p = argparse.ArgumentParser(
        prog="limpet-tools", description="LIMPET plugin registry: discovery, trust, install")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--state-dir", help="override state dir (default /var/lib/limpet)")
    p.add_argument("--plugins-dir", help="override drop-in dir (default /etc/limpet/plugins.d)")
    p.add_argument("--trust-dir", help="override trust-root dir (default /etc/limpet/trust)")
    p.add_argument("--offline", action="store_true",
                   help="skip index git pulls; use the last sync")
    sub = p.add_subparsers(dest="command", required=True)

    si = sub.add_parser("index", help="manage federated index repos")
    isub = si.add_subparsers(dest="index_command", required=True)
    ia = isub.add_parser("add", help="register an index git repo")
    ia.add_argument("git_url")
    ia.add_argument("--name", required=True, help="local name for the index")
    ia.add_argument("--ceiling", default="community", choices=("core", "vetted", "community"),
                    help="max tier this index may confer (default community)")
    isub.add_parser("remove", help="unregister an index").add_argument("name")
    isub.add_parser("list", help="show configured indexes")
    isub.add_parser("sync", help="git-pull every index now")
    si.set_defaults(func=_cmd_index)

    ss = sub.add_parser("search", help="search the merged local indexes")
    ss.add_argument("terms", nargs="*")
    ss.add_argument("--category")
    ss.add_argument("--tag")
    ss.add_argument("--tier", choices=("core", "vetted", "community"))
    ss.add_argument("--hardware", help='HAL id, e.g. "usb:1d50:6002"')
    ss.set_defaults(func=_cmd_search)

    so = sub.add_parser("info", help="full index record + install status")
    so.add_argument("name")
    so.set_defaults(func=_cmd_info)

    sn = sub.add_parser("install", help="install a plugin by index name or git URL")
    sn.add_argument("target", help="plugin name (index-resolved) or git URL (unlisted)")
    sn.add_argument("--rev", help="pin a specific commit SHA")
    sn.add_argument("--name", help="plugin name for a direct git-URL install")
    sn.set_defaults(func=_cmd_install)

    sv = sub.add_parser("verify", help="re-verify an installed plugin now")
    sv.add_argument("name")
    sv.set_defaults(func=_cmd_verify)

    se = sub.add_parser("elevate", help="grant (or revoke) per-plugin privilege")
    se.add_argument("name")
    se.add_argument("--revoke", action="store_true")
    se.set_defaults(func=_cmd_elevate)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
