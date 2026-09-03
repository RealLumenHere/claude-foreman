"""wq#1331 (WORKER_EXEC_CAPABILITY_GAP.md §2.3/§3.2): parse a ticket's capability
needs tags.

Two sources, unioned in order: the ticket's `needs` column (wq#1331 migration,
authored via `wq add/edit --needs`) and an explicit `Needs:` line in the ticket
detail (§2.3 "authored by whoever files the ticket"). The vocabulary is the §2.3
closed set; unknown tags are carried through untouched (capability_lint, slice 4,
is the rejector — this module only reads).

Rollback: FOREMAN_NEEDS_DISABLE=1 makes parse() return an empty Needs, which makes
the router's needs.repo resolution and fworker's add-dir union no-ops in one place;
fworker separately downgrades its spawn assertions to log-only on the same flag.
"""
import os
import re

# §2.3 tag vocabulary (v1, closed set). wq edit --needs validates against this;
# this parser accepts anything tag-shaped so a vocabulary bump can't strand tickets.
TAG_NAMES = ("ssh", "root", "restart", "copy", "repo", "dir", "bench", "power")

_TAG_RE = re.compile(r"(?:needs:)?([a-z_-]+)\(([^)]*)\)")
_NEEDS_LINE_RE = re.compile(r"^Needs:\s*(.+)$", re.MULTILINE)


def disabled():
    return os.environ.get("FOREMAN_NEEDS_DISABLE") == "1"


class Needs:
    def __init__(self, tags):
        # list of (name, arg) in source order, deduped
        self.tags = []
        for t in tags:
            if t not in self.tags:
                self.tags.append(t)

    @property
    def repo(self):
        """First repo(<abs-path>) arg, or None. Single-valued by design: a ticket
        lands in exactly one repo."""
        for name, arg in self.tags:
            if name == "repo" and arg:
                return arg
        return None

    @property
    def dirs(self):
        """Every dir(<abs-path>) arg, in order."""
        return [arg for name, arg in self.tags if name == "dir" and arg]

    @property
    def ssh_nodes(self):
        """Every ssh(<node>) arg, in order (wq#1332 fleet-exec grant resolution)."""
        return [arg for name, arg in self.tags if name == "ssh" and arg]

    @property
    def copy_targets(self):
        """Every copy(<node>:<dir>) arg as (node, dir), malformed args skipped."""
        out = []
        for name, arg in self.tags:
            if name == "copy" and ":" in arg:
                node, _, d = arg.partition(":")
                if node and d:
                    out.append((node, d))
        return out

    @property
    def restart_targets(self):
        """Every restart(<unit>@<node>) arg as (node, unit), malformed args skipped."""
        out = []
        for name, arg in self.tags:
            if name == "restart" and "@" in arg:
                unit, _, node = arg.partition("@")
                if node and unit:
                    out.append((node, unit))
        return out

    def __bool__(self):
        return bool(self.tags)


def parse_tags(text):
    return [(m.group(1), m.group(2).strip()) for m in _TAG_RE.finditer(text or "")]


def parse(needs_field=None, detail=None):
    """Needs from a ticket's `needs` column plus its detail's `Needs:` line.
    Honours FOREMAN_NEEDS_DISABLE=1 (rollback: resolver ignores needs entirely)."""
    if disabled():
        return Needs([])
    tags = parse_tags(needs_field)
    m = _NEEDS_LINE_RE.search(detail or "")
    if m:
        tags += parse_tags(m.group(1))
    return Needs(tags)


def validate(needs_field):
    """(ok, error) for a `needs` column value about to be stored (wq edit --needs).
    Every tag must be tag(arg)-shaped, in TAG_NAMES, with a non-empty arg; repo/dir
    args must be absolute paths."""
    text = (needs_field or "").strip()
    if not text:
        return True, None
    parsed = parse_tags(text)
    # anything left over after removing recognized tag(arg) runs is a syntax error
    leftover = _TAG_RE.sub("", text).replace(",", "").strip()
    if leftover:
        return False, f"unparseable needs fragment: {leftover!r} (want tag(arg), comma-separated)"
    for name, arg in parsed:
        if name not in TAG_NAMES:
            return False, f"unknown needs tag {name!r} (vocabulary: {', '.join(TAG_NAMES)})"
        if not arg:
            return False, f"needs tag {name}() has an empty argument"
        if name in ("repo", "dir") and not arg.startswith("/"):
            return False, f"needs:{name}({arg}) must be an absolute path"
    return True, None
