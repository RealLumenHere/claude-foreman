#!/usr/bin/env python3
"""server.py — Prompt Forge HTTP server (wq#957).

Fleet-aware prompt generator for home.lumennode.uk / prompts.lumennode.uk.
Stdlib-only http.server, same deploy posture as dispatcher/dashboard.py
(wq#718): binds loopback, exposure and auth are the reverse-proxy's job
(cloudflared + Cloudflare Access — see INSTALL.md).

Routes:
    GET  /               UI (static/index.html)
    GET  /api/health     {ok, llm}
    GET  /api/context    deterministic fleet snapshot (debug / UI preview)
    GET  /api/meta       categories, filters, known projects
    POST /api/clarify    description → 1-3 clarifying questions (LLM; [] if down)
    POST /api/generate   full inputs → assembled prompt
    GET  /api/history    recent generations (newest first)
    GET  /api/predict-next  predicted next research turn's category/filters
                             (wq#2521, from recency-weighted history)

Mutating surface is prompt generation only: nothing here claims tickets,
writes fleet state, or runs commands. The only writes are history JSON files
under STATE_DIR.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import fleet_context
import llm
import next_turn_intent
import templates

STATIC_DIR = Path(__file__).resolve().parent / "static"
STATE_DIR = Path(
    os.environ.get("PROMPT_FORGE_STATE_DIR", "/home/lumen/agents/prompt-forge")
)
HISTORY_KEEP = 50
MAX_BODY = 256 * 1024

CLARIFY_SYSTEM = """\
You generate clarifying questions for a fleet prompt generator. Frank describes
a project in one line; a complete session prompt will be built from templates
plus live fleet state. Your ONLY job: decide what 1-3 questions (at most) would
significantly change that prompt, in the spirit of:
- Recognize the domain (e.g. hardware RE -> ask generation/SoC/bootloader, not color).
- If it names an existing project from the fleet summary, prefer continuation
  over re-asking what the project is.
- Only ask what you genuinely need. If a reasonable default exists, assume it
  and record the assumption instead of asking. Zero questions is a fine answer.
Reply with ONLY a JSON object:
{"questions": ["..."], "assumptions": ["..."], "project_slug": "existing-slug-or-empty"}
"""

FRAMING_SYSTEM = """\
You draft the project-specific briefing section for a fleet session prompt.
Everything about the fleet (nodes, autonomy, rules) is templated elsewhere —
do NOT restate fleet facts, node names, or IPs. Write only project substance:
2-4 short paragraphs or bullets covering the likely technical approach, what
to read or examine first (fold in Frank's resources and answers), and the
known risks/unknowns for this specific domain. Plain text, no headings, no
preamble, under 300 words. If you are unsure of a fact, phrase it as something
to verify, not as fact.
"""


def _clarify(payload: dict) -> dict:
    desc = payload.get("description", "").strip()
    user = f"Project description: {desc}\n" \
           f"Category: {payload.get('category', 'project')}\n" \
           f"Resources pasted: {bool(payload.get('resources', '').strip())}\n" \
           f"Filters: {', '.join(payload.get('filters', [])) or 'none'}\n\n" \
           f"Fleet summary:\n{fleet_context.compact_summary()}"
    reply = llm.chat(CLARIFY_SYSTEM, user, max_tokens=600)
    obj = llm.extract_json(reply) or {}
    questions = [q for q in obj.get("questions", []) if isinstance(q, str)][:3]
    assumptions = [a for a in obj.get("assumptions", []) if isinstance(a, str)][:5]
    slug = obj.get("project_slug", "")
    return {
        "questions": questions,
        "assumptions": assumptions,
        "project_slug": slug if isinstance(slug, str) else "",
        "llm": reply is not None,
    }


def _generate(payload: dict) -> dict:
    desc = payload.get("description", "").strip()
    answers = payload.get("answers", [])
    resources = payload.get("resources", "")
    framing = None
    if llm.available():
        qa = "\n".join(
            f"Q: {a.get('q', '')}\nA: {a.get('a', '')}" for a in answers
        )
        framing = llm.chat(
            FRAMING_SYSTEM,
            f"Project: {desc}\nCategory: {payload.get('category', '')}\n"
            f"Resources:\n{resources[:4000]}\nClarifications:\n{qa[:2000]}",
            max_tokens=800,
        )
    prompt = templates.assemble(
        description=desc,
        category=payload.get("category", "project"),
        resources=resources,
        filters=payload.get("filters", []),
        answers=answers,
        framing=framing,
        project_slug=payload.get("project_slug", ""),
        assumptions=payload.get("assumptions", []),
    )
    entry = {
        "id": uuid.uuid4().hex[:12],
        "ts": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        "description": desc,
        "category": payload.get("category", "project"),
        "filters": payload.get("filters", []),
        "prompt": prompt,
        "llm_framing": framing is not None,
    }
    _save_history(entry)
    return entry


def _save_history(entry: dict) -> None:
    """Design open question 3: store generations for re-use — newest 50 kept."""
    hist = STATE_DIR / "history"
    try:
        hist.mkdir(parents=True, exist_ok=True)
        path = hist / f"{time.strftime('%Y%m%d_%H%M%S')}_{entry['id']}.json"
        path.write_text(json.dumps(entry, indent=1))
        old = sorted(hist.glob("*.json"))[:-HISTORY_KEEP]
        for p in old:
            p.unlink(missing_ok=True)
    except OSError:
        pass  # history is a convenience, never a failure


def _history() -> list[dict]:
    out = []
    try:
        for p in sorted((STATE_DIR / "history").glob("*.json"), reverse=True)[:20]:
            e = json.loads(p.read_text())
            out.append({k: e.get(k) for k in
                        ("id", "ts", "description", "category", "prompt")})
    except (OSError, ValueError):
        pass
    return out


class Handler(BaseHTTPRequestHandler):
    server_version = "PromptForge/1.0"

    def log_message(self, fmt, *args):  # quiet; journald gets errors only
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj).encode(), "application/json")

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            try:
                page = (STATIC_DIR / "index.html").read_bytes()
            except OSError:
                return self._json({"error": "ui missing"}, 500)
            return self._send(200, page, "text/html; charset=utf-8")
        if path == "/api/health":
            return self._json({"ok": True, "llm": llm.available()})
        if path == "/api/context":
            return self._json(fleet_context.snapshot())
        if path == "/api/meta":
            return self._json({
                "categories": list(templates.CATEGORIES),
                "filters": templates.FILTERS,
                "projects": fleet_context.known_projects(),
            })
        if path == "/api/history":
            return self._json(_history())
        if path == "/api/predict-next":
            history = next_turn_intent.load_history(STATE_DIR)
            return self._json(next_turn_intent.predict_next_intent(history))
        return self._json({"error": "not found"}, 404)

    def do_POST(self):
        path = self.path.split("?")[0]
        try:
            n = min(int(self.headers.get("Content-Length", 0)), MAX_BODY)
            payload = json.loads(self.rfile.read(n) or b"{}")
            assert isinstance(payload, dict)
        except (ValueError, AssertionError):
            return self._json({"error": "bad json"}, 400)
        if not payload.get("description", "").strip():
            return self._json({"error": "description required"}, 400)
        if path == "/api/clarify":
            return self._json(_clarify(payload))
        if path == "/api/generate":
            return self._json(_generate(payload))
        return self._json({"error": "not found"}, 404)


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="Prompt Forge server (wq#957)")
    ap.add_argument("--bind", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8792)
    args = ap.parse_args()
    srv = ThreadingHTTPServer((args.bind, args.port), Handler)
    print(f"prompt-forge listening on {args.bind}:{args.port}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
