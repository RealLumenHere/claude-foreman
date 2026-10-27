"""Repo-local path-scoped hash allowlist for the secret scanners (wq#4735).

A repo opts a known-fake fixture token out of the scan with a `.secret-scan-allow`
file at its root. One entry per line:

    sha256:<64 hex of the exact matched value>  <fixture glob>

The scanners still scan every blob; on a token match they skip the finding ONLY if
(a) sha256(matched value) equals the listed hash, (b) the file path matches the
entry's glob, and (c) the glob itself targets test/fixture paths — it must contain
`test`, `fixture`, `example`, `sample` or `mock`, so a production path can never be
allowlisted. Malformed lines grant nothing. The file carries only hashes, so it
never trips the scanner itself.

Shared by scan.py (server pre-receive, reads the file from the pushed commit) and
cmd-secret-lint (client, reads it from the working tree).
"""
import hashlib
import re

FILE_NAME = ".secret-scan-allow"
MAX_SIZE = 64 * 1024
_ENTRY = re.compile(r"^sha256:([0-9a-f]{64})\s+(\S+)$")
_FIXTURE_HINT = re.compile(r"(test|fixture|example|sample|mock)", re.I)


def parse(text):
    """[(sha256hex, glob)] from allowlist text; malformed or non-fixture lines grant nothing."""
    entries = []
    if not isinstance(text, str) or len(text) > MAX_SIZE:
        return entries
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        m = _ENTRY.match(line)
        if m and _FIXTURE_HINT.search(m.group(2)):
            entries.append((m.group(1), m.group(2)))
    return entries


def _glob_rx(glob):
    """Translate a path glob: ** crosses `/`, * and ? stay within one segment."""
    out, i = [], 0
    while i < len(glob):
        if glob[i:i + 3] == "**/":
            out.append("(?:.*/)?")
            i += 3
        elif glob[i:i + 2] == "**":
            out.append(".*")
            i += 2
        elif glob[i] == "*":
            out.append("[^/]*")
            i += 1
        elif glob[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(glob[i]))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def path_matches(glob, path):
    if not path:
        return False
    path = path.replace("\\", "/").lstrip("/")
    if path.startswith("./"):
        path = path[2:]
    try:
        return bool(_glob_rx(glob).match(path))
    except re.error:
        return False


def allowed(path, value, entries):
    """True iff sha256(value) is listed for a fixture glob matching path. Fail closed."""
    if not entries or not path or not value:
        return False
    sha = hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()
    for h, glob in entries:
        if h == sha and path_matches(glob, path):
            return True
    return False
