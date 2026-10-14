#!/usr/bin/env python3
"""subprocess boundary for sigrok-cli invocations (wq#3883).

Kept separate from sigrok_cli.py so argv-building/output-parsing stays unit
testable without a real binary or hardware attached. Always argv lists,
never shell=True -- manifest/tool args can't break out of their argument
position (house rule, matches cli_wrapper.py in wq#3808).
"""

from __future__ import annotations

import shutil
import subprocess


class SigrokRunError(RuntimeError):
    def __init__(self, argv: list, returncode: int, stderr: str):
        self.argv = argv
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(f"{' '.join(argv)} exited {returncode}: {stderr.strip()[:500]}")


def run(argv: list, timeout: int = 30) -> subprocess.CompletedProcess:
    if shutil.which(argv[0]) is None:
        raise SigrokRunError(argv, -1, f"{argv[0]} not found on PATH")
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, shell=False)
    if proc.returncode != 0:
        raise SigrokRunError(argv, proc.returncode, proc.stderr)
    return proc
