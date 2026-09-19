#!/usr/bin/env python3
"""wq#894 — does an image block burn the subscription limit like a text block?

Two arms via `claude -p`, one text payload and one image payload sized to the
same input-token count. Session/weekly percent is read from foreman state.json
(governor poll of /api/oauth/usage) before and after each arm; in-band token
counts come from the stream-json result frame.

  vision_limit_probe.py plan  [--tokens N]            # build payloads, print sizes, no spend
  vision_limit_probe.py run   [--tokens N] [--out F]  # spend: one text arm, one image arm

Run `run` on a lane whose allowlist covers `claude -p`, with the fleet quiet:
other workers share the subscription and move the same percent counters.
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import math
import subprocess
import sys
import time

CLAUDE_BIN = "/home/lumen/.npm-global/bin/claude"
STATE_PATH = "/home/lumen/foreman/state.json"
PATCH = 28
SETTLE_S = 100  # governor polls every 60-90s; wait out one full cycle after each arm


def image_tokens(w: int, h: int) -> int:
    return math.ceil(w / PATCH) * math.ceil(h / PATCH)


def make_image(target_tokens: int) -> tuple[bytes, int]:
    """Noisy PNG whose 28px-patch count is ~target_tokens (no downscale: long edge <= 1568)."""
    from PIL import Image
    import random

    cols = min(56, target_tokens)
    rows = max(1, round(target_tokens / cols))
    w, h = cols * PATCH, rows * PATCH
    rng = random.Random(894)
    img = Image.frombytes("RGB", (w, h), bytes(rng.randrange(256) for _ in range(w * h * 3)))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue(), image_tokens(w, h)


def make_text(target_tokens: int) -> str:
    """Random-word filler, ~1 token per short word, so no cache-friendly repetition."""
    import random

    rng = random.Random(894)
    syll = ["ka", "to", "mi", "re", "su", "no", "va", "li", "de", "po"]
    words = ["".join(rng.choice(syll) for _ in range(rng.randint(1, 2))) for _ in range(target_tokens)]
    return " ".join(words)


def read_limits() -> dict:
    with open(STATE_PATH) as f:
        s = json.load(f)
    return {"updated_at": s.get("updated_at"), "reused_cache": s.get("reused_cache"), "limits": s.get("limits")}


def run_arm(name: str, content: list) -> dict:
    frame = json.dumps({"type": "user", "message": {"role": "user", "content": content}})
    before = read_limits()
    t0 = time.time()
    p = subprocess.run(
        [CLAUDE_BIN, "-p", "--input-format", "stream-json", "--output-format", "stream-json",
         "--verbose", "--no-session-persistence", "--tools", ""],
        input=frame, capture_output=True, text=True, timeout=300,
    )
    result, rle = None, []
    for line in p.stdout.splitlines():
        try:
            o = json.loads(line)
        except ValueError:
            continue
        if o.get("type") == "result":
            result = {k: o.get(k) for k in ("usage", "total_cost_usd", "is_error")}
        elif o.get("type") == "rate_limit_event":
            rle.append(o)
    time.sleep(SETTLE_S)
    return {"arm": name, "rc": p.returncode, "secs": round(time.time() - t0, 1),
            "before": before, "after": read_limits(), "result": result, "rate_limit_events": rle}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["plan", "run"])
    ap.add_argument("--tokens", type=int, default=1456)
    ap.add_argument("--out", default="vision_limit_probe_result.json")
    a = ap.parse_args()

    png, itok = make_image(a.tokens)
    text = make_text(a.tokens)
    print(f"image: {len(png)}B png, {itok} visual tokens (formula ceil(w/28)*ceil(h/28)); text: {len(text.split())} words")
    if a.cmd == "plan":
        return 0

    prompt = "Reply with the single word OK."
    arms = [
        ("text", [{"type": "text", "text": text + "\n\n" + prompt}]),
        ("image", [{"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                 "data": base64.b64encode(png).decode()}},
                   {"type": "text", "text": prompt}]),
    ]
    out = [run_arm(n, c) for n, c in arms]
    with open(a.out, "w") as f:
        json.dump(out, f, indent=2)
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
