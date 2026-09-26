#!/usr/bin/env python3
"""Reproducer for MODEL_ROUTING_AUDIT.md §1's endpoint-string pass (wq#2681).

§0 claims every number in the audit is reproducible with `model_routing_audit.py`;
§1.1 admits that is true only of §2's burn statistics — §1's surface list had no
reproducer at all, which is how wq#2664 shipped a hit count off by an order of
magnitude and a "mailbot only" bottom line that missed three live surfaces.
This is §1's reproducer.

    python3 research/model_routing_endpoint_grep.py           # population + verdicts
    python3 research/model_routing_endpoint_grep.py --check    # exit 1 on an unadjudicated hit

Do NOT re-run this pass with the box's `grep`: /usr/bin/grep here is ugrep 7.8.4,
which honours .gitignore on -r, so `grep -r <root>` and `grep -r <root>/<ignored>`
return different populations for the same pattern (kb/ISSUE-LOG.md). This walker
honours nothing and is therefore the stable number.
"""
import argparse
import os
import re
import sys

HOME = "/home/lumen"

# The seven registered primaries a foreman worktree gets read access to
# (UID_SPLIT_FWORKER.md:126). The other six registered primaries are NOT searched —
# see §1's "what this still does not cover".
ROOTS = [
    "foreman",
    "mailbot",
    "pokemon",
    "agents/work-queue",
    "kb",
    "bin",
    "projects/nest",
]

PATTERNS = [
    r"api\.deepseek\.com",
    r"api\.anthropic\.com",
    r"api\.openai\.com",
    r"generativelanguage\.googleapis\.com",
    r"@anthropic-ai/",
    r"openai",
]
PAT_RE = re.compile("|".join(PATTERNS))

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv"}
RUN_ARTIFACT = re.compile(r"/(_runs|events|pilots|logs)/")
LOG_EXT = (".jsonl", ".log")
DOC_EXT = (".md", ".md.bak", ".txt", ".diff", ".patch")

# Every live source/config hit, with the verdict from the wq#2681 read-through.
# "SURFACE n" = a row in §1's table. Anything else is why it is not a surface.
# A hit that is not in this table is unreviewed and --check fails on it.
ADJUDICATED = {
    # --- surfaces ---
    "bin/minerva-factory": "SURFACE 21 — symlink to minerva/minerva_factory.py; deepseek-v4-pro + cerebras-oss-120b rescue, via LiteLLM",
    "foreman/tools/council/council.yaml": "SURFACE 22 — seat roster for tools/council/council.py (bin/research-council)",
    "foreman/harness/evals/worker-quality/promptfooconfig.yaml": "SURFACE 23 — promptfoo LLM-as-judge, LiteLLM loopback",
    "mailbot/src/worker.js": "SURFACE 20 — the four production DeepSeek calls",
    "mailbot/evals/lib/score.mjs": "SURFACE 20 note — eval-harness DeepSeek grader, same key, run by hand",
    "mailbot/test_persona.mjs": "SURFACE 20 note — probe harness, direct DeepSeek fetch",
    "mailbot/test_dodge.mjs": "SURFACE 20 note — probe harness, direct DeepSeek fetch",
    # --- account/balance endpoints, not completions ---
    "foreman/governor/foreman_usaged.py": "not a call — GET /api/oauth/usage (quota headroom), surface 1's governor",
    "foreman/ops/wq1252-lumen-public-guardian/checks_email.py": "not a call — GET /user/balance",
    "foreman/ops/wq1252-lumen-public-guardian/config.json": "config — holds the balance URL checks_email.py reads",
    # --- monitoring/probe code for surfaces already in the table ---
    "foreman/ops/wq1590-deepseek-connect-guard/deepseek_connect_alert.py": "monitor — D4 connect-failure alert (FLEET_COST_PLAN §2)",
    "foreman/research/oauth_passthrough_probe.py": "research proxy — stands in for api.anthropic.com",
    "foreman/research/headroom_proxy_gate.py": "research proxy — same",
    "foreman/dispatcher/cli_canary.py": "package name — checks @anthropic-ai/claude-code is installed",
    "foreman/harness/node_render_gateway.py": "config generator — emits openai/<model> aliases for local rungs",
    # wq#2743: the reproducer matches its own patterns, so --check was red on a clean
    # main from the moment it landed (de5b586) — a gate that cannot be green gates nothing.
    "foreman/research/model_routing_endpoint_grep.py": "self — this reproducer's own pattern table; no call",
    "foreman/claude-shim/claude_shim.py": "dormant — CLAUDE_SHIM_DESIGN.md:10 'DESIGN FOR REVIEW'; claude-shim.service not installed, :8091 not listening",
    # --- spend guards / scanners that read provider names, never dispatch ---
    "foreman/ops/wq1406-paid-fallback-guard/audit_fallback_chains.py": "allowlist — paid-provider prefixes",
    "foreman/ops/wq1406-paid-fallback-guard/paid_fallback_alert.py": "allowlist — same",
    "foreman/ops/wq1406-paid-fallback-guard/paid_spend_tripwire.py": "allowlist — same",
    "foreman/ops/wq2286-gitea-secret-scan/scan.py": "regex — sk-ant-/sk- key shapes for a secret scanner",
    "foreman/reports/2457_probe_timer_spend_script_marker_false_positives.py": "report artifact — quotes the keyword list",
    "foreman/reports/opsaudit_a14_seed_tickets.sh": "report artifact — ticket prose",
    "foreman/research/wq1717_hub_expansion_probe.json": "scrape data — MCP server names",
    "mailbot/check_redteam_results.mjs": "regex — leak-detector vendor-name list",
    "mailbot/src/leakfilter.js": "regex — same, production leak filter",
    "mailbot/test_redteam.mjs": "regex — same",
    # --- LiteLLM config for surfaces already routed through the gateway ---
    "foreman/research/models.yaml": "config — surface 1's own roster (api_base comment)",
    "foreman/ops/inference-registry/litellm_local_block.yaml": "config — local model_list block",
    "foreman/ops/wq1337-hapi-bringup/litellm-hapi1-dark.yaml": "config — hapi1 dark block",
    "foreman/ops/wq1406-paid-fallback-guard/litellm_config_snippet.yaml": "config — fallback-chain snippet",
    "foreman/ops/wq1711-difficulty-router/litellm_config_snippet.yaml": "config — difficulty-router snippet",
    # --- tests/fixtures for the files above ---
    "foreman/dispatcher/tests/test_cli_canary.py": "fixture",
    "foreman/dispatcher/tests/test_node_render_gateway.py": "fixture",
    "foreman/ops/wq1252-lumen-public-guardian/tests/failure_drills.py": "fixture",
    "foreman/ops/wq1295-litellm-health-hardening/tests/test_health_check_model_filter.py": "fixture",
    "foreman/ops/wq1406-paid-fallback-guard/tests/test_audit_fallback_chains.py": "fixture",
    "foreman/ops/wq1590-deepseek-connect-guard/tests/test_deepseek_connect_alert.py": "fixture",
    "foreman/ops/wq1623-qwen36-queue-guard/tests/test_audit_rung_timeout.py": "fixture",
    "foreman/ops/wq2428-timer-spend-guard/tests/test_timer_spend_audit.py": "fixture",
    "foreman/ops/wq2428-timer-spend-guard/.pytest_cache/v/cache/nodeids": "fixture — pytest cache of the above",
}


def bucket(rel):
    """run-artifact | doc | snapshot | vendored | source"""
    if RUN_ARTIFACT.search("/" + rel) or rel.endswith(LOG_EXT):
        return "run-artifact"
    if rel.endswith(DOC_EXT):
        return "doc"
    if "queue.db" in rel:
        return "snapshot"
    if ".bak" in rel:
        return "snapshot"
    if rel == "bin/trivy":
        return "vendored"
    return "source"


def scan(home=HOME, roots=ROOTS):
    found = {}
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(os.path.join(home, root)):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for fn in filenames:
                path = os.path.join(dirpath, fn)
                try:
                    with open(path, "r", errors="ignore") as fh:
                        n = sum(1 for ln in fh if PAT_RE.search(ln))
                except OSError:
                    continue
                if n:
                    found[os.path.relpath(path, home)] = n
    return found


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="exit 1 if a source hit has no verdict")
    ap.add_argument("--home", default=HOME)
    args = ap.parse_args()

    found = scan(args.home)
    by_bucket = {}
    for rel in found:
        by_bucket.setdefault(bucket(rel), []).append(rel)

    total = len(found)
    src = sorted(by_bucket.get("source", []))
    print(f"population over {len(ROOTS)} roots: {total} files match")
    for name in ("run-artifact", "doc", "snapshot", "vendored", "source"):
        print(f"  {name:<13} {len(by_bucket.get(name, [])):>5}")

    unreviewed = [r for r in src if r not in ADJUDICATED]
    print(f"\nsource hits: {len(src)} ({len(unreviewed)} unadjudicated)")
    for rel in src:
        print(f"  {'NEW  ' if rel in unreviewed else '     '}{rel}: {ADJUDICATED.get(rel, '*** no verdict — read it and add one ***')}")

    stale = [r for r in ADJUDICATED if r not in found]
    if stale:
        print("\nadjudicated but no longer matching (file moved or pattern gone):")
        for rel in sorted(stale):
            print(f"  {rel}")

    if args.check and unreviewed:
        print(f"\nFAIL: {len(unreviewed)} source hit(s) with no verdict", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
