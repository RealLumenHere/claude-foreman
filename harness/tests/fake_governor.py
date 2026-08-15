#!/usr/bin/env python3
"""Stub governor CLI for fworker integration tests — always allows dispatch, so the
test exercises fworker's own logic without depending on live governor state.json /
STOP file. Mirrors the two subcommands fworker.py actually calls.
"""
import sys

if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "can-dispatch":
        print('{"allow": true, "local_only": false, "reasons": []}')
        sys.exit(0)
    if len(sys.argv) >= 2 and sys.argv[1] == "report-event":
        sys.exit(0)
    sys.exit(1)
