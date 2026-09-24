#!/usr/bin/env python3
"""read_dir_sweep.py — wq#1441 (research/BROKER_GRANT_COVERAGE.md §2.1).

Deterministic secret sweep of the directories fleet-exec-policy.yaml `allow.read_dirs`
grants as `needs:dir(...)` read context. A read grant is the worker's `--add-dir`, i.e.
every file under the root is readable by the worker's own uid, so the question a root
has to answer before it goes on the allow list is "is there a credential anywhere in
here?" — file:line, not a feeling. wq#1466 is the counter-example this exists for:
`dir(/home/lumen/sentinel/config)` was auto-granted and the dir carried 38 literal
bearer tokens.

Regex + basename globs only, no LLM. Every hit is printed MASKED (the matched token is
cut to its first 4 characters before anything is written to stdout) — a credential
printed is a credential burned (instincts tier 0 MASK BEFORE PRINT).

    python3 harness/read_dir_sweep.py <root> [<root>...]      # human table, exit 1 on hits
    python3 harness/read_dir_sweep.py --policy [--json]       # every allow.read_dirs root
"""
import datetime
import fnmatch
import json
import os
import re
import subprocess
import sys

HARNESS_DIR = os.path.dirname(os.path.abspath(__file__))
POLICY_PATH = os.environ.get("FOREMAN_FLEET_EXEC_POLICY",
                             os.path.join(HARNESS_DIR, "fleet-exec-policy.yaml"))
RUNTIME_PATH = os.environ.get("FOREMAN_READ_DIR_SWEEP_RUNTIME",
                              os.path.join(HARNESS_DIR, "runtime", "read_dir_sweep.json"))
AQ_BIN = os.environ.get("FOREMAN_AQ_BIN", "/home/lumen/bin/aq")
AQ_SOURCE = "read-dir-sweep"

# file names that are a credential by convention regardless of content
BASENAME_GLOBS = ("*.env", ".env*", "*.token", "*.key", "*.pem", "id_*", "*.p12", "*.pfx",
                  "*.kdbx", ".netrc", ".git-credentials", "*.secret", "*.secrets")
# wq#2114: these globs matched the canonical name and nothing else, so every BACKUP of a
# credential file was invisible -- `.git-credentials.bak.20260830_124941_pre_rotate_finish`
# matched no glob at all. That is not an edge case: `.bak.<TS>_pre_<reason>` is the fleet's
# universal convention before any risky edit (sentinel/config alone carries 24 copies of
# one config), so the convention that exists to make changes safe was quietly producing
# credential copies no basename rule could see. Strip a trailing backup suffix and re-test.
BACKUP_SUFFIX = re.compile(
    r"(?:\.bak[-.][^/]*|\.bak|~|\.orig|\.save|\.old|\.dpkg-(?:old|dist|new)|\.rpmsave)$",
    re.IGNORECASE)


def basename_candidates(name):
    """The name itself, plus what it is a backup OF. Applied repeatedly so a doubly
    suffixed copy (`id_ed25519.bak.20260101.orig`) still resolves to its origin."""
    seen = [name]
    cur = name
    for _ in range(4):
        stripped = BACKUP_SUFFIX.sub("", cur)
        if stripped == cur or not stripped:
            break
        seen.append(stripped)
        cur = stripped
    return seen
# wq#2115: the label half of `kv_secret` is shape-based, not an enumerated keyword list.
# A human pasting a credential dump labels it however the source service's docs print it —
# `Signing Secret:`, `Verifaction Token:` (sic) — so any `<word> secret|token|key` counts,
# and the words may be separated by a SPACE, not only [_-]. An exact list missed 3 live
# credentials on the same line it caught one on (messenger leak, wq#1483).
KV_NOUN = r"(?:secret|token|key|credentials?|passphrase|password|passwd)"
# one qualifier word, ended by a separator OR a camelCase hump (`client_`, `Signing `,
# `client` of `clientSecret`) — the hump test is case-SENSITIVE inside the (?i) pattern,
# which is what keeps `monkey:`/`hockey:` out while letting `apiKey:` in. Two letters
# minimum: a one-letter qualifier is an English article, and `a key: …`/`not a token: …`
# prose was 79 of the 93 lines this rule newly matched across the worker add-dirs.
KV_WORD = r"[A-Za-z][A-Za-z0-9]{1,20}(?:[ _-]{1,2}|(?-i:(?=[A-Z])))"
# a public key is not a credential, and infra dirs are full of them
KV_NOT_PUBLIC = r"(?!pub(?:lic)?[ _-]{0,2}key\b)"
# bare `key` is deliberately absent here: `key:` alone is ordinary YAML/JSON, it needs a
# qualifier word in front of it (`api key`, `signing key`) to read as a credential label.
KV_BARE = r"(?:secret|token|credentials?|passphrase|password|passwd|bearer)"
KV_LABEL = (r"(?:" + KV_WORD + r")?" + KV_NOT_PUBLIC + KV_WORD + KV_NOUN + r"|" + KV_BARE)
# an env-var reference or a path to a key file is a POINTER to a credential, not one:
# `secret = process.env.X`, `master key: /home/lumen/.ssh/id_ed25519`. The key file
# itself is caught by BASENAME_GLOBS where it lives.
KV_NOT_A_CREDENTIAL = (r"(?!os\.environ)(?!os\.getenv)(?!(?:process\.)?env\.)"
                       r"(?!\$\{?[A-Z_])(?![~.]{0,2}/)(?!<)"
                       # wq#2122: allowing a quoted label opened a new pointer shape --
                       # `{"x-internal-key": BILLING_INTERNAL_KEY}` and
                       # `{"env_key": "AZURE_OPENAI_API_KEY"}`, where the value is a
                       # constant or the NAME of an env var, not its value. Both were
                       # false positives in the first --policy run of this change.
                       # Requires all-caps AND an underscore AND no lowercase, so a
                       # hex/base64 credential (which carries mixed case or no
                       # underscore) is still caught.
                       # terminator, not end-of-line: the pointer is usually mid-line
                       # (`{"x-internal-key": BILLING_INTERNAL_KEY}, timeout=3)`). Using a
                       # terminator also means a credential that merely STARTS with an
                       # all-caps run (`AAAA_BBBB-ccc123`) is still caught, because `-` is
                       # not a terminator.
                       r"(?![A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+(?:[\"'`,;\s)}\]]|$))")
# wq#2122: the label used to have to touch its separator, so the single most common
# credential shape in the fleet -- standard JSON -- was invisible: in `"api_key": "<val>"`
# the closing quote sits between the label and the colon. Same for a markdown-bold label
# (`*Client Secret*:`, how Slack prints one) and a backtick-wrapped value.
KV_LABEL_CLOSE = r"['\"*`]?"
# `Signing Secret is: <val>` -- a connector word between label and colon. Enumerated, not
# `\w+`, so an arbitrary sentence cannot bridge a prose `secret` to an unrelated long token.
KV_CONNECTOR = r"(?:\s+(?:is|was|=))?"
# `Signing Secret - <val>`. The dash REQUIRES surrounding whitespace: a bare `-` would make
# `api-key-rotation-procedure-doc` read as label `api-key`, separator `-`, value the rest.
KV_SEP = r"(?:\s*[:=]\s*|\s+[-\u2013]\s+)"
# content shapes; first match per line wins. `kv_secret` needs a value of 20+ chars so
# `api_key: os.environ/X` (LiteLLM's env-ref form) and `api_key: sk-none` do not fire.
PATTERNS = (
    # wq#2114: ghr_/ghu_ were absent here but present in the token-engine broker's copy —
    # the three scanners disagreed on which GitHub prefixes exist. Widened to the union.
    ("gh_pat", re.compile(
        r"\b(?:ghp_|github_pat_|gho_|ghs_|ghr_|ghu_|glpat-)[A-Za-z0-9_-]{20,}")),
    ("sntl", re.compile(r"\bsntl-p[A-Za-z0-9_-]{8,}")),
    ("sk_key", re.compile(r"\bsk-(?:proj-|ant-|or-v1-|lf-)?[A-Za-z0-9_-]{24,}")),
    ("aws", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("slack", re.compile(r"\b(?:xox[bpsa]|xapp)-[A-Za-z0-9-]{20,}")),
    ("privkey", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY")),
    # wq#2479: a credential embedded in a URL, scheme://user:secret@host. No label sits in
    # front of the value, so kv_secret cannot see it (its label half never engages) and none
    # of the other patterns has a URL shape. The match is the WHOLE `scheme://user:secret@`
    # span: mask() keeps 4 chars and the scheme plus `://` is already 5+, so no character of
    # the userinfo can reach the excerpt. Not a credential: an empty password, a template or
    # env-ref (`${T}`, `$T`, `{pw}`, `%s`, `<pw>`, `os.environ`, ALL_CAPS_NAME) and the
    # literal placeholder words docs use. `secret{8,}` keeps `user:pw@` and `a:b@` out.
    ("url_basic_auth", re.compile(
        r"\b[A-Za-z][A-Za-z0-9+.\-]+://[^\s:/@'\"`]{1,64}:"
        r"(?![${%<*.]|(?i:password|passwd|secret|changeme|example|redacted|masked|xxxx+)@)"
        r"(?!os\.environ|os\.getenv|(?:process\.)?env\.)"
        r"(?![A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+@)"
        r"[^\s@/'\"`]{8,}@(?=[A-Za-z0-9\[])")),
    ("kv_secret", re.compile(
        r"(?i)\b(?:" + KV_LABEL + r")\b" + KV_LABEL_CLOSE + KV_CONNECTOR + KV_SEP +
        r"['\"`]?" + KV_NOT_A_CREDENTIAL + r"[A-Za-z0-9_\-./+]{20,}")),
)
SKIP_DIRS = frozenset({".git", "node_modules", "__pycache__", ".venv", "venv",
                       "site-packages", ".cache"})
MAX_FILE_BYTES = 2_000_000
MASK_KEEP = 4


def mask(token):
    return token[:MASK_KEEP] + "…" if len(token) > MASK_KEEP else "…"


def _read_text(path):
    try:
        if os.path.islink(path) or os.path.getsize(path) > MAX_FILE_BYTES:
            return None
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return None
    if b"\0" in data[:4096]:
        return None
    return data.decode("utf-8", "replace")


def sweep_root(root):
    """[(path, line, kind, masked_excerpt)] for one root. Deterministic order."""
    hits = []
    nfiles = 0
    for dp, dn, fn in os.walk(root):
        dn[:] = sorted(d for d in dn if d not in SKIP_DIRS)
        for f in sorted(fn):
            p = os.path.join(dp, f)
            nfiles += 1
            if any(fnmatch.fnmatch(c, g)
                   for c in basename_candidates(f) for g in BASENAME_GLOBS):
                hits.append((p, 0, "basename", f))
            text = _read_text(p)
            if text is None:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                for kind, rx in PATTERNS:
                    m = rx.search(line)
                    if m:
                        # mask BEFORE truncating: a cut token would escape the replace
                        masked = line.replace(m.group(0), mask(m.group(0)))
                        hits.append((p, i, kind, masked.strip()[:120]))
                        break
    return hits, nfiles


def policy_read_dirs(path=POLICY_PATH):
    import yaml
    with open(path) as f:
        data = yaml.safe_load(f.read()) or {}
    return list(((data.get("allow") or {}).get("read_dirs")) or [])


def _now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sweep_report(roots):
    report = {}
    for root in roots:
        if not os.path.isdir(root):
            report[root] = {"error": "not a directory", "files": 0, "hits": []}
            continue
        hits, nfiles = sweep_root(root)
        report[root] = {"files": nfiles, "hits": [
            {"path": p, "line": ln, "kind": k, "excerpt": ex} for p, ln, k, ex in hits]}
    return report


def _prev_hit_keys(prev_roots):
    keys = set()
    for root, r in (prev_roots or {}).items():
        for h in r.get("hits") or []:
            keys.add((root, h.get("path"), h.get("line"), h.get("kind")))
    return keys


def write_runtime(roots=None, runtime_path=None):
    """Sweep every `allow.read_dirs` root (or `roots`), write the masked report to
    `runtime_path` (wq#1482: lumen-read-dir-sweep.timer's daily payload), and return
    `(report, new_hits)`. `new_hits` are hits whose (root, path, line, kind) was not
    present in the PREVIOUS runtime write, so a rerun of an already-known hit does
    not re-alert every day — only a hit that is new since the last sweep does."""
    runtime_path = runtime_path or RUNTIME_PATH
    roots = list(roots) if roots is not None else policy_read_dirs()
    prev = None
    try:
        with open(runtime_path) as f:
            prev = json.load(f)
    except Exception:
        prev = None
    prev_keys = _prev_hit_keys((prev or {}).get("roots"))
    report = _sweep_report(roots)
    new_hits = []
    for root, r in report.items():
        for h in r.get("hits") or []:
            key = (root, h.get("path"), h.get("line"), h.get("kind"))
            if key not in prev_keys:
                new_hits.append((root, h))
    os.makedirs(os.path.dirname(runtime_path), exist_ok=True)
    with open(runtime_path, "w") as f:
        json.dump({"ts": _now_iso(), "roots": report}, f, indent=1, sort_keys=True)
    return report, new_hits


def aq_alert(new_hits, aq_bin=None):
    """aq add one row per NEW masked hit (source=read-dir-sweep). Fail-open: no aq
    binary or a failed call must never break the sweep itself (instinct: mask before
    print — the excerpt handed to aq is already masked by sweep_root)."""
    aq_bin = aq_bin or AQ_BIN
    if not new_hits or not os.path.exists(aq_bin):
        return
    for root, h in new_hits:
        title = f"read_dir_sweep: fresh hit under {root}"
        body = f"{h.get('path')}:{h.get('line')} [{h.get('kind')}] {h.get('excerpt')}"
        key = f"{root}:{h.get('path')}:{h.get('line')}"
        try:
            subprocess.run([aq_bin, "add", AQ_SOURCE, key, title, body],
                           check=False, timeout=15)
        except Exception:
            pass


def _main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="secret sweep of read-grant roots (masked output)")
    ap.add_argument("roots", nargs="*")
    ap.add_argument("--policy", action="store_true", help="sweep every allow.read_dirs root")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--max-lines", type=int, default=40, help="hits shown per root (text mode)")
    ap.add_argument("--write-runtime", action="store_true",
                    help="sweep every allow.read_dirs root, write runtime/read_dir_sweep.json, "
                         "aq-alert on any hit new since the last write (wq#1482)")
    a = ap.parse_args(argv)
    if a.write_runtime:
        report, new_hits = write_runtime()
        aq_alert(new_hits)
        total = sum(len(r.get("hits") or []) for r in report.values())
        print(f"read_dir_sweep: wrote {RUNTIME_PATH} — {total} hits, {len(new_hits)} new")
        # wq#2115: a FINDING is not a FAILURE. This mode runs as a systemd oneshot, and
        # a hit-driven exit 1 marks lumen-read-dir-sweep.service failed and skips its
        # ExecStartPost cron stamp — i.e. a found secret looks exactly like a broken
        # sweep. The finding channel is the runtime payload + aq; keep the exit code for
        # the sweep genuinely not running. Interactive/CI mode below still exits 1.
        return 0
    roots = list(a.roots)
    if a.policy:
        roots += policy_read_dirs()
    if not roots:
        ap.error("give at least one root or --policy")
    report = _sweep_report(roots)
    total = sum(1 if "error" in r else len(r.get("hits") or []) for r in report.values())
    if a.json:
        print(json.dumps(report, indent=1, sort_keys=True))
    else:
        for root, r in report.items():
            if "error" in r:
                print(f"{root}: {r['error']}")
                continue
            print(f"{root}: files={r['files']} hits={len(r['hits'])}")
            for h in r["hits"][:a.max_lines]:
                print(f"  {h['path']}:{h['line']} [{h['kind']}] {h['excerpt']}")
            if len(r["hits"]) > a.max_lines:
                print(f"  … {len(r['hits']) - a.max_lines} more")
    return 1 if total else 0


if __name__ == "__main__":
    raise SystemExit(_main())
