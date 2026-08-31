#!/usr/bin/env python3
"""kb_distill — session transcript -> KB note via Minerva/LiteLLM (wq#1151 P3).

Manual trigger first (fleet session-end wiring comes after this proves out):

  kb_distill.py --topic google-nest --transcript /path/to/session.md \\
      [--title thermostat-pairing] [--image /path/img.jpg ...] [--dry-run]

Pipeline: read transcript (plain text/markdown or Claude .jsonl — text parts
are extracted) -> one LiteLLM summarisation call -> optional per-image vision
caption calls -> write the note straight into the vault and git-commit it.
Direct vault write (same kb_paths guards as the server) instead of a loopback
MCP round-trip: the distiller runs on the same host as the vault by design.

Key resolution mirrors prompt-forge/llm.py (docs/litellm/service_keys.md):
LITELLM_API_KEY env first, then the lumen-kb service-key file. No key -> the
run fails loudly; a distiller must never silently drop a session.
"""

from __future__ import annotations

import argparse
import base64
import datetime
import json
import mimetypes
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

from kb_paths import safe_note

GATEWAY = os.environ.get("LUMEN_KB_LITELLM_URL", "http://127.0.0.1:4000")
MODEL = os.environ.get("LUMEN_KB_DISTILL_MODEL", "claude-sonnet-5")
VISION_MODEL = os.environ.get("LUMEN_KB_VISION_MODEL", MODEL)
KEY_FILE = Path(os.environ.get(
    "LUMEN_KB_KEY_FILE", "/home/lumen/.secrets/litellm-service-keys/lumen-kb.key"))
VAULT = Path(os.environ.get("LUMEN_KB_VAULT", "/home/lumen/kb"))
TIMEOUT_S = 120
MAX_TRANSCRIPT_CHARS = 200_000

SUMMARY_SYSTEM = (
    "You distill a work/chat session into a knowledge-base note for the topic "
    "'{topic}'. Keep only durable facts, decisions, procedures, and gotchas — "
    "no play-by-play, no pleasantries. Output plain markdown: a one-line "
    "summary, then '## Facts', '## Procedures', '## Open questions' sections "
    "(omit empty ones). Be terse; this note is re-read by future sessions."
)


def _api_key() -> str:
    key = os.environ.get("LITELLM_API_KEY", "").strip()
    if not key:
        try:
            key = KEY_FILE.read_text().strip()
        except OSError:
            key = ""
    if not key:
        raise SystemExit(f"no LiteLLM key (env LITELLM_API_KEY or {KEY_FILE})")
    return key


def _chat(messages: list[dict], model: str, max_tokens: int = 2000) -> str:
    body = json.dumps({"model": model, "max_tokens": max_tokens,
                       "messages": messages}).encode()
    req = urllib.request.Request(
        f"{GATEWAY}/v1/chat/completions", data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {_api_key()}"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        data = json.loads(resp.read())
    return data["choices"][0]["message"]["content"]


def load_transcript(path: Path) -> str:
    """Plain text passes through; .jsonl gets text parts extracted per line."""
    raw = path.read_text(errors="replace")
    if path.suffix != ".jsonl":
        return raw[:MAX_TRANSCRIPT_CHARS]
    parts: list[str] = []
    for line in raw.splitlines():
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        msg = obj.get("message") or obj
        content = msg.get("content")
        if isinstance(content, str):
            parts.append(f"{msg.get('role', '?')}: {content}")
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(f"{msg.get('role', '?')}: {block.get('text', '')}")
    return "\n".join(parts)[:MAX_TRANSCRIPT_CHARS]


def caption_image(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    b64 = base64.b64encode(path.read_bytes()).decode()
    content = [
        {"type": "text", "text":
         "Caption this image for a knowledge-base note: what it shows and any "
         "readable text/model numbers. 1-3 sentences."},
        {"type": "image_url",
         "image_url": {"url": f"data:{mime};base64,{b64}"}},
    ]
    return _chat([{"role": "user", "content": content}], VISION_MODEL, 400).strip()


def distill(topic: str, transcript: Path, title: str,
            images: list[Path], dry_run: bool) -> str:
    text = load_transcript(transcript)
    if not text.strip():
        raise SystemExit(f"empty transcript: {transcript}")
    summary = _chat(
        [{"role": "system", "content": SUMMARY_SYSTEM.format(topic=topic)},
         {"role": "user", "content": text}], MODEL)
    stamp = datetime.date.today().isoformat()
    note_name = f"{stamp}-{title}.md"
    lines = [f"<!-- distilled {stamp} from {transcript.name} -->", "", summary.strip()]
    if images:
        lines += ["", "## Images"]
        for img in images:
            lines.append(f"- `{img.name}`: {caption_image(img)}")
    content = "\n".join(lines) + "\n"

    if dry_run:
        return content
    dest = safe_note(VAULT, topic, note_name)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(content)
    rel = f"{topic}/{note_name}"
    for cmd in (["add", rel], ["commit", "-m", f"kb: distill {rel}"]):
        subprocess.run(["git", "-C", str(VAULT), *cmd], check=True,
                       capture_output=True, text=True, timeout=30)
    return rel


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--topic", required=True)
    ap.add_argument("--transcript", required=True, type=Path)
    ap.add_argument("--title", default="session")
    ap.add_argument("--image", action="append", type=Path, default=[])
    ap.add_argument("--dry-run", action="store_true",
                    help="print the note to stdout instead of writing")
    args = ap.parse_args()
    result = distill(args.topic, args.transcript, args.title,
                     args.image, args.dry_run)
    print(result if args.dry_run else f"wrote {result}")


if __name__ == "__main__":
    sys.exit(main())
