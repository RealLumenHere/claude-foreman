#!/usr/bin/env python3
"""mailbot-mcp — thin stdio MCP adapter over mailbot's local mail mirror (wq#4882).

Read/search plus draft creation, nothing else. There is no send, delete, forward or
calendar tool: a draft reaches a sender only when Frank runs `mailstore.py approve`
at a terminal (see mailstore.py). This process holds no credential: it reads the
mail.db mirror read-only and writes drafts.db, and never opens a token file.

Mail bodies are third-party text. Launch it through launcher.sh, which puts it behind
lumen_mcp_guard with --untrusted-output and --mask-output.
"""
from __future__ import annotations

try:  # mcp SDK 1.x layout (current pin: mcp>=1.9,<2)
    from mcp.server.fastmcp import FastMCP
except ImportError:  # pragma: no cover - 2.x layout, verify on migration
    from mcp.server import FastMCP  # type: ignore[no-redef,attr-defined]

import mailstore

mcp = FastMCP("mailbot")


@mcp.tool()
def mail_status() -> dict:
    """Message count, newest message (Pacific) and pending task count in the local mirror."""
    return mailstore.mirror_status()


@mcp.tool()
def mail_search(query: str = "", contact: str = "", since_days: int = 30, limit: int = 20) -> list[dict]:
    """Search mailbot's mail, newest first. query matches body or subject, contact matches
    name or address; both are case-insensitive substrings. limit caps at 50."""
    return mailstore.search(query, contact, since_days, limit)


@mcp.tool()
def mail_thread(thread_id: str) -> dict:
    """One thread in order (oldest first), up to the last 50 messages."""
    return mailstore.thread(thread_id)


@mcp.tool()
def mail_contact(who: str) -> dict:
    """Standing note and thread list for a contact (name or address fragment)."""
    return mailstore.contact(who)


@mcp.tool()
def mail_needs_frank(since_days: int = 30) -> list[dict]:
    """Messages flagged as a real handoff (money, scheduling, personal, asked for Frank)."""
    return mailstore.needs_frank(since_days)


@mcp.tool()
def mail_pending_tasks() -> list[dict]:
    """Task-shaped emails that got a templated ack and still need a real answer."""
    return mailstore.pending_tasks()


@mcp.tool()
def mail_draft_create(task_id: str, body: str) -> dict:
    """Draft the real answer to a pending task. Nothing is sent: Frank approves drafts
    from a terminal, then mailbot's cron delivers them threaded onto the original."""
    return mailstore.create_draft(task_id, body)


@mcp.tool()
def mail_drafts() -> list[dict]:
    """Drafts still waiting for Frank's approval."""
    return mailstore.list_drafts()


if __name__ == "__main__":
    mcp.run(transport="stdio")
