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
# (UID_SPLIT_FWORKER.md:126), plus the registered primaries wq#2681 could not search
# (primary_tree_guard.py STATIC_PRIMARY_TREES) that wq#2741 added. `questfree` is a
# registered primary too but the directory no longer exists on Lumen (checked 2026-10-14):
# it stays in the list so a resurrected tree is searched, and os.walk on a missing root
# yields nothing. `logs` is the one registered primary still NOT searched - run artifacts,
# not code.
ROOTS = [
    "foreman",
    "mailbot",
    "pokemon",
    "agents/work-queue",
    "kb",
    "bin",
    "projects/nest",
    # wq#2741
    "questfree",
    "pstack",
    "mission-control",
    "minerva",
    "agent",
    "fleet/inventory",
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
DATA_MARK = (".db", ".sqlite")   # wq#2741: sqlite stores - scraped text mentions vendors, never code

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
    # --- wq#2741: the registered primaries wq#2681 could not search (agent, minerva, ...) ---
    "agent/litellm_config.yaml": "config — the LIVE gateway roster itself; `openai/<m>` is LiteLLM provider prefix for OpenAI-compatible LOCAL endpoints (Ollama on hathor/nephthys/isis/khepri/ptah/sekhmet) and api.z.ai, NOT api.openai.com. Every surface above that says via LiteLLM lands here",
    "agent/litellm_config.yaml.wq236_proposed": "snapshot — proposed config, never applied",
    "agent/staged-archive/litellm_config.yaml.staged.minimax-m3.STALE-20260926": "snapshot — stale staged config",
    "agent/staged-archive/litellm_config.yaml.staged.wq233.STALE-20260926": "snapshot — stale staged config",
    "minerva/vision-eval/lumen-vision-local.litellm.staged.yaml": "config — staged; comment only (drop_params ... openai fields ... for ollama)",
    "minerva/factory_context.py": "policy text — tells the factory LLM never to propose anthropic/openai routes; no call",
    "minerva/minerva_factory.py": "SURFACE 21 — already a row; the hit is the FORBIDDEN_PROVIDERS denylist at :283, not a call",
    "minerva/judgearena_bench.py": "SURFACE 24 — JudgeArena judge bench: langchain ChatOpenAI against the LiteLLM loopback (BASE = LITELLM_BASE_URL or 127.0.0.1:4000/v1, :28). lumen-judgearena-bench.timer is DISABLED (installed, not live): one `systemctl enable` from live",
    "minerva/test_judgearena_correlation.py": "fixture — mocks langchain_openai for judgearena_bench",
    "minerva/v4/reports/fa_episode_failures.json": "report data — prose naming api.deepseek.com as a SPOF; no call",
    "agent/deepseek_cost_tracker.py": "not a call — GET /user/balance; deepseek-cost-tracker.timer is DISABLED",
    "agent/signal_engine.py": "keyword list — retired (wq#2041, signal-engine retired 2026-10-11); no call",
    "agent/tag_auto_generator.py": "keyword list — tag vocabulary; no call",
    "agent/lumen_intel.py": "keyword list — relevance filter; no call",
    "agent/processed_research.json": "data — processed-research filename list matching on text",
    "agent/crew.py": "dormant — CrewAI pipeline; `openai/smart|fast` are LiteLLM aliases on localhost:4000, not api.openai.com; no installed unit, no cron, no live importer",
    "agent/backfill_content.py": "dormant — one-off direct api.deepseek.com backfill (DS_KEY); no unit/cron/script references it",
    "agent/attic/lumen_agent_v2.py": "dormant — attic; direct api.deepseek.com",
    "agent/lumen_agent_v2.py.pre_cap_patch": "snapshot — pre-patch copy of attic/lumen_agent_v2.py",
    "agent/lumen_agent.py": "dormant — legacy direct Gemini; only agent/run.sh (2026-03-18) references it; lumen-agent.service is MASKED",
    "agent/lumen_agent_v3.py": "dormant — legacy direct Groq + Gemini; only lumen_research_agent.py imports it; no installed unit",
    "agent/lumen_research_agent.py": "dormant — legacy direct Groq + Gemini (mtime 2026-07-12); no installed unit, no process, no cron",
    "agent/lumen_deep_research_orchestrator.py": "dormant — direct Groq + Gemini. fleet_tools.yaml:55-58 says runs via service but NO installed unit/timer/cron execs it, no process on 2026-10-14, newest scratch/deep_research_output file is 2026-07-13 (stale claim). agents/truth_table.yaml:90 queues its PBOX excision",
    "agent/llm_project_scout.py": "dormant — direct Groq + Gemini; no installed unit",
    "agent/project_engine.py": "dormant — direct Groq; only referenced by lumen-builder.service, which exists as a file in agent/ and is NOT installed",
    # --- wq#2741: hits that appeared in the original seven roots after wq#2681 (new files since 2026-09-25) ---
    "bin/angr-venv/lib/python3.14/site-packages/angr/llm_client.py": "vendored - angr optional pydantic_ai OpenAI provider (RE toolkit venv); no fleet unit invokes it",
    "bin/emba-dist/helpers/helpers_emba_defaults.sh": "vendored - EMBA log-filename variable (q02_openai_question.txt); no call",
    "bin/gitleaks": "vendored - gitleaks rule table (openai-api-key rule id); no call",
    "bin/provider-watch": "monitor - release watcher (npm/GitHub versions of provider SDKs, e.g. @anthropic-ai/claude-code); no model call",
    "bin/rotate-provider-keys": "not a completion - key-liveness verifier: GET /models on api.deepseek.com and generativelanguage.googleapis.com (:23-24); the model names are the trace-landed check through LiteLLM",
    "foreman/harness/systemd/lumen-despag.service": "comment only - explains a cgroup bug that cut claude children off api.anthropic.com; the call is the claude CLI (surface 1)",
    "foreman/ops/wq2903-model-ref-drift/tests/fixtures/callers/clean.py": "fixture",
    "foreman/ops/wq2904-judge-model-group-fix/litellm_config_snippet.yaml": "config - judge model-group snippet (openai/llama3.1:8b = LiteLLM prefix for a local Ollama endpoint)",
    "foreman/ops/wq3097-litellm-ghsa-3cv6-patch/scan_callers_pr36011.py": "scanner - regex for /openai/deployments/ URL shapes (GHSA caller scan); no call",
    "foreman/ops/wq3169-litellm-completion-probe/tests/test_litellm_completion_probe.py": "fixture",
    "foreman/ops/wq3212-litellm-dead-rungs/tests/test_remove_dead_rungs.py": "fixture",
    "foreman/ops/wq3336-provider-watch/provider_watch.py": "monitor - source of bin/provider-watch (same verdict)",
    "foreman/ops/wq3354-fleet-selfheal/tests/test_exposure_census.py": "fixture",
    "foreman/ops/wq3354-fleet-selfheal/tests/test_resource_classes.py": "fixture",
    "foreman/ops/wq3543-gemini-flash-403/fragments/gemini-flash-fallback.yaml": "config - fallback fragment; the endpoint string is in a comment about a direct-probe 403",
    "mailbot/test_known_associates.mjs": "SURFACE 20 note - probe harness, direct DeepSeek fetch (dev-run, same key as test_persona/test_dodge)",
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
    """run-artifact | doc | snapshot | data | vendored | source"""
    if RUN_ARTIFACT.search("/" + rel) or rel.endswith(LOG_EXT):
        return "run-artifact"
    if rel.endswith(DOC_EXT):
        return "doc"
    if "queue.db" in rel:
        return "snapshot"
    if ".bak" in rel:
        return "snapshot"
    if any(m in os.path.basename(rel) for m in DATA_MARK):
        return "data"
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
    for name in ("run-artifact", "doc", "snapshot", "data", "vendored", "source"):
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
