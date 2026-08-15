#!/usr/bin/env python3
"""Stub governor CLI for fworker integration tests — by default always allows
dispatch, so most tests exercise fworker's own logic without depending on live
governor state.json / STOP file. Mirrors the two subcommands fworker.py
actually calls.

Deny behavior is controlled by env vars, for the wq#639 claim-vs-gate-ordering
tests (fworker calls can-dispatch once pre-claim in cmd_run, then again
per-attempt in do_attempt — a test needs to flip allow->deny across those
two calls to exercise the post-claim release path):
  FAKE_GOVERNOR_DENY=always      deny every call
  FAKE_GOVERNOR_DENY=after:<N>   allow the first N calls, deny call N+1 on
  (unset / anything else)        always allow (default)
FAKE_GOVERNOR_COUNTER_FILE persists the call count across the separate process
invocations `after:N` needs; required only when using that mode.
"""
import os
import sys


def _call_n(counter_file):
    n = 1
    if counter_file and os.path.exists(counter_file):
        n = int(open(counter_file).read().strip()) + 1
    if counter_file:
        with open(counter_file, "w") as f:
            f.write(str(n))
    return n


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "can-dispatch":
        mode = os.environ.get("FAKE_GOVERNOR_DENY", "")
        deny = mode == "always"
        if mode.startswith("after:"):
            deny = _call_n(os.environ.get("FAKE_GOVERNOR_COUNTER_FILE")) > int(mode.split(":", 1)[1])
        if deny:
            print('{"allow": false, "local_only": true, "reasons": ["synthetic test deny"]}')
            sys.exit(1)
        print('{"allow": true, "local_only": false, "reasons": []}')
        sys.exit(0)
    if len(sys.argv) >= 2 and sys.argv[1] == "report-event":
        sys.exit(0)
    sys.exit(1)
