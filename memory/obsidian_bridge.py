#!/usr/bin/env python3
"""obsidian_bridge.py — wq#2442: Obsidian vault as cross-session fleet memory.

Frank tested Claude Projects for weeks: it's a folder, not memory — nothing
carries context between chats unless an agent explicitly writes it
somewhere durable and every future session explicitly reads it back. He
paid for Obsidian instead. An Obsidian vault is just a directory of
markdown files: no server, no plugin, no running app required to read or
write it, which is exactly what a headless fleet needs. Obsidian's own
scripting surface (the community "Local REST API" plugin) only answers
while the desktop app is open at a screen — no use to a worker running
unattended on Lumen — so this module writes vault files directly instead.

One note per ticket under `<vault>/Fleet Memory/`, generated from the same
digest `episodic.commit_to_archive()` already computes (memory/README.md),
so this is a second write of data the fleet already produces at attempt
end, not a new source of truth to keep in sync by hand. A running
`_Log.md` gives Frank (or an agent, in the Obsidian app or over the raw
files) a chronological index to skim; `search_notes()` is the recall side
an agent calls to pull prior sessions into a prompt — the piece Projects
never had, since nothing there read the folder back in.

See memory/OBSIDIAN.md for the vault layout, the sync design (a synced
folder, not Obsidian's proprietary Sync protocol — this module never
needs Frank's Obsidian account), and what still needs Frank's hands.

Stdlib-only (repo convention). Fail-open like the rest of memory/: the
call site (harness/fworker.py commit_episode_to_archive) wraps every call
here in try/except, so a missing or unwritable vault degrades this to
"no Obsidian note" and can never fail an attempt.
"""
import glob
import os
import re
import time

DEFAULT_VAULT_ROOT = os.environ.get(
    "OBSIDIAN_VAULT_ROOT", "/home/lumen/obsidian-vault")

NOTES_SUBDIR = "Fleet Memory"
LOG_FILENAME = "_Log.md"

_FRONTMATTER_UNSAFE_RE = re.compile(r'["\n]')


def _yaml_str(value):
    """Quote a scalar for YAML frontmatter. Strips the two characters
    (quote, newline) that would break a double-quoted scalar rather than
    escaping them — frontmatter values here are short summaries, not data
    that needs round-tripping exactly."""
    return '"' + _FRONTMATTER_UNSAFE_RE.sub(" ", str(value)) + '"'


def _yaml_list(items):
    return "[" + ", ".join(_yaml_str(i) for i in items) + "]"


def note_path(ticket_id, vault_root=None):
    root = vault_root or DEFAULT_VAULT_ROOT
    return os.path.join(root, NOTES_SUBDIR, f"wq{ticket_id}.md")


def log_path(vault_root=None):
    root = vault_root or DEFAULT_VAULT_ROOT
    return os.path.join(root, NOTES_SUBDIR, LOG_FILENAME)


def render_note(ticket_id, digest):
    """Render one archival digest (the dict shape episodic.digest_events()
    returns) as an Obsidian note: YAML frontmatter Obsidian indexes as
    searchable properties, then the same summary/changed/failed/why
    fields the archival store already carries."""
    lines = [
        "---",
        f"ticket: {ticket_id}",
        f"kind: {_yaml_str(digest.get('kind') or 'session')}",
        f"tags: {_yaml_list(digest.get('tags') or [])}",
        f"date: {_yaml_str(time.strftime('%Y-%m-%d %H:%M:%S'))}",
        f"ref: {_yaml_str(digest.get('ref') or '')}",
        "---",
        "",
        f"# wq#{ticket_id}",
        "",
        digest.get("summary") or "",
        "",
    ]
    if digest.get("changed"):
        lines.append("## Changed")
        lines.extend(f"- {c}" for c in digest["changed"])
        lines.append("")
    if digest.get("failed"):
        lines.append("## Failed attempts")
        lines.extend(f"- {f}" for f in digest["failed"])
        lines.append("")
    if digest.get("why"):
        lines.append("## Why")
        lines.append(digest["why"])
        lines.append("")
    return "\n".join(lines)


def write_note(ticket_id, digest, vault_root=None):
    """Write (overwrite) this ticket's note and append one line to the
    vault's running log. Returns the note path.

    Raises on I/O failure rather than swallowing it — same posture as
    episodic.append_event — because the fail-open contract lives at the
    call site (harness/fworker.py), which already wraps this in
    try/except so a dead vault mount degrades memory, never an attempt.
    """
    path = note_path(ticket_id, vault_root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(render_note(ticket_id, digest))

    summary = (digest.get("summary") or f"wq#{ticket_id}").strip()
    with open(log_path(vault_root), "a") as f:
        f.write(f"- {time.strftime('%Y-%m-%d %H:%M')} [[wq{ticket_id}]] {summary}\n")
    return path


def read_note(ticket_id, vault_root=None):
    """Full text of one ticket's note, or None if it doesn't exist yet."""
    path = note_path(ticket_id, vault_root)
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return None


def search_notes(query, vault_root=None, limit=20):
    """Grep every note in the vault for `query` (case-insensitive
    substring) — the recall half of cross-session memory: an agent calls
    this to pull prior sessions into its own prompt, the step Claude
    Projects never had (wq#2442 — Projects is a folder nothing reads
    back). Returns (path, line_no, line) tuples, newest notes first."""
    root = vault_root or DEFAULT_VAULT_ROOT
    files = sorted(glob.glob(os.path.join(root, NOTES_SUBDIR, "*.md")),
                    key=os.path.getmtime, reverse=True)
    needle = query.lower()
    hits = []
    for path in files:
        try:
            with open(path) as f:
                for lineno, line in enumerate(f, 1):
                    if needle in line.lower():
                        hits.append((path, lineno, line.rstrip("\n")))
                        if len(hits) >= limit:
                            return hits
        except OSError:
            continue
    return hits
