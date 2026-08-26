#!/usr/bin/env python3
"""Live smoke (not run in CI): generate a prompt from the REAL fleet state and
assert the measured sections landed. Run manually from prompt-forge/:
    python3 tests/smoke_live.py"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["PROMPT_FORGE_KEY_FILE"] = "/nonexistent/k"
os.environ.pop("LITELLM_API_KEY", None)
tmp = tempfile.mkdtemp(prefix="pf-smoke-")
os.environ["PROMPT_FORGE_STATE_DIR"] = tmp

import server

server.STATE_DIR = Path(tmp)
entry = server._generate({
    "description": "Starting a new project. We are going to reverse engineer "
                   "a Google Glass device.",
    "category": "project",
    "filters": ["hardware", "sensitive", "web_research"],
    "answers": [{"q": "What generation Glass?", "a": "Explorer Edition XE-C"}],
})
p = entry["prompt"]
checks = {
    "roster node": "anubis" in p,
    "roster ip": "100.121.17.5" in p,
    "autonomy live": "FULL AUTONOMY" in p,
    "services inventory": "plex" in p.lower(),
    "answer threaded": "Explorer Edition XE-C" in p,
    "hardware rule": "No backup, no write" in p,
    "separation rule": "OUT of the orchestrator" in p,
}
for name, ok in checks.items():
    print(("PASS" if ok else "FAIL"), name)
print("prompt chars:", len(p))
sys.exit(0 if all(checks.values()) else 1)
