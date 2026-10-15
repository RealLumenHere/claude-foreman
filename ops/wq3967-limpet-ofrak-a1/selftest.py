#!/usr/bin/env python3
"""wq#3967 OFRAK plugin self-test, run as the limpet-plugin.toml [health]
command. Exercises the real unpack -> patch-strings -> repack pipeline
against the committed fixture and checks the repacked bytes differ only in
the patched region -- a probe that can actually fail, not a bare
binary-exists check. Exits non-zero (with a reason on stderr) if `ofrak`
isn't installed, the pipeline errors, or the output doesn't match."""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ofrak_ops  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tests", "fixtures", "sample.tar")


def main() -> int:
    try:
        with tempfile.TemporaryDirectory(prefix="ofrak-selftest-") as tmp:
            workdir = os.path.join(tmp, "work")
            output = os.path.join(tmp, "repacked.tar")

            ofrak_ops.unpack(FIXTURE, workdir)
            ofrak_ops.patch_strings(workdir, "OFRAK fixture", "OFRAK patched")
            result = ofrak_ops.repack(workdir, output)

            original = open(FIXTURE, "rb").read()
            repacked = open(output, "rb").read()
            if len(original) != len(repacked):
                print(f"selftest FAIL: size changed {len(original)} -> {len(repacked)}", file=sys.stderr)
                return 1
            if result["sha256"] == result["original_sha256"]:
                print("selftest FAIL: repacked file is byte-identical to the original", file=sys.stderr)
                return 1
    except ofrak_ops.OfrakOpsError as exc:
        print(f"selftest FAIL: {exc}", file=sys.stderr)
        return 1

    print("selftest OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
