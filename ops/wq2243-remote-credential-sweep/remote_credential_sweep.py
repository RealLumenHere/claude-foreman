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
from dataclasses import dataclass, field

DEFAULT_ROOT = "/home/lumen"
DEFAULT_STATE = "/home/lumen/.local/state/remote-credential-sweep/paged-unstrippable"
NTFY_BIN = "/home/lumen/bin/ntfy-send"
EXCLUDE_DIR_NAMES = {"node_modules", ".venv", "venv", "__pycache__"}
URL_KEYS = ("url", "pushurl")

# Any http(s)/ftp URL with userinfo. ssh://git@host and scp-style git@host:path
# carry a username only, not a secret, so they are not matched.
SCHEME_RE = re.compile(r"^(?P<scheme>(?:https?|ftps?)://)(?P<rest>.*)$", re.I | re.S)
URL_PARTS_RE = re.compile(r"^(?P<base>[^?#]*)(?:\?(?P<query>[^#]*))?(?:#(?P<frag>.*))?$", re.S)
# A prefix only counts at a word boundary and with a token-length body, so repo
# names like `highs_solver` (`ghs_` inside a word) and `ghp_notes` (too short)
# are not tokens. Real bodies: gh?_ 36, github_pat_ 82, glpat- 20 chars.
TOKEN_PREFIX_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:(?:ghp_|gho_|ghs_|ghu_)[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{20,}|glpat-[A-Za-z0-9_-]{20,})"
)
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


_VALUE_ESCAPES = {"n": "\n", "t": "\t", "b": "\b"}


def _parse_value(raw):
    """Decode a raw git config value: quotes, backslash escapes, `#`/`;` comments.

    git quotes any value holding `#` or `;`, so `url = "https://u:pw#1@h/"`
    must be unquoted before SCHEME_RE can see the scheme.
    """
    out, quoted, protect, i = [], False, 0, 0
    while i < len(raw):
        c = raw[i]
        if c == "\\" and i + 1 < len(raw):
            i += 1
            out.append(_VALUE_ESCAPES.get(raw[i], raw[i]))
            protect = len(out)
        elif c == '"':
            quoted = not quoted
            protect = len(out)
        elif c in "#;" and not quoted:
            break
        else:
            out.append(c)
            if quoted:
                protect = len(out)
        i += 1
    while len(out) > protect and out[-1].isspace():
        out.pop()
    return "".join(out)


def _encode_value(val):
    """Inverse of _parse_value: quote when git would (`#`, `;`, edge whitespace)."""
    body = _escape(val)
    return f'"{body}"' if any(c in val for c in "#;") or val != val.strip() else body


@dataclass(frozen=True)
class Entry:
    """One URL-bearing config entry; lineno + span locate its text for rewriting."""
    name: str
    key: str
    url: str = field(repr=False)
    lineno: int
    span: tuple


def parse_urls(text):
    """Yield an Entry for every URL-bearing entry in git config text.

    Remote `url`/`pushurl` values yield (remote, key, decoded value); each
    repeated key is its own entry. A `[url "<sub>"]` header yields
    (`url#N`, URL_SECTION_KEY, sub) since its subsection IS a URL prefix; N is
    the header's ordinal, so the name never carries the secret. Handles
    `[remote "x"] url = ...` on one line.
    """
    section = sub = None
    n_url = 0
    for lineno, line in enumerate(text.splitlines()):
        base = 0
        s = SECTION_RE.match(line)
        if s:
            section = s.group("sec").lower()
            sub = _unescape(s.group("sub")) if s.group("sub") is not None else None
            if section == "url" and sub is not None:
                n_url += 1
                yield Entry(f"url#{n_url}", URL_SECTION_KEY, sub, lineno, s.span("sub"))
            base, line = s.start("rest"), s.group("rest")
        elif line.lstrip().startswith("["):
            section = sub = None
            continue
        k = KEY_RE.match(line)
        if section == "remote" and sub and k and k.group("key").lower() in URL_KEYS:
            a, b = k.span("val")
            yield Entry(sub, k.group("key").lower(), _parse_value(k.group("val")), lineno, (base + a, base + b))


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
    """Return [(entry, strippable)] for credential-bearing URLs in one config file."""
    try:
        with open(cfg, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return []
    return [(e, strippable(e.url)) for e in parse_urls(text) if has_credential(e.url)]


def rewrite_span(cfg, lineno, span, text):
    """Replace span of line `lineno` in cfg with text.

    Edited as text so the credential never travels through a child's argv, and
    so one of several repeated `url`/`pushurl` entries can be fixed alone.
    """
    try:
        with open(cfg, encoding="utf-8", errors="surrogateescape", newline="") as f:
            lines = f.read().splitlines(keepends=True)
        mode = os.stat(cfg).st_mode & 0o7777
    except OSError:
        return False
    if lineno >= len(lines):
        return False
    a, b = span
    lines[lineno] = lines[lineno][:a] + text + lines[lineno][b:]
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


def fix_config(cfg, entry):
    """Rewrite one URL entry token-free. Returns True on success."""
    with open(cfg, encoding="utf-8", errors="replace") as f:
        cur = next((e for e in parse_urls(f.read())
                    if (e.lineno, e.name, e.key) == (entry.lineno, entry.name, entry.key)), None)
    clean = strip_credentials(cur.url) if cur else None
    if clean is None or has_credential(clean):
        return False
    encode = _escape if cur.key == URL_SECTION_KEY else _encode_value
    return rewrite_span(cfg, cur.lineno, cur.span, encode(clean))


def scan(root, fix=False, out=sys.stdout, findings_out=None):
    """Report credential-bearing URLs; return the configs still unresolved.

    Every finding is appended to findings_out, if given, as
    (status, cfg, name, key) with status FIXED, EMBEDDED or UNSTRIPPABLE.
    """
    unresolved = []
    for cfg in iter_configs(root):
        for entry, can_strip in scan_config(cfg):
            fixed = fix and can_strip and fix_config(cfg, entry)
            status = "FIXED" if fixed else "EMBEDDED" if can_strip else "UNSTRIPPABLE"
            if not fixed:
                unresolved.append(cfg)
            if findings_out is not None:
                findings_out.append((status, cfg, entry.name, entry.key))
            print(f"{status} {cfg} remote={entry.name} key={entry.key}", file=out)
    return unresolved


def _repos(configs):
    return " ".join(sorted({c.rsplit("/", 2)[0] if c.endswith("/.git/config") else os.path.dirname(c) for c in configs}))


def notify(configs, fixed=(), stuck=()):
    """Page once per run with repo paths only (never the URL); True if a page went out.

    A FIXED URL still pages: the token stays valid until it is rotated.
    `stuck` is a token in the URL path, which no run can strip.
    """
    parts = []
    if configs:
        parts.append(f"credential in git remote URL: {_repos(configs)}")
    if fixed:
        parts.append(f"credential stripped from git remote URL, rotate the token: {_repos(fixed)}")
    if stuck:
        parts.append(f"credential in git remote URL path, cannot auto-strip (edit by hand, rotate the token; paged once): {_repos(stuck)}")
    if not parts:
        return False
    res = subprocess.run([NTFY_BIN, "-p", "high", "-T", "secret leak", "; ".join(parts)],
                         capture_output=True, check=False)
    return res.returncode == 0


def _load_state(path):
    try:
        with open(path, encoding="utf-8") as f:
            return {ln.rstrip("\n") for ln in f if ln.strip()}
    except OSError:
        return set()


def _save_state(path, keys):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("".join(f"{k}\n" for k in sorted(keys)))
    except OSError:
        pass


def notify_findings(findings, state_path):
    """Page for `findings`, but an UNSTRIPPABLE entry only the first time it is seen.

    An unstrippable URL never changes on its own, so re-paging it hourly is noise.
    The state file holds `config-path<TAB>remote<TAB>key` of entries already
    paged, pruned to the ones still present so a recurrence pages again.
    """
    seen = _load_state(state_path)
    stuck = {f"{c}\t{n}\t{k}": c for st, c, n, k in findings if st == "UNSTRIPPABLE"}
    new = {key: cfg for key, cfg in stuck.items() if key not in seen}
    embedded = [c for st, c, _, _ in findings if st == "EMBEDDED"]
    fixed = [c for st, c, _, _ in findings if st == "FIXED"]
    paged = notify(embedded, fixed, list(new.values()))
    keep = (seen & stuck.keys()) | (new.keys() if paged else set())
    if keep != seen:
        _save_state(state_path, keep)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan")
    s.add_argument("--root", default=DEFAULT_ROOT)
    s.add_argument("--fix", action="store_true", help="rewrite each URL token-free")
    s.add_argument("--notify", action="store_true",
                   help="ntfy the repo paths if any URL stayed embedded or was FIXED, or a path token is new")
    s.add_argument("--state", default=DEFAULT_STATE, help="where --notify remembers UNSTRIPPABLE entries it paged")
    args = ap.parse_args(argv)
    findings = []
    unresolved = scan(args.root, fix=args.fix, findings_out=findings)
    fixed = sum(1 for st, *_ in findings if st == "FIXED")
    print(f"remote-credential-sweep: embedded={len(unresolved)} fixed={fixed}")
    if args.notify:
        notify_findings(findings, args.state)
    return 1 if unresolved else 0


if __name__ == "__main__":
    sys.exit(main())
