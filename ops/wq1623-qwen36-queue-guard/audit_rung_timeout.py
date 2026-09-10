#!/usr/bin/env python3
"""wq#1623 — audit qwen36-local/qwen36-live's configured request timeout
against what real callers actually wait.

Measured during wq#1588 (Isis llama-qwen36.service, :8095, 2 slots x 8192
ctx): the rung is not down (723ms health probe), it is queue-starved — a
4,910-token prompt spent 277s in PROMPT EVAL alone. Live journal 05:43-05:47Z
showed three sibling tasks cancelled by their own callers while waiting, at
40-160s elapsed. qwen36-local carried `timeout: 600` and qwen36-live carried
`timeout: 120` against the same upstream. Nothing observed waits 600s — real
callers cancel at 40-160s — so 600 buys nothing but a held connection.

Rule: every deployment whose litellm_params.model targets the qwen36 Isis
upstream (ollama/qwen36* or an api_base containing the Isis tailscale IP,
100.100.129.24) must carry `litellm_params.timeout` no larger than
MAX_CALLER_WAIT_S. A timeout above that ceiling is a violation: it holds a
connection open long past the point every observed caller has already given
up and cancelled.

Usage (on the gateway node, where the live config actually lives — this
BUILD worker's sandbox has no read access to it, same convention as
wq#897/wq#911/wq#948/wq#1299/wq#1406; see README.md):

    python3 audit_rung_timeout.py /home/lumen/agent/litellm_config.yaml

Exits 1 if any deployment violates the rule, 0 otherwise. Prints a human
report; --json emits the same findings as machine-readable JSON.
"""
import json
import sys

# Longest cancellation observed in the wq#1588/wq#1623 journal window
# (05:43-05:47Z: callers cancelled at 40-160s). A generous margin above that
# ceiling still beats the 600s the rung carried before this ticket, which
# bought nothing (nothing observed waits anywhere near it) but held a
# connection open for minutes past every real caller's own patience.
MAX_CALLER_WAIT_S = 180

ISIS_TAILSCALE_IP = "100.100.129.24"


def targets_qwen36_isis(litellm_model: str, api_base: str) -> bool:
    """True if a deployment's backend is the qwen36 rung served off Isis —
    matched on the ollama/qwen36* model string OR an api_base pointed at
    Isis's tailscale address, since qwen36-local and qwen36-live are two
    LiteLLM aliases for variants of the same upstream (ticket: "carries
    timeout 600 and ... 120 against the same upstream")."""
    m = (litellm_model or "").strip().lower()
    base = (api_base or "").strip().lower()
    return m.startswith("ollama/qwen36") or ISIS_TAILSCALE_IP in base


def audit_deployment(entry: dict) -> dict | None:
    """Returns a violation dict for one model_list entry, else None."""
    name = entry.get("model_name")
    params = entry.get("litellm_params") or {}
    if not targets_qwen36_isis(params.get("model", ""), params.get("api_base", "")):
        return None
    timeout = params.get("timeout")
    if timeout is None or timeout <= MAX_CALLER_WAIT_S:
        return None
    return {
        "model_name": name,
        "timeout": timeout,
        "reason": (
            f"'{name}' targets the qwen36/Isis upstream with timeout={timeout}s, "
            f"above the {MAX_CALLER_WAIT_S}s ceiling — real callers cancel at "
            f"40-160s (wq#1588/wq#1623), so anything above that just holds a "
            f"connection open with no caller left waiting on it"
        ),
    }


def run_audit(config: dict) -> list[dict]:
    violations = []
    for entry in config.get("model_list") or []:
        v = audit_deployment(entry)
        if v:
            violations.append(v)
    return violations


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    as_json = "--json" in argv
    if not args:
        print(__doc__)
        return 2
    import yaml  # local import: keep module importable/testable without PyYAML on PATH mismatch
    with open(args[0]) as f:
        config = yaml.safe_load(f)

    violations = run_audit(config)
    if as_json:
        print(json.dumps(violations, indent=2))
    else:
        if not violations:
            print(f"OK: no qwen36/Isis deployment exceeds the {MAX_CALLER_WAIT_S}s ceiling.")
        for v in violations:
            print(f"VIOLATION: {v['reason']}")
        print(f"\n{len(violations)} violation(s).")
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
