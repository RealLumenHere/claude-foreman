#!/usr/bin/env python3
"""wq#2608 -- add the mcp-bes ingress rule to Bes's EXISTING locally-managed
cloudflared tunnel config, without disturbing any of its other hostnames.

Bes already runs a locally-managed tunnel for other services (ticket: "Bes
already has a locally-managed tunnel (config.yml + 2 tunnel creds already
present)") -- this is NOT a new tunnel, so the only change is one more
`ingress:` entry. cloudflared requires the catch-all (no `hostname` key) rule
to be last; this inserts the new rule immediately before it so any existing
catch-all keeps working unchanged.

Usage (as root on Bes):
    python3 cloudflared_ingress_patch.py /etc/cloudflared/config.yml \\
        --hostname mcp-bes.lumennode.uk --service http://localhost:3100

Idempotent: if a rule for --hostname already exists, this reports it and
exits 0 without writing anything (safe to re-run apply.sh).

Backup: writes <config>.pre-wq2608.bak the FIRST time only (first backup wins
-- it is the pre-wq2608 baseline), matching ops/wq2605-control-plane-isolation
/apply.sh's backup() convention.
"""
import argparse
import shutil
import sys
from pathlib import Path

import yaml


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("config_path")
    ap.add_argument("--hostname", required=True)
    ap.add_argument("--service", required=True)
    args = ap.parse_args()

    cfg_path = Path(args.config_path)
    if not cfg_path.exists():
        print(f"REFUSING: {cfg_path} does not exist", file=sys.stderr)
        return 1

    backup_path = cfg_path.with_name(cfg_path.name + ".pre-wq2608.bak")
    if not backup_path.exists():
        shutil.copy2(cfg_path, backup_path)
        print(f"backed up {cfg_path} -> {backup_path}")
    else:
        print(f"backup already exists at {backup_path} (not overwritten)")

    raw = cfg_path.read_text()
    cfg = yaml.safe_load(raw) or {}

    if "ingress" not in cfg or not isinstance(cfg["ingress"], list):
        print(f"REFUSING: {cfg_path} has no top-level 'ingress:' list -- "
              "this script only knows how to extend an existing ingress config, "
              "not create a tunnel from scratch (that's the Anubis path, wq#2607).",
              file=sys.stderr)
        return 1

    ingress = cfg["ingress"]

    for rule in ingress:
        if isinstance(rule, dict) and rule.get("hostname") == args.hostname:
            print(f"NOOP: {args.hostname} already has an ingress rule "
                  f"(service={rule.get('service')}) -- not changed.")
            return 0

    new_rule = {"hostname": args.hostname, "service": args.service}

    catch_all_idx = None
    for i, rule in enumerate(ingress):
        if isinstance(rule, dict) and "hostname" not in rule:
            catch_all_idx = i
            break

    if catch_all_idx is None:
        print("WARNING: no catch-all rule (entry without 'hostname') found -- "
              "appending at the end. cloudflared requires a catch-all to be "
              "present and last; verify config.yml manually after this runs.",
              file=sys.stderr)
        ingress.append(new_rule)
    else:
        ingress.insert(catch_all_idx, new_rule)

    cfg_path.write_text(yaml.safe_dump(cfg, default_flow_style=False, sort_keys=False))
    print(f"added ingress rule: {args.hostname} -> {args.service} "
          f"(before catch-all at index {catch_all_idx})" if catch_all_idx is not None
          else f"added ingress rule: {args.hostname} -> {args.service} (appended)")
    print("run `cloudflared tunnel ingress validate --config "
          f"{cfg_path}` before restarting the service.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
