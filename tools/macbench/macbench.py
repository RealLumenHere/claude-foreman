#!/usr/bin/env python3
"""macbench: unattended model-after-model bench for the Macs (wq#2246).

  macbench.py plan     --node wadjet            dry run: guards + what would run, no node contact
  macbench.py run      --node wadjet            walk the matrix on ONE node, resumable
  macbench.py agentic  --model hathor-llama8b   8-task agentic suite through fworker + gateway
  macbench.py render   --node wadjet            markdown table from the results JSONL

No power actions, never touches a node serving the local-worker group without
--allow-serving-group, refuses below 30 GB free disk, restores what was resident.
"""
import argparse
import os
import sys
import tempfile
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from mb_agentic import guard_model, load_suite, run_suite  # noqa: E402
from mb_matrix import load_matrix  # noqa: E402
from mb_node import MIN_FREE_GB, Node, Refused, load_nodes  # noqa: E402
from mb_render import load, render  # noqa: E402
from mb_runner import Results, Runner, _Skipped, describe, plan_rows  # noqa: E402

STEPS = ("warm", "prefill 512 / 4k / 8k (nonce each)", "same 4k again (prefix reuse)",
         "decode 128 tok", "2 concurrent 2k prompts vs solo", "resident memory + SMC temp/fan + plug watts")


def default_out(node):
    return os.path.join(HERE, "results", f"{node}.jsonl")


def get_node(name, nodes_file=None):
    nodes = load_nodes(nodes_file)
    if name not in nodes:
        raise SystemExit(f"unknown node {name!r}; known: {sorted(nodes)}")
    return Node(name, nodes[name])


def cmd_plan(a):
    node = get_node(a.node, a.nodes)
    matrix = load_matrix(a.matrix)
    results = Results(a.out or default_out(a.node))
    print(f"node {node.name} host {node.host} ssh {node.ssh or 'local'} plug {node.plug or 'none'}")
    try:
        node.guard_identity()
        member = node.guard_serving_group(a.allow_serving_group)
        print("guard: not thor; serving group: " +
              (f"MEMBER {member} (allowed by flag)" if member else "not a member"))
    except Refused as e:
        print(f"REFUSED: {e}")
        return 2
    print(f"guard: will refuse below {MIN_FREE_GB} GB free (checked live at run time)")
    done = results.done()
    for i, (item, note) in enumerate(plan_rows(matrix, results), 1):
        if isinstance(item, _Skipped):
            print(f"{i:2}. {item.id}: SKIP - {item.why}")
        elif not hasattr(item, "engine"):
            print(f"{i:2}. {item.id}: {note}")
        else:
            state = "done (resume skips)" if done.get(item.id) == item.digest() else "todo"
            fetch = (item.fetch or {}).get("kind", "none - model must already be on the node"
                                           if item.engine == "llama-server" else "ollama_pull if missing")
            print(f"{i:2}. {item.id} [{state}] {item.engine} {item.quant} ~{item.size_gb} GB fetch={fetch}")
            print(f"      start: {describe(node, item)}")
    print("each row measures: " + "; ".join(STEPS))
    print("after the last row: unload ours, restore whatever was resident before the run")
    return 0


def cmd_run(a):
    node = get_node(a.node, a.nodes)
    matrix = load_matrix(a.matrix)
    results = Results(a.out or default_out(a.node))
    runner = Runner(node, matrix, results, a.allow_serving_group, set(a.only or []) or None,
                    sampler_interval=a.sample_interval)
    try:
        runner.run()
    except Refused as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2
    print(render(results.records(), node.name))
    return 0


def cmd_agentic(a):
    from harness import fworker
    try:
        guard_model(a.model, a.allow_serving_group)
    except Refused as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2
    results = Results(a.out or default_out(a.node or "agentic"))
    run_id = time.strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
    tags = {"schema": "macbench/1", "run": run_id, "node": a.node, "model": a.model,
            "row": a.row or f"baseline-{a.model}", "row_digest": None,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    root = a.scratch_root or tempfile.mkdtemp(prefix="macbench-agentic-")
    _, summary = run_suite(a.model, a.base_url or fworker.LITELLM_BASE_URL,
                           fworker.read_litellm_key(), results.write, tags, root,
                           claude_bin=a.claude_bin, timeout_s=a.task_timeout,
                           tasks=[t for t in load_suite() if not a.tasks or t["id"] in a.tasks])
    print(f"agentic {a.model}: {summary['successes']}/{summary['tasks']} "
          f"turns_median={summary['turns_median']} wall_total_s={summary['wall_total_s']} "
          f"tool_errors={summary['tool_errors']}")
    return 0


def cmd_render(a):
    text = render(load(a.out or default_out(a.node)), a.node)
    if a.md:
        os.makedirs(os.path.dirname(os.path.abspath(a.md)), exist_ok=True)
        with open(a.md, "w") as fh:
            fh.write(text)
    print(text)
    return 0


def parser():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, fn in (("plan", cmd_plan), ("run", cmd_run), ("render", cmd_render),
                     ("agentic", cmd_agentic)):
        s = sub.add_parser(name)
        s.set_defaults(fn=fn)
        s.add_argument("--node", required=name != "agentic")
        s.add_argument("--out", help="results JSONL (default tools/macbench/results/<node>.jsonl)")
        s.add_argument("--allow-serving-group", action="store_true",
                       help="permit a node/model that serves the local-worker group")
        if name in ("plan", "run"):
            s.add_argument("--matrix", default=os.path.join(HERE, "matrix.yaml"))
            s.add_argument("--nodes", default=None, help="nodes.yaml override")
        if name == "run":
            s.add_argument("--only", nargs="*", help="run just these row ids")
            s.add_argument("--sample-interval", type=float, default=10.0)
        if name == "render":
            s.add_argument("--md", help="also write the table here")
        if name == "agentic":
            s.add_argument("--model", required=True, help="gateway model alias")
            s.add_argument("--row", help="row id label (default baseline-<model>)")
            s.add_argument("--base-url", help="default: the LiteLLM gateway fworker uses")
            s.add_argument("--task-timeout", type=int, default=1200)
            s.add_argument("--tasks", nargs="*", help="run just these task ids (default: all 8)")
            s.add_argument("--scratch-root")
            s.add_argument("--claude-bin", default="/home/lumen/.npm-global/bin/claude")
    return p


def main(argv=None):
    a = parser().parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
