#!/usr/bin/env python3
"""wq#2457 — probe: wq#2428's timer_spend_audit.py script-body markers flag
data literals, not calls, on four real, currently-installed, currently-enabled
timers.

Each snippet below is copied verbatim (2026-09-24) from a live file the audit
scans today with full read access -- the exact deploy shape `install.sh`
prints as the real audit ("Full-read sweep first ... as lumen, with every
ExecStart target readable, --strict is meaningful and this is the real
audit"). `lumen-timer-spend-guard.service` also runs `User=lumen`, so the
installed daily guard sees the same four false positives this probe
reproduces.

None of the four lines invoke the Claude CLI or the Anthropic SDK. All four
are data literals: two topic-keyword lists, an exclude-list, and a
FORBIDDEN_*_ENVS guard tuple whose entire purpose is to *block* an Anthropic
key from being auto-applied.

Imports the real, unmodified `timer_spend_audit` module -- no reimplementation
of its regexes here.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "..", "ops", "wq2428-timer-spend-guard"))
import timer_spend_audit as tsa  # noqa: E402

CASES = [
    ("/home/lumen/agent/lumen_intel.py:93", "lumen-intel.timer (enabled, fires hourly)",
     '    "code generation", "swe-bench", "claude", "gpt", "llama", "qwen",'),
    ("/home/lumen/agent/signal_engine.py:47", "lumen-signal-engine.timer (enabled, daily)",
     '    "ai","llm","ollama","mcp","claude","gpt","openai","anthropic",'),
    ("/home/lumen/personas/worklog_gen.py:17", "lumen-worklog.timer (enabled, ~6h)",
     'EXCLUDE=("persona","agent_bridge","brain","worklog","hermit","identity",'
     '"seal","opus","sonnet","claude","fable","anthropic","litellm",'
     '"lumen-convo-learn","case-book","self-updating")'),
    ("/home/lumen/minerva/minerva_factory.py:284", "minerva-factory.timer (enabled, ~4h)",
     "FORBIDDEN_KEY_ENVS = ('ANTHROPIC_API_KEY', 'OPENAI_API_KEY')"),
]


def main():
    bad = 0
    for path, timer, line in CASES:
        markers = tsa.markers_in_script(line)
        verdict = "FLAGGED" if markers else "clean"
        print(f"{verdict:8} {path}  [{timer}]")
        print(f"         line: {line!r}")
        print(f"         markers_in_script() -> {markers}")
        if markers:
            bad += 1
    print()
    print(f"{bad}/{len(CASES)} data-literal lines misread as a live Anthropic call")
    print()
    print("Each of these is a real ExecStart target of a real, enabled, currently")
    print("firing timer (confirmed via `systemctl list-timers --all`), so a full-read")
    print("run of timer_spend_audit.py (no --roots, as `lumen`) classifies all four")
    print("services `cloud` and --alert would file an aq ticket + high-priority ntfy")
    print("push on first run, none of it a real Anthropic call.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
