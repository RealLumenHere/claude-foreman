"""wq#2815: does a candidate emit STRUCTURED tool calls via ollama /api/chat?
A worker that only prints a tool call as text never runs it; the CLI sees a plain
answer, the session ends in ~2 turns, and fworker classes it FALSE_COMPLETE."""
import json
import sys
import time
import urllib.request

base, model = sys.argv[1], sys.argv[2]
tools = [{"type": "function", "function": {
    "name": "Bash", "description": "Run a shell command and return its output.",
    "parameters": {"type": "object", "properties": {"command": {"type": "string"}},
                   "required": ["command"]}}}]
body = {"model": model, "stream": False, "tools": tools,
        "options": {"num_ctx": 32768, "num_predict": 200},
        "messages": [{"role": "user", "content":
                      "Append the line 'canary ran' to the file research/LOG.md. "
                      "Use the Bash tool to do it."}]}
if "qwen3" in model:
    body["think"] = False
t = time.time()
req = urllib.request.Request(base + "/api/chat", data=json.dumps(body).encode(),
                             headers={"Content-Type": "application/json"})
d = json.load(urllib.request.urlopen(req, timeout=900))
m = d["message"]
print(json.dumps({"model": model, "wall_s": round(time.time() - t, 1),
                  "structured_tool_calls": len(m.get("tool_calls") or []),
                  "tool_calls": m.get("tool_calls"),
                  "content_head": (m.get("content") or "")[:200]}))
