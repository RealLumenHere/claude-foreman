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
    python3 harness/read_dir_sweep.py --policy --record       # + write runtime/read_dir_sweep.json,
                                                               #   aq-alert new hits (wq#1482 slice 5,
                                                               #   the lumen-read-dir-sweep.timer command)
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
# wq#1253 (#23): mutable runtime state lives under a `runtime/` dir, gitignored — this
# file is regenerated daily by the timer, never a tracked source artifact. Same env var
# capability_lint.py reads (READ_DIR_SWEEP_PATH) — one override knob for one file.
SWEEP_RECORD_PATH = os.environ.get("FOREMAN_READ_DIR_SWEEP",
                                   os.path.join(HARNESS_DIR, "runtime", "read_dir_sweep.json"))
AQ_CLI = os.environ.get("FOREMAN_AQ_CLI", "/home/lumen/agents/alert-queue/aq.py")

# file names that are a credential by convention regardless of content
BASENAME_GLOBS = ("*.env", ".env*", "*.token", "*.key", "*.pem", "id_*", "*.p12", "*.pfx",
                  "*.kdbx", ".netrc", ".git-credentials", "*.secret", "*.secrets")
# content shapes; first match per line wins. `kv_secret` needs a value of 20+ chars so
# `api_key: os.environ/X` (LiteLLM's env-ref form) and `api_key: sk-none` do not fire.
PATTERNS = (
    ("gh_pat", re.compile(r"\b(?:ghp_|github_pat_|gho_|ghs_|glpat-)[A-Za-z0-9_-]{20,}")),
    ("sntl", re.compile(r"\bsntl-p[A-Za-z0-9_-]{8,}")),
    ("sk_key", re.compile(r"\bsk-(?:proj-|ant-|or-v1-|lf-)?[A-Za-z0-9_-]{24,}")),
    ("aws", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("slack", re.compile(r"\b(?:xox[bpsa]|xapp)-[A-Za-z0-9-]{20,}")),
    ("privkey", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY")),
    ("kv_secret", re.compile(
        r"(?i)\b(?:api[_-]?key|secret[_-]?key|client[_-]?secret|password|passwd|"
        r"auth[_-]?token|access[_-]?token|api[_-]?token|bearer)\b\s*[:=]\s*"
        r"['\"]?(?!os\.environ)(?!\$\{?[A-Z_])(?!<)(?!\*{3})[A-Za-z0-9_\-./+]{20,}")),
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
            if any(fnmatch.fnmatch(f, g) for g in BASENAME_GLOBS):
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


def _prev_hit_keys(out_path):
    """{(root, path, line, kind)} from a previously recorded sweep, or set() if the
    record is missing/unreadable — a first run alerts on every hit it finds, which is
    correct: nothing has been recorded as already-known yet."""
    try:
        with open(out_path) as f:
            prev = json.load(f)
    except Exception:
        return set()
    keys = set()
    for root, r in (prev.get("roots") or {}).items():
        for h in r.get("hits") or []:
            keys.add((root, h.get("path"), h.get("line"), h.get("kind")))
    return keys


def _alert_new_hits(new_hits, aq_cli):
    """One aq row for this sweep's batch of new hits (masked excerpts only — instincts
    tier 0 MASK BEFORE PRINT already applied by sweep_root before this is called)."""
    title = f"read_dir_sweep: {len(new_hits)} new hit(s) under allow.read_dirs"
    message = "\n".join(
        f"{root}: {h['path']}:{h['line']} [{h['kind']}] {h['excerpt']}"
        for root, h in new_hits)[:2000]
    cmd = [sys.executable, aq_cli, "add", "read-dir-sweep", "fresh-hit", title, message,
           "--owner", "orchestrator"]
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    except OSError:
        pass


def record_policy_sweep(out_path=None, aq_cli=None):
    """Sweep every allow.read_dirs root, write `out_path` (masked hits per root + ts —
    wq#1441 dir(X) allow evidence must be fresh, wq#1482 slice 5), and fire one aq
    alert for any hit this sweep found that the previous recorded sweep did not have.
    Returns the report dict (same shape `--json` prints, plus `ts`)."""
    out_path = out_path or SWEEP_RECORD_PATH
    aq_cli = aq_cli or AQ_CLI
    prev_keys = _prev_hit_keys(out_path)

    report = {"ts": _now_iso(), "roots": {}}
    new_hits = []
    for root in policy_read_dirs():
        if not os.path.isdir(root):
            report["roots"][root] = {"error": "not a directory", "files": 0, "hits": []}
            continue
        hits, nfiles = sweep_root(root)
        hit_dicts = [{"path": p, "line": ln, "kind": k, "excerpt": ex} for p, ln, k, ex in hits]
        report["roots"][root] = {"files": nfiles, "hits": hit_dicts}
        for h in hit_dicts:
            if (root, h["path"], h["line"], h["kind"]) not in prev_keys:
                new_hits.append((root, h))

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(report, f, indent=1, sort_keys=True)

    if new_hits:
        _alert_new_hits(new_hits, aq_cli)
    return report


def _main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="secret sweep of read-grant roots (masked output)")
    ap.add_argument("roots", nargs="*")
    ap.add_argument("--policy", action="store_true", help="sweep every allow.read_dirs root")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--record", action="store_true",
                    help="write --policy's result to runtime/read_dir_sweep.json and "
                         "aq-alert any hit not in the previous record (the timer's command)")
    ap.add_argument("--max-lines", type=int, default=40, help="hits shown per root (text mode)")
    a = ap.parse_args(argv)
    if a.record:
        if not a.policy:
            ap.error("--record requires --policy")
        report = record_policy_sweep()
        total = sum(len(r.get("hits") or []) for r in report["roots"].values())
        print(f"wrote {SWEEP_RECORD_PATH} ts={report['ts']} hits={total}")
        return 1 if total else 0
    roots = list(a.roots)
    if a.policy:
        roots += policy_read_dirs()
    if not roots:
        ap.error("give at least one root or --policy")
    total = 0
    report = {}
    for root in roots:
        if not os.path.isdir(root):
            report[root] = {"error": "not a directory", "files": 0, "hits": []}
            total += 1
            continue
        hits, nfiles = sweep_root(root)
        total += len(hits)
        report[root] = {"files": nfiles, "hits": [
            {"path": p, "line": ln, "kind": k, "excerpt": ex} for p, ln, k, ex in hits]}
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
