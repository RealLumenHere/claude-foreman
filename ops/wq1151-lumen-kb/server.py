#!/usr/bin/env python3
"""lumen-kb-mcp — HTTP MCP server over the markdown vault at /home/lumen/kb (wq#1151).

Canonical copy lives in foreman ops/wq1151-lumen-kb/; apply.sh deploys it to
/home/lumen/projects/lumen-kb-mcp/server.py. Streamable-http on 127.0.0.1:8781
(loopback only — exposure happens exclusively via mcp-oauth-worker, see
P2_EXPOSURE_REVIEW.md).

Tools (surface fixed by wq#1151): list_topics, search, read_note, write_note,
list_attachments. Every write is git-committed so the vault history is the
audit log. All paths go through kb_paths guards (P1 traversal hardening).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

try:  # mcp SDK 1.x layout (current pin: mcp>=1.9,<2)
    from mcp.server.fastmcp import FastMCP
except ImportError:  # pragma: no cover - 2.x layout, verify on migration
    from mcp.server import FastMCP  # type: ignore[no-redef,attr-defined]

from kb_paths import list_topic_dirs, safe_note, safe_topic

VAULT = Path(os.environ.get("LUMEN_KB_VAULT", "/home/lumen/kb"))
HOST = os.environ.get("LUMEN_KB_HOST", "127.0.0.1")
PORT = int(os.environ.get("LUMEN_KB_PORT", "8781"))
MAX_RESULTS = 40
MAX_NOTE_BYTES = 512 * 1024

mcp = FastMCP("lumen-kb", host=HOST, port=PORT)


def _git(*args: str) -> None:
    subprocess.run(["git", "-C", str(VAULT), *args], check=True,
                   capture_output=True, text=True, timeout=30)


@mcp.tool()
def list_topics() -> list[dict]:
    """List KB topics with their note counts. Call this first to see what the KB covers."""
    out = []
    for slug in list_topic_dirs(VAULT):
        notes = sorted(p.name for p in (VAULT / slug).glob("*.md"))
        out.append({"topic": slug, "notes": notes})
    return out


@mcp.tool()
def search(query: str, topic: str = "") -> list[dict]:
    """Full-text search across the KB (optionally scoped to one topic).

    Returns {topic, note, line, text} hits. Search BEFORE answering questions
    the KB might cover.
    """
    scope = safe_topic(VAULT, topic) if topic else VAULT
    hits: list[dict] = []
    rg = shutil.which("rg")
    if rg:
        proc = subprocess.run(
            [rg, "-n", "-i", "--no-heading", "-g", "*.md", "-m",
             str(MAX_RESULTS), "--", query, str(scope)],
            capture_output=True, text=True, timeout=20,
        )
        lines = proc.stdout.splitlines()
    else:  # fallback: plain scan
        lines = []
        needle = query.lower()
        for md in scope.rglob("*.md"):
            try:
                for i, line in enumerate(md.read_text(errors="replace").splitlines(), 1):
                    if needle in line.lower():
                        lines.append(f"{md}:{i}:{line}")
            except OSError:
                continue
    for raw in lines[:MAX_RESULTS]:
        path_s, _, rest = raw.partition(":")
        lineno, _, text = rest.partition(":")
        try:
            rel = Path(path_s).resolve().relative_to(VAULT.resolve())
        except ValueError:
            continue
        hits.append({
            "topic": rel.parts[0] if rel.parts else "",
            "note": rel.name,
            "line": int(lineno) if lineno.isdigit() else 0,
            "text": text.strip()[:300],
        })
    return hits


@mcp.tool()
def read_note(topic: str, note: str) -> str:
    """Read one note (markdown) from a topic. note must be the filename, e.g. 'setup.md'."""
    path = safe_note(VAULT, topic, note)
    if not path.is_file():
        raise FileNotFoundError(f"no such note: {topic}/{note}")
    return path.read_text(errors="replace")[:MAX_NOTE_BYTES]


@mcp.tool()
def write_note(topic: str, note: str, content: str, source: str = "mcp") -> str:
    """Create or overwrite a note; the write is git-committed (vault history = audit log)."""
    if len(content.encode()) > MAX_NOTE_BYTES:
        raise ValueError(f"note exceeds {MAX_NOTE_BYTES} bytes")
    path = safe_note(VAULT, topic, note)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    rel = f"{topic}/{note}"
    _git("add", rel)
    _git("commit", "--allow-empty-message", "-m", f"kb: write {rel} (via {source})")
    return f"wrote {rel}"


@mcp.tool()
def list_attachments(topic: str) -> list[str]:
    """List attachment filenames for a topic (images etc.; content not served over MCP)."""
    adir = safe_topic(VAULT, topic) / "attachments"
    if not adir.is_dir():
        return []
    return sorted(p.name for p in adir.iterdir() if p.is_file())


def main() -> None:
    if not VAULT.is_dir():
        raise SystemExit(f"vault missing: {VAULT}")
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
