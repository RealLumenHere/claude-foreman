#!/usr/bin/env python3
"""wq#3943 selftest — the [health] check for limpet-plugin.toml's
component-id manifest (limpet/examples/component-id.toml).

Exercises the pipeline wiring (flash-marking resolve, honest-miss resolve,
vision-stage degrade path) without needing a camera, a pulled VLM, or a
Part-DB instance — those are separate TOOL_REGISTRY adoptions
(design/wq3900_limpet_vision_component_id.md §7), not something a health
probe can assume is provisioned. This is a probe that can actually fail
(fleet acceptance-probe doctrine, wq#2630): it is not a bare "import
succeeded".

Exit 0 = pipeline wiring correct. Exit 1 = a wiring/contract regression.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import component_lookup_ops  # noqa: E402
import vision_ops  # noqa: E402


def main() -> int:
    hit = component_lookup_ops.lookup_part("W25Q64JVSIQ")
    if not hit["resolved"] or hit["resolved"]["manufacturer"] != "Winbond":
        print("FAIL: flash-marking resolver did not resolve a known Winbond marking", file=sys.stderr)
        return 1

    miss = component_lookup_ops.lookup_part("totally-unresolvable-marking")
    if miss["resolved"] is not None:
        print("FAIL: resolver fabricated an identity for an unresolvable marking", file=sys.stderr)
        return 1

    read = vision_ops.identify_components("/nonexistent/limpet-selftest-fixture.jpg")
    if read["ok"] is not False or not read["error"]:
        print("FAIL: vision read did not degrade cleanly on a missing fixture", file=sys.stderr)
        return 1

    print("OK: component-id pipeline wiring self-test passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
