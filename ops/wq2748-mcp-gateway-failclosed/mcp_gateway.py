#!/usr/bin/env python3
"""
Lumen MCP Gateway — HTTP/SSE bridge for Claude (Android, Desktop, Mobile)
Exposes Lumen's tools over streamable-http on port 3333
Secured with Bearer token auth over Tailscale
"""
import os, sys, json, subprocess, asyncio, logging, hmac
from datetime import datetime
from pathlib import Path
from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger("mcp-gateway")

app = FastAPI(title="Lumen MCP Gateway", version="1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

AGENT_DIR = Path("/home/lumen/agent")
TASK_QUEUE = AGENT_DIR / "TASK_QUEUE.jsonl"
MEMORY_FILE = AGENT_DIR / "MEMORY.md"
TOKEN_LOG = AGENT_DIR / "daily_token_log.json"
LOG_FILE = AGENT_DIR / "logs" / "agent_service.log"
SLUGS_FILE = AGENT_DIR / "published_slugs.json"
# wq#2748: no default. The old literal fallback is BURNED (it sat in source for months)
# and is refused outright. Fail closed at startup with EX_CONFIG (78); the unit drop-in
# sets RestartPreventExitStatus=78 so a missing token stops instead of crash-looping.
_BURNED_TOKENS = {"lumen-gateway-2026"}
MCP_TOKEN = os.environ.get("MCP_GATEWAY_TOKEN", "").strip()
if not MCP_TOKEN or MCP_TOKEN in _BURNED_TOKENS or len(MCP_TOKEN) < 32:
    log.critical("MCP_GATEWAY_TOKEN unset, burned, or <32 chars -- refusing to start (wq#2748)")
    sys.exit(78)

def auth(request: Request):
    # wq#2748: auth was dead code (`if False`) and never attached to any route, so
    # POST /mcp (incl. run_shell) was open to the whole tailnet and every local
    # container. Constant-time bearer check, attached to every /mcp route below.
    hdr = request.headers.get("Authorization", "")
    token = hdr[7:].strip() if hdr.startswith("Bearer ") else ""
    if not token or not hmac.compare_digest(token.encode(), MCP_TOKEN.encode()):
        log.warning(f"auth refused from {request.client.host if request.client else '?'}")
        raise HTTPException(status_code=401, detail="unauthorized")
    return True

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
                "lines": {"type": "integer", "default": 30, "description": "Number of log lines to return"}
            }
        }
    },
    {
        "name": "get_token_budget",
        "description": "Check Lumen's daily API token usage and remaining budget.",
        "inputSchema": {"type": "object", "properties": {}}
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
        "description": "Gracefully restart the Lumen agent service. Waits for current task to finish.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "get_agent_status",
        "description": "Full system status — agent running, Ghost CMS, Ollama, queue summary, last action.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "clear_failed_tasks",
        "description": "Reset all failed tasks back to pending so they get retried.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "run_shell",
        "description": "Run a shell command on Lumen. Use carefully — for diagnostics and maintenance only.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to execute"}
            },
            "required": ["command"]
        }
    }
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
        "created": datetime.utcnow().isoformat() + "Z",
        "status": "pending"
    }
    with open(TASK_QUEUE, "a") as f:
        f.write(json.dumps(task) + "\n")
    return f"✅ Task added to queue:\n  ID: {task['id'][:8]}...\n  Type: {task['type']}\n  Topic: {task['topic']}\n  Priority: {task['priority']}"

def tool_get_queue_status(args):
    tasks = [json.loads(l) for l in open(TASK_QUEUE)] if TASK_QUEUE.exists() else []
    by_status = {}
    for t in tasks:
        by_status[t["status"]] = by_status.get(t["status"], [])
        by_status[t["status"]].append(t)
    
    lines = [f"📋 **Queue Status** ({len(tasks)} total tasks)"]
    for status, items in sorted(by_status.items()):
        lines.append(f"\n**{status.upper()}** ({len(items)}):")
        for t in items[:5]:
            lines.append(f"  • [{t['type']}] {t['topic'][:60]}")
        if len(items) > 5:
            lines.append(f"  ... and {len(items)-5} more")
    return "\n".join(lines)

def tool_get_agent_logs(args):
    n = args.get("lines", 30)
    if not LOG_FILE.exists():
        return "No log file found"
    lines = LOG_FILE.read_text().splitlines()
    return "\n".join(lines[-n:])

def tool_get_token_budget(args):
    if not TOKEN_LOG.exists():
        return "Token log not found"
    data = json.loads(TOKEN_LOG.read_text())
    groq_pct = (data.get("groq_used", 0) / 100000) * 100
    return (
        f"📊 **Token Budget** ({data.get('date', 'unknown')})\n"
        f"  Groq 70B: {data.get('groq_used', 0):,} / 100,000 ({groq_pct:.0f}%)\n"
        f"  Gemini calls: {data.get('gemini_calls', 0)}\n"
        f"  Ollama calls: {data.get('ollama_calls', 0)}\n"
        f"  DeepSeek calls: {data.get('deepseek_calls', 0)}"
    )

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
    
    r = subprocess.run(["ss", "-tlnp"], capture_output=True, text=True)
    checks["ghost_2368"] = "up" if ":2368" in r.stdout else "down"
    # librechat probe removed 2026-04-21 — LibreChat deprecated
    
    tasks = [json.loads(l) for l in open(TASK_QUEUE)] if TASK_QUEUE.exists() else []
    by_status = {s: sum(1 for t in tasks if t["status"] == s) for s in set(t["status"] for t in tasks)}
    
    last_log = ""
    if LOG_FILE.exists():
        lines = LOG_FILE.read_text().splitlines()
        last_log = lines[-1] if lines else ""
    
    return (
        f"🖥️ **Lumen System Status**\n"
        f"  Agent V2: {checks.get('lumen-agent-v2', '?')}\n"
        f"  Ollama: {checks.get('ollama', '?')}\n"
        f"  Ghost CMS: {checks.get('ghost_2368', '?')}\n"
        f"  MCP Gateway: running ✅\n\n"
        f"📋 Queue: {by_status}\n\n"
        f"📝 Last log: {last_log[-120:]}"
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
    return f"✅ Reset {count} failed tasks back to pending."

def tool_run_shell(args):
    cmd = args.get("command", "")
    if not cmd:
        return "No command provided"
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
        output = r.stdout + r.stderr
        return output[:2000] if output else "(no output)"
    except subprocess.TimeoutExpired:
        return "Command timed out after 30s"

TOOL_HANDLERS = {
    "add_task": tool_add_task,
    "get_queue_status": tool_get_queue_status,
    "get_agent_logs": tool_get_agent_logs,
    "get_token_budget": tool_get_token_budget,
    "get_memory": tool_get_memory,
    "restart_agent": tool_restart_agent,
    "get_agent_status": tool_get_agent_status,
    "clear_failed_tasks": tool_clear_failed_tasks,
    "run_shell": tool_run_shell,
}

# ── MCP Protocol Endpoints ───────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "service": "lumen-mcp-gateway", "timestamp": datetime.utcnow().isoformat()}

@app.get("/mcp")
async def mcp_info(_: bool = Depends(auth)):
    return {
        "name": "lumen-gateway",
        "version": "1.0.0",
        "description": "Lumen autonomous agent control plane",
        "tools": len(TOOLS)
    }

@app.post("/mcp")
async def mcp_endpoint(request: Request, _: bool = Depends(auth)):
    body = await request.json()
    method = body.get("method", "")
    req_id = body.get("id", 1)

    if method == "initialize":
        return JSONResponse({
            "jsonrpc": "2.0", "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "lumen-gateway", "version": "1.0.0"}
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
        
        if tool_name not in TOOL_HANDLERS:
            return JSONResponse({
                "jsonrpc": "2.0", "id": req_id,
                "error": {"code": -32601, "message": f"Tool not found: {tool_name}"}
            })
        
        try:
            result = TOOL_HANDLERS[tool_name](tool_args)
            log.info(f"Tool called: {tool_name} by {request.client.host}")
            return JSONResponse({
                "jsonrpc": "2.0", "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": str(result)}],
                    "isError": False
                }
            })
        except Exception as e:
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
    log.info(f"Lumen MCP Gateway starting on port {port}")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
