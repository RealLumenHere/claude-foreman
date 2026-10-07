#!/usr/bin/env python3
"""wq#3558 live repro of the ollama scheduler wedge on Lumen (bounded).

control: E1 (slow embed, no keep_alive) in flight -> chat C for a cold model.
         C must load as soon as E1 finishes.
wedge:   same, plus E2 (tiny embed WITH keep_alive) sent while E1 is in flight.
         E2's fast path overwrites the evict-marker sessionDuration=0, so C waits
         for E2's keep_alive timer instead of E1's completion.
--unwedge N: N s after E1 ends, if C is still pending, POST /api/generate
         {model: nomic, keep_alive: 0} (expireRunner) and time C's completion.
"""
import argparse, json, threading, time, urllib.request

BASE = "http://127.0.0.1:11434"
EMBED = "nomic-embed-text:latest"
CHAT = "hf.co/LiquidAI/LFM2.5-1.2B-Instruct-GGUF:Q4_K_M"
T0 = time.monotonic()
ev = {}


def t():
    return round(time.monotonic() - T0, 2)


def post(path, body, timeout):
    req = urllib.request.Request(BASE + path, json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def run(name, path, body, timeout):
    ev[name + "_start"] = t()
    try:
        post(path, body, timeout)
        ev[name + "_end"] = t()
    except Exception as e:  # noqa: BLE001 - recorded, not raised
        ev[name + "_err"] = f"{t()} {type(e).__name__}"


def ps():
    with urllib.request.urlopen(BASE + "/api/ps", timeout=5) as r:
        return [m["name"] for m in json.loads(r.read())["models"]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["control", "wedge"])
    ap.add_argument("--batch", type=int, default=96)
    ap.add_argument("--e2-keep", default="3m")
    ap.add_argument("--unwedge", type=float, default=0)
    ap.add_argument("--observe", type=float, default=40)
    ap.add_argument("--heal-cmd", default="")
    a = ap.parse_args()

    text = "the quick brown fox jumps over the lazy dog " * 40
    post("/api/embed", {"model": EMBED, "input": "warm"}, 120)
    ev["ps_before"] = ps()
    e1 = threading.Thread(target=run, args=("E1", "/api/embed",
                          {"model": EMBED, "input": [text] * a.batch}, 300))
    c = threading.Thread(target=run, args=("C", "/api/generate",
                         {"model": CHAT, "prompt": "Say OK", "stream": False,
                          "options": {"num_predict": 4}}, 400), daemon=True)
    e1.start(); time.sleep(0.5)
    c.start(); time.sleep(0.5)
    if a.mode == "wedge":
        body = {"model": EMBED, "input": "pin"}
        if a.e2_keep != "none":
            body["keep_alive"] = a.e2_keep
        run("E2", "/api/embed", body, 300)
    e1.join()
    ev["ps_after_E1"] = ps()
    deadline = time.monotonic() + a.observe
    while c.is_alive() and time.monotonic() < deadline:
        time.sleep(1)
    ev["C_pending_at_observe_end"] = c.is_alive()
    if c.is_alive() and a.unwedge:
        ev["ps_before_unwedge"] = ps()
        if a.heal_cmd:
            import subprocess
            ev["U_start"] = t()
            p = subprocess.run(a.heal_cmd, shell=True, capture_output=True, text=True)
            ev["U_end"] = t()
            ev["U_out"] = p.stdout.strip()[-300:]
        else:
            run("U", "/api/generate", {"model": EMBED, "keep_alive": 0}, 30)
        c.join(120)
    ev["ps_final"] = ps()
    print(json.dumps(ev))


if __name__ == "__main__":
    main()
