#!/usr/bin/env python3
"""kb_paths — path guards for the lumen-kb vault (wq#1151 P1).

Every filesystem path the MCP server or distiller touches goes through here.
The contract: a returned Path is ALWAYS a real child of the vault root, or
PathViolation is raised. Guards cover dot-dot segments, absolute paths, hidden
files (including .git), non-slug names, and symlink escapes (resolved paths
are re-checked against the resolved vault root).

Vault layout (defined here, single source of truth):
  <vault>/<topic>/            one directory per topic (slug)
  <vault>/<topic>/*.md        notes (slug + .md)
  <vault>/<topic>/attachments/*   binary attachments (slug + ext)

Stdlib-only so it is unit-testable anywhere.
"""

from __future__ import annotations

import re
from pathlib import Path

# One path segment: lowercase alnum start, then alnum . _ - (no /, no .., no
# leading dot, so .git and hidden files can never be addressed).
_SLUG = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")


class PathViolation(ValueError):
    """A requested topic/note/attachment name escapes or abuses the vault."""


def _check_segment(name: str, kind: str) -> str:
    if not isinstance(name, str) or not _SLUG.match(name) or ".." in name:
        raise PathViolation(f"invalid {kind} name: {name!r}")
    return name


def _inside(vault: Path, candidate: Path) -> Path:
    """Resolve symlinks and require candidate to stay under the vault root."""
    root = vault.resolve()
    resolved = candidate.resolve()
    if resolved != root and root not in resolved.parents:
        raise PathViolation(f"path escapes vault: {candidate}")
    return candidate


def safe_topic(vault: Path, topic: str) -> Path:
    """Path of a topic dir; guarded, need not exist yet."""
    _check_segment(topic, "topic")
    return _inside(vault, vault / topic)


def safe_note(vault: Path, topic: str, note: str) -> Path:
    """Path of a note file; guarded; enforces the .md suffix."""
    _check_segment(topic, "topic")
    _check_segment(note, "note")
    if not note.endswith(".md"):
        raise PathViolation(f"note must end in .md: {note!r}")
    return _inside(vault, vault / topic / note)


def safe_attachment(vault: Path, topic: str, name: str) -> Path:
    """Path of an attachment; guarded."""
    _check_segment(topic, "topic")
    _check_segment(name, "attachment")
    return _inside(vault, vault / topic / "attachments" / name)


def list_topic_dirs(vault: Path) -> list[str]:
    """Existing topic slugs (skips .git and anything non-slug)."""
    if not vault.is_dir():
        return []
    return sorted(
        p.name
        for p in vault.iterdir()
        if p.is_dir() and _SLUG.match(p.name)
    )
