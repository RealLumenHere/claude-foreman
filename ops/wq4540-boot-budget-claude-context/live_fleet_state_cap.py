#!/usr/bin/env python3
"""live_fleet_state_cap.py (wq#4540).

MINERVA_STALENESS_REVIEW.md §2.7: `projection_state.py check-warn` already measures
LIVE_FLEET_STATE.md against a 4000-tok soft cap and prints
"LIVE_FLEET_STATE=5734 tok exceeds soft cap 4000 ... (not truncated here)" — then the
selector passes the whole block through anyway. A warning nobody acts on is not a cap.

This enforces it: whole-line budget cut, same contract as
harness/instincts_render.py's tier0_block() (never cut mid-line, state what was
dropped instead of silently truncating). Caller contract: run this AFTER
`check-warn`/`state-regen --if-dirty` and BEFORE `cat`-ing the file into the boot
context, on the file claude_context.sh is about to inject — not on the store.

Exit: 0 untouched (already within budget), 1 truncated (cap enforced), 2 tool error.
"""
import argparse
import sys

CHARS_PER_TOKEN = 4  # research/TOKEN_BASELINE.md house estimate, same as instincts_render.py
DEFAULT_BUDGET = 4000


def est_tokens(text):
    return len(text) // CHARS_PER_TOKEN


def cap(text, budget):
    """Whole-line cut. Returns (kept_text, n_dropped_lines, dropped_tokens_estimate)."""
    if est_tokens(text) <= budget:
        return text, 0, 0
    lines = text.splitlines(keepends=True)
    kept, used = [], 0
    for i, line in enumerate(lines):
        cost = est_tokens(line)
        if used + cost > budget:
            dropped = lines[i:]
            dropped_tok = est_tokens("".join(dropped))
            footer = (
                "\n<!-- TRUNCATED by live_fleet_state_cap.py (wq#4540): "
                "%d line(s) / ~%d tok dropped past the %d-tok cap; read the full "
                "file on disk for the rest. -->\n" % (len(dropped), dropped_tok, budget)
            )
            return "".join(kept) + footer, len(dropped), dropped_tok
        kept.append(line)
        used += cost
    return text, 0, 0  # unreachable: the sum of all lines was already <= budget


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--file", required=True, help="file to cap in place")
    ap.add_argument("--budget-tokens", type=int, default=DEFAULT_BUDGET)
    args = ap.parse_args(argv)

    try:
        with open(args.file, encoding="utf-8") as f:
            text = f.read()
    except OSError as exc:
        print(f"live_fleet_state_cap.py: {exc}", file=sys.stderr)
        return 2

    kept, n_dropped, dropped_tok = cap(text, args.budget_tokens)
    if n_dropped == 0:
        return 0

    with open(args.file, "w", encoding="utf-8") as f:
        f.write(kept)
    print(
        f"live_fleet_state_cap.py: {args.file} was ~{est_tokens(text)} tok, "
        f"capped at {args.budget_tokens} tok ({n_dropped} line(s) / ~{dropped_tok} tok dropped)",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
