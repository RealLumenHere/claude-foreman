"""fleet_node.py — `fleet-node` entry point (research/INFERENCE_NODE_REGISTRY.md §3, §5).

  python3 harness/fleet_node.py lint   [--path REGISTRY]                           (wq#2248)
  python3 harness/fleet_node.py render [--check | --apply] [--path REGISTRY] [--root REPO]  (wq#2250)

`render --check` exits 2 when a generated block has drifted from the registry: that is the CI gate.
`add` / `retire` arrive with wq#2253.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import inference_registry  # noqa: E402
import node_render  # noqa: E402

COMMANDS = {"lint": inference_registry.main, "render": node_render.main}


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] not in COMMANDS:
        print(__doc__, file=sys.stderr)
        return 1
    return COMMANDS[args[0]](args)


if __name__ == "__main__":
    sys.exit(main())
