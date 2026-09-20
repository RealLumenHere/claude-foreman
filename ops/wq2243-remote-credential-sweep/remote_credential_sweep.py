#!/usr/bin/env python3
"""remote_credential_sweep.py — find (and optionally strip) credentials embedded
in git remote URLs anywhere under a root (wq#2243).

The class this closes: a clone whose `remote.<name>.url` is
`https://<token>@github.com/...` or `https://user:<token>@host/...`. The token
is then printed by `git remote -v` and lands in any transcript that lists
remotes. bin/lumen-secret-check only matched a few known token prefixes under
`find -maxdepth 4`; it missed opaque tokens, `user:pass@` URLs, deep scratch
trees and linked worktrees. This walks the whole tree, resolves `.git` files
(worktrees/submodules) to the real config, and flags any http(s)/ftp URL with
userinfo regardless of token format.

Mask before print (instinct 3): output is `config-path remote-name key` only.
The credential value is never printed, logged or returned by any function
that reaches stdout.

Usage:
  remote_credential_sweep.py scan [--root DIR] [--fix] [--notify]   exit 1 if any remain
"""
import argparse
import os
import re
import subprocess
import sys

DEFAULT_ROOT = "/home/lumen"
NTFY_BIN = "/home/lumen/bin/ntfy-send"
EXCLUDE_DIR_NAMES = {"node_modules", ".venv", "venv", "__pycache__"}
URL_KEYS = ("url", "pushurl")

# Any http(s)/ftp URL with userinfo. ssh://git@host and scp-style git@host:path
# carry a username only, not a secret, so they are not matched.
USERINFO_RE = re.compile(r"^(?P<scheme>(?:https?|ftps?)://)(?P<userinfo>[^/@\s]+)@(?P<rest>.+)$", re.I)
TOKEN_PREFIX_RE = re.compile(r"(ghp_|github_pat_|gho_|ghs_|ghu_|glpat-)[A-Za-z0-9_-]+")
SECTION_RE = re.compile(r'^\s*\[remote\s+"(?P<name>[^"]+)"\s*\]\s*$')
KEY_RE = re.compile(r"^\s*(?P<key>[A-Za-z]+)\s*=\s*(?P<val>.*?)\s*$")


def strip_userinfo(url):
    """Return url without its userinfo, or None if it has no embedded credential."""
    m = USERINFO_RE.match(url)
    if not m:
        return None
    return m.group("scheme") + m.group("rest")


def has_credential(url):
    return strip_userinfo(url) is not None or bool(TOKEN_PREFIX_RE.search(url))


def parse_remote_urls(text):
    """Yield (remote_name, key, url) for every remote url/pushurl in config text."""
    remote = None
    for line in text.splitlines():
        s = SECTION_RE.match(line)
        if s:
            remote = s.group("name")
            continue
        if line.lstrip().startswith("["):
            remote = None
            continue
        k = KEY_RE.match(line)
        if remote and k and k.group("key").lower() in URL_KEYS:
            yield remote, k.group("key").lower(), k.group("val")


def resolve_config(git_entry):
    """Map a `.git` dir or gitdir-file to the config that holds its remotes."""
    if os.path.isdir(git_entry):
        return os.path.join(git_entry, "config")
    try:
        with open(git_entry, encoding="utf-8", errors="replace") as f:
            first = f.readline().strip()
    except OSError:
        return None
    if not first.startswith("gitdir:"):
        return None
    gitdir = first.split(":", 1)[1].strip()
    if not os.path.isabs(gitdir):
        gitdir = os.path.normpath(os.path.join(os.path.dirname(git_entry), gitdir))
    common = os.path.join(gitdir, "commondir")
    if os.path.isfile(common):
        with open(common, encoding="utf-8", errors="replace") as f:
            gitdir = os.path.normpath(os.path.join(gitdir, f.read().strip()))
    return os.path.join(gitdir, "config")


def iter_configs(root):
    """Yield every distinct git config file under root, any depth, incl. bare repos."""
    seen = set()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIR_NAMES]
        candidates = []
        if ".git" in dirnames:
            candidates.append(os.path.join(dirpath, ".git"))
            dirnames.remove(".git")
        elif ".git" in filenames:
            candidates.append(os.path.join(dirpath, ".git"))
        elif "HEAD" in filenames and "config" in filenames and "objects" in dirnames:
            candidates.append(dirpath)
        for c in candidates:
            cfg = resolve_config(c)
            if cfg and os.path.isfile(cfg):
                real = os.path.realpath(cfg)
                if real not in seen:
                    seen.add(real)
                    yield real


def scan_config(cfg):
    """Return [(remote, key)] for credential-bearing URLs in one config file."""
    try:
        with open(cfg, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return []
    return [(r, k) for r, k, u in parse_remote_urls(text) if has_credential(u)]


def fix_config(cfg, remote, key):
    """Rewrite one remote URL token-free via git itself. Returns True on success."""
    with open(cfg, encoding="utf-8", errors="replace") as f:
        url = next((u for r, k, u in parse_remote_urls(f.read()) if r == remote and k == key), None)
    clean = strip_userinfo(url) if url else None
    if clean is None:
        return False
    res = subprocess.run(
        ["git", "config", "--file", cfg, f"remote.{remote}.{key}", clean],
        capture_output=True, check=False,
    )
    return res.returncode == 0


def scan(root, fix=False, out=sys.stdout):
    """Report credential-bearing remotes; return the configs still embedded."""
    unresolved = []
    for cfg in iter_configs(root):
        for remote, key in scan_config(cfg):
            fixed = fix and fix_config(cfg, remote, key)
            if not fixed:
                unresolved.append(cfg)
            print(f"{'FIXED' if fixed else 'EMBEDDED'} {cfg} remote={remote} key={key}", file=out)
    return unresolved


def notify(configs):
    """Page once per run with repo paths only (never the URL)."""
    repos = " ".join(sorted({c.rsplit("/", 2)[0] if c.endswith("/.git/config") else os.path.dirname(c) for c in configs}))
    subprocess.run([NTFY_BIN, "-p", "high", "-T", "secret leak", f"credential in git remote URL: {repos}"],
                   capture_output=True, check=False)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan")
    s.add_argument("--root", default=DEFAULT_ROOT)
    s.add_argument("--fix", action="store_true", help="rewrite each URL token-free")
    s.add_argument("--notify", action="store_true", help="ntfy the repo paths if any stay embedded")
    args = ap.parse_args(argv)
    unresolved = scan(args.root, fix=args.fix)
    print(f"remote-credential-sweep: embedded={len(unresolved)}")
    if unresolved and args.notify:
        notify(unresolved)
    return 1 if unresolved else 0


if __name__ == "__main__":
    sys.exit(main())
