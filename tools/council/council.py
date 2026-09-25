#!/usr/bin/env python3
"""research-council — fan one hard prompt out to several models, then synthesize (wq#2628).

Pattern after karpathy/llm-council, cut down to two stages:
  1. fan-out   every enabled seat in council.yaml answers the prompt in parallel through
               the LiteLLM gateway's OpenAI-compatible route (prompt-forge/llm.py pattern).
  2. chairman  one model reads the answers, labelled Response A/B/C with no model names so
               it can't favour a brand, and writes a synthesis with fixed sections:
               consensus, disagreements, unique points, synthesis, confidence.

A seat that errors or times out is dropped and reported; the council goes ahead as long as
min_responses seats answered. Every run writes a JSON record (prompt, each seat's answer or
error, the chairman used, the synthesis) under --out-dir.

  research-council "Should we shard queue.db or move to Postgres?"
  echo "$PROMPT" | research-council -
  research-council --models gemini-flash,deepseek-v4-pro --json "..."
  research-council --dry-run "..."          # show seats and chairman, call nothing

Exit: 0 ok, 2 usage/config error, 3 too few seats answered, 4 no chairman met the contract.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_CONFIG = HERE / "council.yaml"
GATEWAY = os.environ.get("COUNCIL_LITELLM_URL", "http://127.0.0.1:4000")
KEY_DIR = Path("/home/lumen/.secrets/litellm-service-keys")
# council.key is the council's own service key once minted; until then it borrows
# prompt-forge's, which already has every chat alias on the gateway.
KEY_FILES = [Path(p) for p in os.environ.get(
    "COUNCIL_KEY_FILES", f"{KEY_DIR}/council.key:{KEY_DIR}/prompt-forge.key").split(":")]
DEFAULT_OUT_DIR = Path(os.environ.get("COUNCIL_OUT_DIR", "/home/lumen/logs/council"))

LABELS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
REQUIRED_SECTIONS = ("## Consensus", "## Disagreements", "## Synthesis")

MEMBER_SYSTEM = """You are one member of a research council. Other models answer the same \
question independently, and a chairman will compare the answers.
Research the question thoroughly and answer it directly. State your key claims, the \
reasoning or evidence behind each, and how confident you are in each. Name the assumptions \
you are making and what evidence would change your answer. Be concrete: numbers, names, \
mechanisms. Do not pad."""

CHAIRMAN_SYSTEM = """You chair a research council. Several models answered the same \
question independently. Their answers are labelled Response A, B, C... and the models are \
not named. Judge the content, not the style or length.

Write your report in markdown with exactly these sections, in this order:

## Consensus
Claims most or all responses agree on. Cite which responses, e.g. (A, C).

## Disagreements
Every point where responses conflict. For each: what each side says, which responses hold \
it, and which side the evidence favours and why. Write "None." if there are none.

## Unique points
Substantive claims only one response made, with your call on whether each holds up.

## Synthesis
The best answer to the original question, combining the strongest reasoning across \
responses. Correct any errors you found. This section must stand on its own.

## Confidence
high / medium / low, with one sentence on why and what would raise it."""


class GatewayError(RuntimeError):
    def __init__(self, msg, status=None):
        super().__init__(msg)
        self.status = status


@dataclass
class Seat:
    seat: str
    model: str
    timeout_s: float
    enabled: bool = True
    max_tokens: int = 0     # 0 = council.member_max_tokens


@dataclass
class Answer:
    seat: str
    model: str
    ok: bool
    latency_s: float
    text: str = ""
    error: str = ""
    label: str = ""


@dataclass
class Council:
    seats: list[Seat]
    chairman: list[str]
    min_responses: int = 2
    member_max_tokens: int = 32000
    chairman_max_tokens: int = 32000
    timeout_s: float = 300
    retry_429_s: float = 30


@dataclass
class Result:
    prompt: str
    answers: list[Answer]
    chairman: str = ""
    synthesis: str = ""
    chairman_attempts: list[dict] = field(default_factory=list)
    started_at: str = ""
    elapsed_s: float = 0.0


def load_config(path, models_override=None):
    import yaml
    with open(path) as f:
        doc = yaml.safe_load(f) or {}
    timeout = float(doc.get("timeout_s", 300))
    if models_override:
        seats = [Seat(seat=m, model=m, timeout_s=timeout) for m in models_override]
    else:
        seats = [Seat(seat=str(m.get("seat") or m["model"]), model=str(m["model"]),
                      timeout_s=float(m.get("timeout_s", timeout)),
                      enabled=bool(m.get("enabled", True)),
                      max_tokens=int(m.get("max_tokens", 0)))
                 for m in doc.get("members") or []]
    seats = [s for s in seats if s.enabled]
    if not seats:
        raise ValueError(f"{path}: no enabled members")
    chairman = [str(c) for c in doc.get("chairman") or []] or [seats[0].model]
    return Council(seats=seats, chairman=chairman,
                   min_responses=int(doc.get("min_responses", 2)),
                   member_max_tokens=int(doc.get("member_max_tokens", 32000)),
                   chairman_max_tokens=int(doc.get("chairman_max_tokens", 32000)),
                   timeout_s=timeout,
                   retry_429_s=float(doc.get("retry_429_s", 30)))


def api_key():
    key = os.environ.get("LITELLM_API_KEY", "").strip()
    if key:
        return key
    for path in KEY_FILES:
        try:
            key = path.read_text().strip()
        except OSError:
            continue
        if key:
            return key
    raise GatewayError("no LiteLLM key (env LITELLM_API_KEY or "
                       + " / ".join(map(str, KEY_FILES)) + ")")


def gateway_chat(model, system, user, *, max_tokens, timeout_s):
    """One completion through the gateway. Raises GatewayError on any failure."""
    body = json.dumps({
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
    }).encode()
    req = urllib.request.Request(
        GATEWAY.rstrip("/") + "/v1/chat/completions", data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {api_key()}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            doc = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        detail = e.read()[:300].decode("utf-8", "replace")
        raise GatewayError(f"HTTP {e.code}: {detail}", status=e.code) from e
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        raise GatewayError(f"{type(e).__name__}: {e}") from e
    try:
        choice = doc["choices"][0]
        text = choice["message"]["content"] or ""
    except (KeyError, IndexError, TypeError) as e:
        raise GatewayError(f"reply missing choices/message: {e}") from e
    if not text.strip():
        raise GatewayError(f"empty completion (finish_reason={choice.get('finish_reason')})")
    return text.strip()


def ask_seat(seat, prompt, council, chat, sleep=time.sleep):
    """One seat's answer. A 429 gets one retry after retry_429_s: free-tier per-minute
    quotas (gemini-flash) clear within that, and every other error is final."""
    t0 = time.monotonic()
    for attempt in (1, 2):
        try:
            text = chat(seat.model, MEMBER_SYSTEM, prompt,
                        max_tokens=seat.max_tokens or council.member_max_tokens,
                        timeout_s=seat.timeout_s)
            return Answer(seat.seat, seat.model, True, round(time.monotonic() - t0, 1),
                          text=text)
        except GatewayError as e:
            if e.status == 429 and attempt == 1:
                sleep(council.retry_429_s)
                continue
            return Answer(seat.seat, seat.model, False, round(time.monotonic() - t0, 1),
                          error=str(e)[:300])


def fan_out(prompt, council, chat):
    with cf.ThreadPoolExecutor(max_workers=len(council.seats)) as pool:
        answers = list(pool.map(lambda s: ask_seat(s, prompt, council, chat), council.seats))
    for label, ans in zip(LABELS, [a for a in answers if a.ok]):
        ans.label = label
    return answers


def chairman_prompt(prompt, answers):
    parts = [f"# Question\n\n{prompt}\n"]
    for a in answers:
        if a.ok:
            parts.append(f"# Response {a.label}\n\n{a.text}\n")
    return "\n".join(parts)


def missing_sections(text):
    return [s for s in REQUIRED_SECTIONS if s.lower() not in text.lower()]


def synthesize(result, council, chat):
    """Try each chairman in order, skipping models that already failed as seats."""
    failed = {a.model for a in result.answers if not a.ok}
    user = chairman_prompt(result.prompt, result.answers)
    for model in council.chairman:
        if model in failed:
            result.chairman_attempts.append({"model": model, "skipped": "failed as a seat"})
            continue
        t0 = time.monotonic()
        try:
            text = chat(model, CHAIRMAN_SYSTEM, user,
                        max_tokens=council.chairman_max_tokens, timeout_s=council.timeout_s)
        except GatewayError as e:
            failed.add(model)
            result.chairman_attempts.append({"model": model, "error": str(e)[:300]})
            continue
        missing = missing_sections(text)
        result.chairman_attempts.append({"model": model,
                                         "latency_s": round(time.monotonic() - t0, 1),
                                         "missing_sections": missing})
        if not missing:
            result.chairman, result.synthesis = model, text
            return True
    return False


def run(prompt, council, chat=None):
    chat = chat or gateway_chat
    t0 = time.monotonic()
    result = Result(prompt=prompt, answers=[],
                    started_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"))
    result.answers = fan_out(prompt, council, chat)
    if sum(a.ok for a in result.answers) >= council.min_responses:
        synthesize(result, council, chat)
    result.elapsed_s = round(time.monotonic() - t0, 1)
    return result


def render_markdown(result):
    lines = ["# Research council", ""]
    for a in result.answers:
        tag = f"Response {a.label}" if a.ok else "no answer"
        extra = f"{a.latency_s}s" if a.ok else a.error[:160]
        lines.append(f"- **{a.seat}** (`{a.model}`): {tag}, {extra}")
    lines.append(f"- chairman: `{result.chairman or 'none'}`, total {result.elapsed_s}s")
    lines.append("")
    lines.append(result.synthesis or "_No synthesis: see the run record for why._")
    return "\n".join(lines)


def write_record(result, out_dir):
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"council-{time.strftime('%Y%m%dT%H%M%S')}-{os.getpid()}.json"
    path.write_text(json.dumps(asdict(result), indent=2))
    return path


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="research-council",
        description="Fan a prompt out to several LiteLLM models and synthesize the answers.",
        epilog="Seats and chairman order live in council.yaml next to this script.")
    ap.add_argument("prompt", help="the question; '-' reads it from stdin")
    ap.add_argument("--config", default=str(DEFAULT_CONFIG), help="council yaml")
    ap.add_argument("--models", help="comma list of gateway aliases; replaces the seats")
    ap.add_argument("--json", action="store_true", help="print the full run record as JSON")
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR),
                    help="where the JSON run record goes (default %(default)s)")
    ap.add_argument("--dry-run", action="store_true", help="print the plan, call nothing")
    args = ap.parse_args(argv)

    prompt = sys.stdin.read() if args.prompt == "-" else args.prompt
    if not prompt.strip():
        print("research-council: empty prompt", file=sys.stderr)
        return 2
    models = [m.strip() for m in args.models.split(",") if m.strip()] if args.models else None
    try:
        council = load_config(args.config, models)
    except (OSError, ValueError, KeyError) as e:
        print(f"research-council: bad config: {e}", file=sys.stderr)
        return 2

    if args.dry_run:
        print(json.dumps({"seats": [asdict(s) for s in council.seats],
                          "chairman": council.chairman,
                          "min_responses": council.min_responses}, indent=2))
        return 0

    result = run(prompt.strip(), council)
    record = write_record(result, Path(args.out_dir))
    print(json.dumps(asdict(result), indent=2) if args.json else render_markdown(result))
    print(f"\nrun record: {record}", file=sys.stderr)
    answered = sum(a.ok for a in result.answers)
    if answered < council.min_responses:
        print(f"research-council: only {answered}/{len(result.answers)} seats answered "
              f"(min {council.min_responses})", file=sys.stderr)
        return 3
    if not result.synthesis:
        print("research-council: no chairman produced a report with the required sections",
              file=sys.stderr)
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
