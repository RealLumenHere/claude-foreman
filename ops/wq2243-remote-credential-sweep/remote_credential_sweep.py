#!/usr/bin/env python3
"""remote_credential_sweep.py — find (and optionally strip) credentials embedded
in git remote URLs anywhere under a root (wq#2243, hardened wq#2257).

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
SCHEME_RE = re.compile(r"^(?P<scheme>(?:https?|ftps?)://)(?P<rest>.*)$", re.I | re.S)
URL_PARTS_RE = re.compile(r"^(?P<base>[^?#]*)(?:\?(?P<query>[^#]*))?(?:#(?P<frag>.*))?$", re.S)
TOKEN_PREFIX_RE = re.compile(r"(ghp_|github_pat_|gho_|ghs_|ghu_|glpat-)[A-Za-z0-9_-]+")
# `[section]`, `[section "sub"]`, optionally followed on the same line by `key = val`.
SECTION_RE = re.compile(r'^\s*\[\s*(?P<sec>[A-Za-z][A-Za-z0-9-]*)(?:\s+"(?P<sub>(?:[^"\\]|\\.)*)")?\s*\](?P<rest>.*)$')
KEY_RE = re.compile(r"^\s*(?P<key>[A-Za-z]+)\s*=\s*(?P<val>.*?)\s*$")
URL_SECTION_KEY = "url-section"  # pseudo-key: the credential sits in a `[url "..."]` header


def strip_userinfo(url):
    """Return url without its userinfo, or None if it has no embedded credential.

    Userinfo ends at the LAST `@` before the first `/`, so a raw `@` in a
    password (https://user:pa@ss@host/) cannot leave a fragment behind.
    """
    m = SCHEME_RE.match(url)
    if not m:
        return None
    rest = m.group("rest")
    slash = rest.find("/")
    authority, tail = (rest, "") if slash < 0 else (rest[:slash], rest[slash:])
    at = authority.rfind("@")
    if at < 1:
        return None
    return m.group("scheme") + authority[at + 1:] + tail


def strip_query_tokens(url):
    """Drop query params / fragment carrying a known token prefix; None if none did."""
    m = URL_PARTS_RE.match(url)
    base, query, frag = m.group("base", "query", "frag")
    changed = False
    if query is not None:
        params = query.split("&")
        kept = [p for p in params if not TOKEN_PREFIX_RE.search(p)]
        changed = len(kept) != len(params)
        query = "&".join(kept)
    if frag is not None and TOKEN_PREFIX_RE.search(frag):
        frag, changed = None, True
    if not changed:
        return None
    return base + (f"?{query}" if query else "") + (f"#{frag}" if frag is not None else "")


def strip_credentials(url):
    """url minus userinfo and token-bearing query/fragment, or None if nothing changed."""
    out = strip_userinfo(url) or url
    out = strip_query_tokens(out) or out
    return None if out == url else out


def has_credential(url):
    return strip_userinfo(url) is not None or bool(TOKEN_PREFIX_RE.search(url))


def strippable(url):
    """True if stripping leaves a token-free URL (a token in the path cannot be)."""
    clean = strip_credentials(url)
    return clean is not None and not has_credential(clean)


def _unescape(sub):
    return re.sub(r"\\(.)", r"\1", sub)


def _escape(sub):
    return sub.replace("\\", "\\\\").replace('"', '\\"')


def parse_urls(text):
    """Yield (name, key, url) for every URL-bearing entry in git config text.

    Remote `url`/`pushurl` values yield (remote, key, value). A `[url "<sub>"]`
    header yields (`url#N`, URL_SECTION_KEY, sub) since its subsection IS a URL
    prefix; N is the header's ordinal, so the name never carries the secret.
    Handles `[remote "x"] url = ...` on one line.
    """
    section = sub = None
    n_url = 0
    for line in text.splitlines():
        s = SECTION_RE.match(line)
        if s:
            section = s.group("sec").lower()
            sub = _unescape(s.group("sub")) if s.group("sub") is not None else None
            if section == "url" and sub is not None:
                n_url += 1
                yield f"url#{n_url}", URL_SECTION_KEY, sub
            line = s.group("rest")
        elif line.lstrip().startswith("["):
            section = sub = None
            continue
        k = KEY_RE.match(line)
        if section == "remote" and sub and k and k.group("key").lower() in URL_KEYS:
            yield sub, k.group("key").lower(), k.group("val")


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
    """Return [(name, key, strippable)] for credential-bearing URLs in one config file."""
    try:
        with open(cfg, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return []
    return [(n, k, strippable(u)) for n, k, u in parse_urls(text) if has_credential(u)]


def rewrite_url_header(cfg, name, clean):
    """Replace the subsection of the `[url "..."]` header numbered `name` with clean.

    Edited as text so the credential never travels through a child's argv.
    """
    try:
        with open(cfg, encoding="utf-8", errors="surrogateescape", newline="") as f:
            lines = f.read().splitlines(keepends=True)
        mode = os.stat(cfg).st_mode & 0o7777
    except OSError:
        return False
    n = 0
    for i, line in enumerate(lines):
        s = SECTION_RE.match(line)
        if s and s.group("sec").lower() == "url" and s.group("sub") is not None:
            n += 1
            if f"url#{n}" == name:
                a, b = s.span("sub")
                lines[i] = line[:a] + _escape(clean) + line[b:]
                break
    else:
        return False
    tmp = cfg + ".sweep-tmp"
    try:
        with open(tmp, "w", encoding="utf-8", errors="surrogateescape", newline="") as f:
            f.write("".join(lines))
        os.chmod(tmp, mode)
        os.replace(tmp, cfg)
    except OSError:
        if os.path.exists(tmp):
            os.unlink(tmp)
        return False
    return True


def fix_config(cfg, name, key):
    """Rewrite one URL entry token-free. Returns True on success."""
    with open(cfg, encoding="utf-8", errors="replace") as f:
        url = next((u for n, k, u in parse_urls(f.read()) if n == name and k == key), None)
    clean = strip_credentials(url) if url else None
    if clean is None or has_credential(clean):
        return False
    if key == URL_SECTION_KEY:
        return rewrite_url_header(cfg, name, clean)
    res = subprocess.run(
        ["git", "config", "--file", cfg, f"remote.{name}.{key}", clean],
        capture_output=True, check=False,
    )
    return res.returncode == 0


def scan(root, fix=False, out=sys.stdout, fixed_out=None):
    """Report credential-bearing URLs; return the configs still embedded.

    Configs whose credential was stripped are appended to fixed_out if given.
    """
    unresolved = []
    for cfg in iter_configs(root):
        for name, key, can_strip in scan_config(cfg):
            fixed = fix and can_strip and fix_config(cfg, name, key)
            if fixed and fixed_out is not None:
                fixed_out.append(cfg)
            if not fixed:
                unresolved.append(cfg)
            status = "FIXED" if fixed else "EMBEDDED" if can_strip else "UNSTRIPPABLE"
            print(f"{status} {cfg} remote={name} key={key}", file=out)
    return unresolved


def _repos(configs):
    return " ".join(sorted({c.rsplit("/", 2)[0] if c.endswith("/.git/config") else os.path.dirname(c) for c in configs}))


def notify(configs, fixed=()):
    """Page once per run with repo paths only (never the URL).

    A FIXED URL still pages: the token stays valid until it is rotated.
    """
    parts = []
    if configs:
        parts.append(f"credential in git remote URL: {_repos(configs)}")
    if fixed:
        parts.append(f"credential stripped from git remote URL, rotate the token: {_repos(fixed)}")
    if parts:
        subprocess.run([NTFY_BIN, "-p", "high", "-T", "secret leak", "; ".join(parts)],
                       capture_output=True, check=False)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan")
    s.add_argument("--root", default=DEFAULT_ROOT)
    s.add_argument("--fix", action="store_true", help="rewrite each URL token-free")
    s.add_argument("--notify", action="store_true", help="ntfy the repo paths if any URL stayed embedded or was FIXED")
    args = ap.parse_args(argv)
    fixed = []
    unresolved = scan(args.root, fix=args.fix, fixed_out=fixed)
    print(f"remote-credential-sweep: embedded={len(unresolved)} fixed={len(fixed)}")
    if args.notify and (unresolved or fixed):
        notify(unresolved, fixed)
    return 1 if unresolved else 0


if __name__ == "__main__":
    sys.exit(main())
