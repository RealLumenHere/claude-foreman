#!/usr/bin/env python3
"""
wq#4890 — Headroom context-compression proxy pilot runner.

Measures headroom proxy (--port 8787, --openai-api-url http://localhost:4000)
fronting LiteLLM on a firmware decompile analysis workload. Compares token
counts, latency, and compression metrics against a direct baseline.

Usage:
    python3 -m venv /tmp/hr-venv
    /tmp/hr-venv/bin/pip install 'headroom-ai[proxy]'
    python3 pilots/headroom_litellm/runner.py

Writes results to pilots/headroom_litellm/results.json and the fleet report
to /home/lumen/scratch/research/headroom_litellm_pilot.md.
"""

import json
import os
import pathlib
import subprocess
import sys
import textwrap
import time

LITELLM_URL = "http://localhost:4000"
PROXY_PORT = 8787
PROXY_URL = f"http://localhost:{PROXY_PORT}"
MODEL = "deepseek-v4-flash"
PILOT_DIR = pathlib.Path(__file__).resolve().parent
RESULTS_PATH = PILOT_DIR / "results.json"
REPORT_PATH = pathlib.Path("/home/lumen/scratch/research/headroom_litellm_pilot.md")

HEADROOM_BIN = None
for candidate in [
    "/tmp/fworker-wq4890/hr-venv/bin/headroom",
    "/tmp/hr-venv/bin/headroom",
]:
    if os.path.isfile(candidate):
        HEADROOM_BIN = candidate
        break
if not HEADROOM_BIN:
    sys.exit("headroom not found — install to a venv first (see docstring)")


def _load_litellm_key():
    path = os.path.expanduser("~/.secrets/foreman-litellm.token")
    with open(path) as f:
        raw = f.read().strip()
    return raw.split("=", 1)[1].strip().strip('"').strip("'") if "=" in raw else raw


LITELLM_KEY = _load_litellm_key()

GHIDRA_DECOMPILE_OUTPUT = textwrap.dedent("""\
    void FUN_08004a20(uint param_1, void *param_2, uint param_3)
    {
      uint uVar1; byte *pbVar3; uint uVar4; uint uVar5;
      if (param_3 != 0) {
        DAT_40023c10 = DAT_40023c10 | 0x100;
        while ((DAT_40023c10 & 0x10000) == 0) { }
        uVar4 = param_1 & 0xfff;
        uVar5 = param_3;
        if (0xfff < param_3 + uVar4) {
          uVar1 = 0x1000 - uVar4;
          FUN_08004980(param_1, param_2, 0x1000 - uVar4);
          param_1 = param_1 + uVar1;
          param_2 = (void *)((int)param_2 + uVar1);
          uVar5 = param_3 - uVar1;
        }
        if (0xfff < uVar5) {
          do {
            FUN_08004980(param_1, param_2, 0x1000);
            param_1 = param_1 + 0x1000;
            param_2 = (void *)((int)param_2 + 0x1000);
            uVar5 = uVar5 - 0x1000;
          } while (0xfff < uVar5);
        }
        if (uVar5 != 0) {
          FUN_08004980(param_1, param_2, uVar5);
        }
        DAT_40023c10 = DAT_40023c10 & 0xfffffeff;
      }
      return;
    }

    void FUN_08004980(uint param_1, void *param_2, uint param_3)
    {
      uint uVar1; byte *pbVar2;
      DAT_40022000 = param_1; DAT_40022004 = 0;
      DAT_40022008 = param_3 - 1;
      DAT_4002200c = DAT_4002200c | 2;
      while ((DAT_4002200c & 1) != 0) { }
      pbVar2 = (byte *)param_2; uVar1 = 0;
      while (uVar1 < param_3) {
        *pbVar2 = *(byte *)(DAT_40022010 + uVar1);
        pbVar2 = pbVar2 + 1; uVar1 = uVar1 + 1;
      }
      return;
    }

    Strings: SPI_FLASH_READ_ERR | TIMEOUT: flash busy > 500ms | FW_VER=3.2.1-rc4
    BOARD=USFS-05-V2 | BUILD_DATE=20260815 | SENSOR_FUSION_ENABLED=1
    I2C_ADDR_MPU9250=0x68 | I2C_ADDR_BMP280=0x76 | SPI_CLK_DIV=4
    FLASH_SIZE_MB=16 | PAGE_SIZE=4096 | SECTOR_SIZE=65536
""")

TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "ghidra_decompile",
            "description": "Decompile a firmware function at the given address.",
            "parameters": {
                "type": "object",
                "properties": {
                    "binary_path": {"type": "string"},
                    "address": {"type": "string"},
                    "include_strings": {"type": "boolean"},
                },
                "required": ["binary_path", "address"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file from the filesystem.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "offset": {"type": "integer"},
                    "limit": {"type": "integer"},
                },
                "required": ["path"],
            },
        },
    },
]

MESSAGES_PLAIN = [
    {
        "role": "user",
        "content": (
            "You are a firmware RE analyst. Analyze the Ghidra decompiler "
            "output below from a Seneca USFS-05-V2 sensor board. Identify "
            "the hardware peripheral, explain the page-aligned read logic, "
            "and suggest meaningful names.\n\n" + GHIDRA_DECOMPILE_OUTPUT
        ),
    },
]

MESSAGES_TOOL_USE = [
    {"role": "system", "content": "You are a firmware reverse-engineering analyst."},
    {
        "role": "user",
        "content": "Decompile the SPI flash read function at 0x08004a20 in "
        "the Seneca USFS-05-V2 firmware and analyze it.",
    },
    {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_ghidra_001",
                "type": "function",
                "function": {
                    "name": "ghidra_decompile",
                    "arguments": json.dumps(
                        {
                            "binary_path": "/tmp/firmware/Seneca_v3.2.1.bin",
                            "address": "0x08004a20",
                            "include_strings": True,
                        }
                    ),
                },
            }
        ],
    },
    {"role": "tool", "tool_call_id": "call_ghidra_001", "content": GHIDRA_DECOMPILE_OUTPUT},
    {
        "role": "user",
        "content": "Identify the peripheral and suggest better names.",
    },
]


def _chat(base_url, messages, tools=None):
    payload = {
        "model": MODEL,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": 1024,
    }
    if tools:
        payload["tools"] = tools

    start = time.monotonic()
    r = subprocess.run(
        [
            "curl", "-s",
            "-H", f"Authorization: Bearer {LITELLM_KEY}",
            "-H", "Content-Type: application/json",
            "-d", json.dumps(payload),
            f"{base_url}/v1/chat/completions",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    elapsed = time.monotonic() - start

    try:
        body = json.loads(r.stdout)
    except json.JSONDecodeError:
        return {"error": r.stdout[:500], "elapsed_s": round(elapsed, 2)}

    usage = body.get("usage", {})
    choices = body.get("choices", [])
    content = (
        choices[0]["message"]["content"]
        if choices and choices[0]["message"].get("content")
        else ""
    )
    return {
        "elapsed_s": round(elapsed, 2),
        "prompt_tokens": usage.get("prompt_tokens", 0),
        "completion_tokens": usage.get("completion_tokens", 0),
        "total_tokens": usage.get("total_tokens", 0),
        "response_length": len(content),
        "model": body.get("model", "?"),
    }


def _start_proxy():
    env = os.environ.copy()
    env["OPENAI_TARGET_API_URL"] = LITELLM_URL
    env["OPENAI_API_KEY"] = LITELLM_KEY
    proc = subprocess.Popen(
        [
            HEADROOM_BIN, "proxy",
            "--port", str(PROXY_PORT),
            "--openai-api-url", LITELLM_URL,
            "--no-subscription-tracking",
            "--mode", "token",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    for attempt in range(30):
        time.sleep(1)
        try:
            r = subprocess.run(
                ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
                 f"{PROXY_URL}/health"],
                capture_output=True, text=True, timeout=3,
            )
            if r.stdout.strip() in ("200", "404"):
                return proc
            r2 = subprocess.run(
                ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
                 f"{PROXY_URL}/stats"],
                capture_output=True, text=True, timeout=3,
            )
            if r2.stdout.strip() == "200":
                return proc
        except Exception:
            pass
    proc.terminate()
    return None


def _stats():
    r = subprocess.run(
        ["curl", "-s", f"{PROXY_URL}/stats"],
        capture_output=True, text=True, timeout=5,
    )
    try:
        return json.loads(r.stdout)
    except Exception:
        return {}


def run_test(label, messages, tools=None):
    print(f"\n--- {label} ---")
    baseline = _chat(LITELLM_URL, messages, tools)
    print(f"  Baseline: {baseline.get('prompt_tokens')}p + "
          f"{baseline.get('completion_tokens')}c = "
          f"{baseline.get('total_tokens')}t, {baseline.get('elapsed_s')}s")

    proxy = _start_proxy()
    if not proxy:
        return {"baseline": baseline, "treatment": {"error": "proxy start failed"}}

    try:
        treatment = _chat(PROXY_URL, messages, tools)
        print(f"  Treatment: {treatment.get('prompt_tokens')}p + "
              f"{treatment.get('completion_tokens')}c = "
              f"{treatment.get('total_tokens')}t, {treatment.get('elapsed_s')}s")
        time.sleep(0.5)
        stats = _stats()
    finally:
        proxy.terminate()
        try:
            proxy.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proxy.kill()

    bp = baseline.get("prompt_tokens", 0)
    tp = treatment.get("prompt_tokens", 0)
    savings = round(((bp - tp) / bp) * 100, 1) if bp else 0
    latency_delta = round(
        treatment.get("elapsed_s", 0) - baseline.get("elapsed_s", 0), 2
    )
    comp = stats.get("summary", {}).get("compression", {})

    print(f"  Prompt savings: {savings}%, latency delta: {latency_delta:+.2f}s")
    print(f"  Headroom compressed: {comp.get('requests_compressed', '?')}, "
          f"tokens removed: {comp.get('total_tokens_removed', '?')}")

    return {
        "baseline": baseline,
        "treatment": treatment,
        "headroom_stats": stats,
        "comparison": {
            "prompt_savings_pct": savings,
            "latency_delta_s": latency_delta,
        },
    }


def main():
    results = {
        "ticket": "wq#4890",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "model": MODEL,
        "litellm_url": LITELLM_URL,
        "proxy_url": PROXY_URL,
        "tests": {},
    }

    results["tests"]["plain_chat"] = run_test(
        "Test 1: plain chat", MESSAGES_PLAIN
    )
    results["tests"]["tool_use"] = run_test(
        "Test 2: tool-use format", MESSAGES_TOOL_USE, TOOLS_SCHEMA
    )

    RESULTS_PATH.write_text(json.dumps(results, indent=2))
    print(f"\nResults: {RESULTS_PATH}")


if __name__ == "__main__":
    main()
