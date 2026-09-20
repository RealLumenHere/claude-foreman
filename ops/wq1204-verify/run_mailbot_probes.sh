#!/usr/bin/env bash
# needs_frank classifier probes live in mailbot and import the production module
# (src/leakfilter.js); nothing here re-implements a mailbot regex.
set -u
MAILBOT=${MAILBOT:-/home/lumen/mailbot}
rc=0
node "$MAILBOT/ops/wq2147-verify/probe.mjs" || rc=1
node "$MAILBOT/ops/wq2164-verify/probe_pronoun_overfire.mjs" || rc=1
exit "$rc"
