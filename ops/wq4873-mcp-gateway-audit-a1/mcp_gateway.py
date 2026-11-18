#!/usr/bin/env python3
"""
Lumen MCP Gateway — HTTP/SSE bridge for Claude (Android, Desktop, Mobile)
Exposes Lumen's tools over streamable-http on port 3333
Secured with Bearer token auth over Tailscale

wq#4873: hardened — run_shell removed, per-tool auth levels, structured
audit log with argument recording, CORS restricted to tailnet.
"""
import os, sys, json, subprocess, asyncio, logging, hmac, ipaddress
from datetime import datetime, timezone
from pathlib import Path
from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger("mcp-gateway")

# ── Structured audit log ────────────────────────────────────────────────────

AUDIT_LOG_DIR = Path("/home/lumen/agent/logs")
AUDIT_LOG_FILE = AUDIT_LOG_DIR / "mcp_gateway_audit.jsonl"

REDACT_FIELDS = frozenset({
    "password", "secret", "token", "key", "credential", "api_key",
    "auth", "authorization", "cookie", "session",
})

def _redact_args(args: dict) -> dict:
    """Return a copy with sensitive field values replaced by [REDACTED]."""
    out = {}
    for k, v in args.items():
        if any(rf in k.lower() for rf in REDACT_FIELDS):
            out[k] = "[REDACTED]"
        elif isinstance(v, str) and len(v) > 500:
            out[k] = v[:200] + f"...[truncated, {len(v)} chars total]"
        else:
            out[k] = v
    return out


def audit_log(*, tool: str, client_ip: str, level: str, args: dict,
              result_ok: bool, error: str | None = None):
    """Append a structured JSON audit record."""
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "tool": tool,
        "level": level,
        "client_ip": client_ip,
        "args": _redact_args(args),
        "ok": result_ok,
    }
    if error:
        record["error"] = error[:500]
    try:
        AUDIT_LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(AUDIT_LOG_FILE, "a") as f:
            f.write(json.dumps(record, default=str) + "\n")
    except Exception as e:
        log.error(f"audit write failed: {e}")


# ── App setup ───────────────────────────────────────────────────────────────

app = FastAPI(title="Lumen MCP Gateway", version="2.0")

# wq#4873: CORS restricted to tailnet CGNAT range + localhost only.
ALLOWED_ORIGINS = [
    "http://localhost",
    "http://localhost:3333",
    "http://127.0.0.1",
    "http://127.0.0.1:3333",
    "http://100.64.0.0",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_origin_regex=r"^https?://100\.\d{1,3}\.\d{1,3}\.\d{1,3}(:\d+)?$",
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)

AGENT_DIR = Path("/home/lumen/agent")
TASK_QUEUE = AGENT_DIR / "TASK_QUEUE.jsonl"
MEMORY_FILE = AGENT_DIR / "MEMORY.md"
LOG_FILE = AGENT_DIR / "logs" / "agent_service.log"

# wq#2748: no default. The old literal fallback is BURNED.
_BURNED_TOKENS = {"lumen-gateway-2026"}
MCP_TOKEN = os.environ.get("MCP_GATEWAY_TOKEN", "").strip()
if not MCP_TOKEN or MCP_TOKEN in _BURNED_TOKENS or len(MCP_TOKEN) < 32:
    log.critical("MCP_GATEWAY_TOKEN unset, burned, or <32 chars -- refusing to start (wq#2748)")
    sys.exit(78)

# ── Per-tool permission levels ──────────────────────────────────────────────
# read:  safe, no side-effects
# write: mutates state (queue, tasks)
# admin: service-level operations (restart)

TOOL_LEVELS = {
    "get_queue_status": "read",
    "get_agent_logs":   "read",
    "get_memory":       "read",
    "get_agent_status": "read",
    "add_task":         "write",
    "clear_failed_tasks": "write",
    "restart_agent":    "admin",
}

# Admin token is optional — when set, admin-level tools require it instead of
# the regular bearer. When unset, admin tools are DISABLED (fail-closed).
ADMIN_TOKEN = os.environ.get("MCP_GATEWAY_ADMIN_TOKEN", "").strip()

def auth(request: Request):
    hdr = request.headers.get("Authorization", "")
    token = hdr[7:].strip() if hdr.startswith("Bearer ") else ""
    if not token or not hmac.compare_digest(token.encode(), MCP_TOKEN.encode()):
        log.warning(f"auth refused from {request.client.host if request.client else '?'}")
        raise HTTPException(status_code=401, detail="unauthorized")
    return token


def check_tool_access(tool_name: str, bearer_token: str):
    """Enforce per-tool permission level. Returns None on success, raises on deny."""
    level = TOOL_LEVELS.get(tool_name)
    if level is None:
        raise HTTPException(status_code=404, detail=f"unknown tool: {tool_name}")

    if level == "admin":
        if not ADMIN_TOKEN:
            raise HTTPException(
                status_code=403,
                detail=f"tool '{tool_name}' requires admin token (not configured — disabled)",
            )
        if not hmac.compare_digest(bearer_token.encode(), ADMIN_TOKEN.encode()):
            raise HTTPException(
                status_code=403,
                detail=f"tool '{tool_name}' requires admin-level bearer token",
            )

    # read and write levels: regular bearer token (already validated) is sufficient.


# ── Tool definitions exposed to Claude ──────────────────────────────────────

TOOLS = [
    {
        "name": "add_task",
        "description": "Add a new content task to Lumen's queue. Use this to assign Lumen work.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "Blog post topic or task description"},
                "type": {"type": "string", "enum": ["write_post", "seo_audit", "research"], "default": "write_post"},
                "tags": {"type": "array", "items": {"type": "string"}, "default": ["linux", "surface-pro"]},
                "priority": {"type": "integer", "default": 1, "description": "1=normal, 2=high, 3=urgent"}
            },
            "required": ["topic"]
        }
    },
    {
        "name": "get_queue_status",
        "description": "Show Lumen's current task queue — pending, done, failed counts and next tasks.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "get_agent_logs",
        "description": "Get recent Lumen agent logs to see what he's working on or debug issues.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "lines": {"type": "integer", "default": 30, "description": "Number of log lines to return (max 200)"}
            }
        }
    },
    {
        "name": "get_memory",
        "description": "Read Lumen's MEMORY.md — published posts, research notes, operational history.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "section": {"type": "string", "description": "Optional: filter by section keyword"}
            }
        }
    },
    {
        "name": "restart_agent",
        "description": "Gracefully restart the Lumen agent service. Requires admin token.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "get_agent_status",
        "description": "System status — agent running, Ollama, queue summary, last action.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "clear_failed_tasks",
        "description": "Reset all failed tasks back to pending so they get retried.",
        "inputSchema": {"type": "object", "properties": {}}
    },
]

# ── Tool implementations ─────────────────────────────────────────────────────

def tool_add_task(args):
    import uuid
    task = {
        "id": str(uuid.uuid4()),
        "type": args.get("type", "write_post"),
        "topic": args["topic"],
        "tags": args.get("tags", ["linux", "surface-pro"]),
        "priority": args.get("priority", 1),
        "created": datetime.now(timezone.utc).isoformat(),
        "status": "pending"
    }
    with open(TASK_QUEUE, "a") as f:
        f.write(json.dumps(task) + "\n")
    return f"Task added: {task['id'][:8]}... type={task['type']} topic={task['topic']}"


def tool_get_queue_status(args):
    tasks = [json.loads(l) for l in open(TASK_QUEUE)] if TASK_QUEUE.exists() else []
    by_status = {}
    for t in tasks:
        by_status.setdefault(t["status"], []).append(t)

    lines = [f"Queue Status ({len(tasks)} total tasks)"]
    for status, items in sorted(by_status.items()):
        lines.append(f"\n{status.upper()} ({len(items)}):")
        for t in items[:5]:
            lines.append(f"  - [{t['type']}] {t['topic'][:60]}")
        if len(items) > 5:
            lines.append(f"  ... and {len(items)-5} more")
    return "\n".join(lines)


def tool_get_agent_logs(args):
    n = min(args.get("lines", 30), 200)
    if not LOG_FILE.exists():
        return "No log file found"
    lines = LOG_FILE.read_text().splitlines()
    return "\n".join(lines[-n:])


def tool_get_memory(args):
    if not MEMORY_FILE.exists():
        return "MEMORY.md not found"
    content = MEMORY_FILE.read_text()
    section = args.get("section", "").lower()
    if section:
        lines = content.splitlines()
        relevant = [l for l in lines if section in l.lower()]
        return "\n".join(relevant[:50]) if relevant else f"No entries matching '{section}'"
    return content[:3000]


def tool_restart_agent(args):
    result = subprocess.run(
        ["/usr/local/bin/lumen-graceful-restart.sh"],
        capture_output=True, text=True, timeout=10
    )
    return f"Restart triggered.\n{result.stdout}\n{result.stderr}"


def tool_get_agent_status(args):
    checks = {}
    for svc in ["lumen-agent-v2", "ollama"]:
        r = subprocess.run(["systemctl", "is-active", svc], capture_output=True, text=True)
        checks[svc] = r.stdout.strip()

    tasks = [json.loads(l) for l in open(TASK_QUEUE)] if TASK_QUEUE.exists() else []
    by_status = {s: sum(1 for t in tasks if t["status"] == s) for s in set(t["status"] for t in tasks)}

    last_log = ""
    if LOG_FILE.exists():
        lines = LOG_FILE.read_text().splitlines()
        last_log = lines[-1] if lines else ""

    return (
        f"Lumen System Status\n"
        f"  Agent V2: {checks.get('lumen-agent-v2', '?')}\n"
        f"  Ollama: {checks.get('ollama', '?')}\n"
        f"  MCP Gateway: running\n\n"
        f"Queue: {by_status}\n\n"
        f"Last log: {last_log[-120:]}"
    )


def tool_clear_failed_tasks(args):
    tasks = [json.loads(l) for l in open(TASK_QUEUE)] if TASK_QUEUE.exists() else []
    count = 0
    for t in tasks:
        if t["status"] == "failed":
            t["status"] = "pending"
            t["retry_count"] = 0
            count += 1
    with open(TASK_QUEUE, "w") as f:
        for t in tasks:
            f.write(json.dumps(t) + "\n")
    return f"Reset {count} failed tasks back to pending."


TOOL_HANDLERS = {
    "add_task": tool_add_task,
    "get_queue_status": tool_get_queue_status,
    "get_agent_logs": tool_get_agent_logs,
    "get_memory": tool_get_memory,
    "restart_agent": tool_restart_agent,
    "get_agent_status": tool_get_agent_status,
    "clear_failed_tasks": tool_clear_failed_tasks,
}

# ── MCP Protocol Endpoints ───────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "service": "lumen-mcp-gateway", "version": "2.0",
            "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/mcp")
async def mcp_info(_: str = Depends(auth)):
    return {
        "name": "lumen-gateway",
        "version": "2.0.0",
        "description": "Lumen autonomous agent control plane",
        "tools": len(TOOLS)
    }


@app.post("/mcp")
async def mcp_endpoint(request: Request, bearer_token: str = Depends(auth)):
    body = await request.json()
    method = body.get("method", "")
    req_id = body.get("id", 1)

    if method == "initialize":
        return JSONResponse({
            "jsonrpc": "2.0", "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "lumen-gateway", "version": "2.0.0"}
            }
        })

    elif method == "tools/list":
        return JSONResponse({
            "jsonrpc": "2.0", "id": req_id,
            "result": {"tools": TOOLS}
        })

    elif method == "tools/call":
        params = body.get("params", {})
        tool_name = params.get("name", "")
        tool_args = params.get("arguments", {})
        client_ip = request.client.host if request.client else "?"
        level = TOOL_LEVELS.get(tool_name, "?")

        if tool_name not in TOOL_HANDLERS:
            audit_log(tool=tool_name, client_ip=client_ip, level="?",
                      args=tool_args, result_ok=False, error="tool not found")
            return JSONResponse({
                "jsonrpc": "2.0", "id": req_id,
                "error": {"code": -32601, "message": f"Tool not found: {tool_name}"}
            })

        try:
            check_tool_access(tool_name, bearer_token)
        except HTTPException as e:
            audit_log(tool=tool_name, client_ip=client_ip, level=level,
                      args=tool_args, result_ok=False, error=e.detail)
            return JSONResponse({
                "jsonrpc": "2.0", "id": req_id,
                "error": {"code": -32600, "message": e.detail}
            })

        try:
            result = TOOL_HANDLERS[tool_name](tool_args)
            audit_log(tool=tool_name, client_ip=client_ip, level=level,
                      args=tool_args, result_ok=True)
            return JSONResponse({
                "jsonrpc": "2.0", "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": str(result)}],
                    "isError": False
                }
            })
        except Exception as e:
            audit_log(tool=tool_name, client_ip=client_ip, level=level,
                      args=tool_args, result_ok=False, error=str(e))
            log.error(f"Tool {tool_name} error: {e}")
            return JSONResponse({
                "jsonrpc": "2.0", "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": f"Error: {str(e)}"}],
                    "isError": True
                }
            })

    return JSONResponse({
        "jsonrpc": "2.0", "id": req_id,
        "error": {"code": -32601, "message": f"Unknown method: {method}"}
    })


if __name__ == "__main__":
    port = int(os.getenv("MCP_PORT", "3333"))
    log.info(f"Lumen MCP Gateway v2.0 starting on port {port}")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
