"""Markdown table from a results JSONL: one line per matrix row, latest completed run wins."""
import json

COLS = ("row", "engine", "quant", "prefill 512", "prefill 4k", "prefill 8k", "4k reuse",
        "decode", "2x slowdown", "RSS GB", "pkg C max", "fan max", "W mean", "agentic")


def _f(v, nd=1):
    return "-" if v is None else (f"{v:.{nd}f}" if isinstance(v, float) else str(v))


def load(path):
    out = []
    with open(path) as fh:
        for ln in fh:
            try:
                out.append(json.loads(ln))
            except ValueError:
                continue
    return out


def collect(records):
    """{row: {phase: record}} taking each row's most recent run that reached row_done."""
    done_run = {}
    for r in records:
        if r.get("phase") == "row_done":
            done_run[r["row"]] = r["run"]
    rows = {}
    for r in records:
        rid = r.get("row")
        if rid is None:
            continue
        if r.get("phase") == "row_skipped" or r.get("phase") == "row_error":
            rows.setdefault(rid, {})[r["phase"]] = r
        elif done_run.get(rid) == r.get("run") or r.get("phase", "").startswith("agentic"):
            rows.setdefault(rid, {})[r["phase"]] = r
    return rows


def _cell(phases, name, key):
    r = phases.get(name)
    return r.get(key) if r else None


def agentic_detail(records):
    """Per-task table for each row's most recent agentic run."""
    last = {}
    for r in records:
        if r.get("phase") == "agentic_summary":
            last[r["row"]] = r["run"]
    lines = []
    for row, run in last.items():
        lines += [f"#### agentic tasks - {row}", "",
                  "| task | kind | pass | turns | tool calls | tool errors | wall s | diff lines |",
                  "|---|---|---|---|---|---|---|---|"]
        for r in records:
            if r.get("phase") == "agentic_task" and r["row"] == row and r["run"] == run:
                lines.append(f"| {r['task']} | {r['kind']} | {'yes' if r['success'] else 'no'} | "
                             f"{r['turns']} | {r['tool_calls']} | {r['tool_errors']} | "
                             f"{r['wall_s']:.0f} | {r['diff_lines']} |")
        lines.append("")
    return lines


def render(records, node=None):
    rows = collect(records)
    lines = [f"### macbench results{f' - {node}' if node else ''}", "",
             "| " + " | ".join(COLS) + " |", "|" + "---|" * len(COLS)]
    notes = []
    for rid, ph in rows.items():
        if "row_skipped" in ph or "row_error" in ph:
            why = (ph.get("row_skipped") or ph["row_error"])
            notes.append(f"- `{rid}`: {why.get('reason') or why.get('error')}")
            if "decode" not in ph:
                continue
        any_rec = next(iter(ph.values()))
        temps = [r.get("pkg_c_max") for r in ph.values() if r.get("pkg_c_max") is not None]
        fans = [r.get("fan_rpm_max") for r in ph.values() if r.get("fan_rpm_max") is not None]
        watts = [r.get("watts_mean") for r in ph.values() if r.get("watts_mean") is not None]
        rss = max((r.get("resident_bytes") or 0 for r in ph.values()), default=0)
        reuse = _cell(ph, "prefix_reuse_4096", "reuse_frac")
        ag = ph.get("agentic_summary")
        cells = [rid, any_rec.get("engine", "-"), any_rec.get("quant") or "-",
                 _f(_cell(ph, "prefill_512", "prompt_tps")),
                 _f(_cell(ph, "prefill_4096", "prompt_tps")),
                 _f(_cell(ph, "prefill_8192", "prompt_tps")),
                 "-" if reuse is None else f"{reuse * 100:.0f}%",
                 _f(_cell(ph, "decode", "decode_tps")),
                 _f(_cell(ph, "concurrent_x2", "slowdown"), 2),
                 _f(rss / 1e9 if rss else None), _f(max(temps) if temps else None, 0),
                 _f(max(fans) if fans else None, 0), _f(max(watts) if watts else None),
                 f"{ag['successes']}/{ag['tasks']}" if ag else "-"]
        lines.append("| " + " | ".join(cells) + " |")
    lines += ["", "prefill and decode are tok/s from server-side counters; 2x slowdown is "
              "pair wall time over solo wall time for a 2048-token prompt (about 2 means "
              "prefill is time-sliced, about 1 means parallel).", ""]
    lines += agentic_detail(records)
    return "\n".join(lines + (["Not measured:"] + notes + [""] if notes else []))
