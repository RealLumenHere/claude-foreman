#!/usr/bin/env python3
"""wq#3097 item (c): scan our own callers for the 3 breaking shapes in upstream litellm PR 36011
(the fix that ships in v1.96.2 / GHSA-3cv6-jpf6-8222).

PR 36011 ("fix(proxy)!: apply request-parameter checks consistently across body, path and form
inputs") makes three previously-permissive request shapes start being refused:

  1. path    - a model/deployment name resolved from the URL PATH (Azure-style
               /openai/deployments/{model}/...) that parses as an http(s) destination is now
               refused, unless the host is in litellm_settings.provider_url_destination_allowed_hosts.
  2. bracket - a multipart form field written in bracket notation, e.g. litellm_metadata[api_base],
               is now subject to the same restricted-parameter rules as its JSON-body equivalent,
               unless general_settings.allow_client_side_credentials or the deployment's
               configurable_clientside_auth_params allow it.
  3. health  - POST /health/test_connection naming a configured model no longer lets the request
               override that model's routing/credential params; the request must supply a full
               connection of its own instead.

This does NOT call litellm or any network service. It is a static grep over source we control,
looking for callers that would break under the new checks, plus the litellm_settings /
general_settings knobs that pre-emptively widen the allowlists (a caller-side and a config-side
check, run together since a config exemption also proves a caller needs one).
"""
import argparse
import os
import re
import sys

PATTERNS = {
    "path": re.compile(r"/openai/deployments/\{|/engines/\{|deployments/['\"]?\s*\+|/deployments/%s"),
    "bracket": re.compile(r"litellm_metadata\["),
    "health": re.compile(r"/health/test_connection"),
}
CONFIG_KNOBS = re.compile(
    r"provider_url_destination_allowed_hosts|allow_client_side_credentials|configurable_clientside_auth_params"
)
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".tmp", "_runs"}
TEXT_EXT = {
    ".py", ".sh", ".js", ".ts", ".json", ".yaml", ".yml", ".md", ".txt", ".cfg", ".conf",
}

SELF_DIR = os.path.dirname(os.path.abspath(__file__))


def iter_files(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".claude")]
        if os.path.abspath(dirpath) == SELF_DIR:
            continue
        for fn in filenames:
            ext = os.path.splitext(fn)[1]
            if ext in TEXT_EXT:
                yield os.path.join(dirpath, fn)


def scan(roots):
    hits = {"path": [], "bracket": [], "health": [], "config_knob": []}
    for root in roots:
        if not os.path.isdir(root):
            continue
        for path in iter_files(root):
            try:
                with open(path, "r", errors="ignore") as f:
                    text = f.read()
            except OSError:
                continue
            for line_no, line in enumerate(text.splitlines(), 1):
                for shape, pat in PATTERNS.items():
                    if pat.search(line):
                        hits[shape].append(f"{path}:{line_no}: {line.strip()[:160]}")
                if CONFIG_KNOBS.search(line):
                    hits["config_knob"].append(f"{path}:{line_no}: {line.strip()[:160]}")
    return hits


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("roots", nargs="*", default=[
        "/home/lumen/worktrees/wq3097-a4",
        "/home/lumen/agents/work-queue",
        "/home/lumen/mailbot",
        "/home/lumen/pokemon",
        "/home/lumen/bin",
        "/home/lumen/kb",
        "/home/lumen/projects/nest",
    ], help="directories to scan (default: this worker's granted dirs)")
    args = ap.parse_args()
    hits = scan(args.roots)
    total = sum(len(v) for v in hits.values())
    for shape in ("path", "bracket", "health"):
        label = {"path": "1. path-valued deployment name", "bracket": "2. litellm_metadata[bracket] form field",
                  "health": "3. /health/test_connection"}[shape]
        print(f"== {label}: {len(hits[shape])} hit(s)")
        for h in hits[shape]:
            print(f"   {h}")
    print(f"== config knobs already set (allow_client_side_credentials / configurable_clientside_auth_params / "
          f"provider_url_destination_allowed_hosts): {len(hits['config_knob'])} hit(s)")
    for h in hits["config_knob"]:
        print(f"   {h}")
    print(f"TOTAL={total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
