#!/usr/bin/env python3
"""Server-side secret scan for the Anubis Gitea (wq#2286).

Gitea has no secret scanning or push protection, so a committed token was only caught by
the local layers. This is a git pre-receive hook: it reads `old new ref` lines on stdin,
walks the objects that arrive with the push (blobs, commits, tags) and refuses the push if
one carries a token shape. The value is never printed: a finding shows its label, path,
line number and the first 12 hex of the value's sha256, which is what the rotation
tickets already use to compare a candidate.

  scan.py pre-receive            hook mode (stdin: old new ref)
  scan.py audit GITDIR [...]     scan every reachable object in bare repos, report only

Allow a known-synthetic value one of two ways: put `secret-scan:allow` on the same line, or
list the 12-hex sha in /etc/lumen-secret-scan/allow.txt (one per line, # comments).

Fails OPEN on its own errors and on the time budget (logged to syslog): a broken scanner
must not stop the fleet landing PRs. A finding always fails closed.
"""
import hashlib
import os
import re
import subprocess
import sys
import syslog
import time

MAX_BLOB = int(os.environ.get("SECRET_SCAN_MAX_BLOB", str(2 * 1024 * 1024)))
BUDGET_S = int(os.environ.get("SECRET_SCAN_BUDGET_S", "25"))
ALLOW_FILE = os.environ.get("SECRET_SCAN_ALLOW", "/etc/lumen-secret-scan/allow.txt")
INLINE_ALLOW = "secret-scan:allow"
_B = r"(?<![A-Za-z0-9])"

# Token shapes, provider-specific on purpose: a blocking gate wants precision. The github and
# gitlab rows are remote_credential_sweep.TOKEN_PREFIX_RE (wq#2243); the rest follow
# harness/masking.PATTERNS with longer floors.
RULES = [
    ("github-token", re.compile(_B + r"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{20,})")),
    ("gitlab-token", re.compile(_B + r"glpat-[A-Za-z0-9_-]{20,}")),
    ("cloudflare-token", re.compile(_B + r"cfut_[A-Za-z0-9_.-]{30,}")),
    ("anthropic-key", re.compile(_B + r"sk-ant-[A-Za-z0-9_-]{30,}")),
    ("openrouter-key", re.compile(_B + r"sk-or-v1-[A-Za-z0-9]{40,}")),
    ("openai-key", re.compile(_B + r"sk-(?:proj-)?[A-Za-z0-9_-]{40,}")),
    ("slack-token", re.compile(_B + r"xox[bpasr]-[A-Za-z0-9-]{20,}")),
    ("aws-access-key", re.compile(_B + r"AKIA[0-9A-Z]{16}(?![A-Za-z0-9])")),
    ("private-key", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP |ENCRYPTED )?PRIVATE KEY(?: BLOCK)?-----")),
]


def log(msg):
    try:
        syslog.openlog("lumen-secret-scan")
        syslog.syslog(msg)
    except Exception:
        pass


def load_allow():
    out = set()
    try:
        with open(ALLOW_FILE) as fh:
            for line in fh:
                line = line.split("#", 1)[0].strip().lower()
                if re.fullmatch(r"[0-9a-f]{12}", line):
                    out.add(line)
    except OSError:
        pass
    return out


def scan_text(text, allow=frozenset()):
    """[(label, lineno, sha12)] for every unallowed token shape in text."""
    hits, seen = [], set()
    for label, rx in RULES:
        for m in rx.finditer(text):
            sha = hashlib.sha256(m.group(0).encode("utf-8", "replace")).hexdigest()[:12]
            if sha in allow or (label, m.start()) in seen:
                continue
            ls = text.rfind("\n", 0, m.start()) + 1
            le = text.find("\n", m.end())
            if INLINE_ALLOW in text[ls:le if le != -1 else len(text)]:
                continue
            seen.add((label, m.start()))
            hits.append((label, text.count("\n", 0, m.start()) + 1, sha))
    return hits


def git(*args, inp=None):
    return subprocess.run(["git", *args], input=inp, capture_output=True, check=True).stdout


def new_objects(new, base_args):
    """{sha: path} for objects reachable from `new` and not from the given base."""
    out = git("rev-list", "--objects", new, *base_args).decode("utf-8", "replace")
    objs = {}
    for line in out.splitlines():
        sha, _, path = line.partition(" ")
        if sha:
            objs.setdefault(sha, path)
    return objs


def scan_objects(objs, allow, deadline):
    """Scan blobs, commits and tags. Returns (findings, timed_out)."""
    findings = []
    if not objs:
        return findings, False
    shas = list(objs)
    meta = git("cat-file", "--batch-check", inp=("\n".join(shas) + "\n").encode()).decode().splitlines()
    want = []
    for line in meta:
        p = line.split()
        if len(p) == 3 and p[1] in ("blob", "commit", "tag") and int(p[2]) <= MAX_BLOB:
            want.append(p[0])
    for i in range(0, len(want), 200):
        if time.time() > deadline:
            return findings, True
        chunk = want[i:i + 200]
        raw = git("cat-file", "--batch", inp=("\n".join(chunk) + "\n").encode())
        pos = 0
        while pos < len(raw):
            eol = raw.index(b"\n", pos)
            hdr = raw[pos:eol].split()
            size = int(hdr[2])
            body = raw[eol + 1:eol + 1 + size]
            pos = eol + 1 + size + 1
            for label, lineno, sha12 in scan_text(body.decode("latin-1"), allow):
                findings.append((label, objs.get(hdr[0].decode(), "") or "(%s)" % hdr[1].decode(), lineno, sha12))
    return findings, False


def report(findings, out):
    out.write("\nsecret-scan: push REFUSED, %d token-shaped value(s) found:\n" % len(findings))
    for label, path, lineno, sha12 in findings[:20]:
        out.write("  %s  %s:%d  sha256:%s\n" % (label, path, lineno, sha12))
    out.write("Remove the value from every commit in the push (rewrite the branch), and rotate it:\n"
              "it has left the machine. A known-fake value: add `secret-scan:allow` on the same line.\n\n")


def pre_receive(stdin, stderr):
    allow, deadline, findings = load_allow(), time.time() + BUDGET_S, []
    try:
        for line in stdin.read().splitlines():
            parts = line.split()
            if len(parts) != 3 or set(parts[1]) == {"0"}:
                continue                                    # malformed, or a ref delete
            new = parts[1]
            objs = new_objects(new, ["--not", "--all"])
            f, timed_out = scan_objects(objs, allow, deadline)
            findings += f
            if timed_out and not findings:
                log("time budget hit, push allowed unscanned (%s)" % parts[2])
                stderr.write("secret-scan: time budget hit, push not fully scanned\n")
                return 0
    except Exception as exc:                                # fail open on our own bugs
        log("scanner error, push allowed: %r" % (exc,))
        stderr.write("secret-scan: scanner error, push not scanned\n")
        return 0
    if findings:
        log("refused push: %d finding(s)" % len(findings))
        report(findings, stderr)
        return 1
    return 0


def audit(gitdirs, out):
    allow, total = load_allow(), 0
    for gd in gitdirs:
        os.environ["GIT_DIR"] = gd
        objs = new_objects("--all", [])
        f, _ = scan_objects(objs, allow, time.time() + 3600)
        total += len(f)
        out.write("%s: %d objects, %d finding(s)\n" % (gd, len(objs), len(f)))
        for label, path, lineno, sha12 in f[:40]:
            out.write("  %s  %s:%d  sha256:%s\n" % (label, path, lineno, sha12))
    return 1 if total else 0


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "pre-receive":
        sys.exit(pre_receive(sys.stdin, sys.stderr))
    if len(sys.argv) >= 3 and sys.argv[1] == "audit":
        sys.exit(audit(sys.argv[2:], sys.stdout))
    sys.stderr.write(__doc__)
    sys.exit(2)
