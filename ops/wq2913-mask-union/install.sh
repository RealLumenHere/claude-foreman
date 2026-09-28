#!/usr/bin/env bash
# wq#2913 union-of-spans masking — deploy-verify handler (wq#2967, ops/VERIFY_CONTRACT.md).
#
# This bundle ships no files of its own to install (harness/masking.py IS the primary
# tree — nothing to copy there beyond the normal repo checkout). What it ADDS is a
# rule-set rewrite with two live mirrors, and --verify's job is to say whether both
# have actually caught up with what harness/masking.py at HEAD now reads:
#
#   - ops/wq2316-mask-secrets-boundary/mask-secrets, installed to
#     /home/lumen/bin/mask-secrets. That bundle already carries its own --verify
#     (vfile against the same DST); this one re-checks the identical target so a
#     bundle-level confirm of wq#2913 does not need to know wq2316's bundle name to
#     see whether the wrapper actually reached /home/lumen/bin — "a repo-only check
#     does not count" (ops/VERIFY_CONTRACT.md, wq#1696) applies to THIS bundle too,
#     and harness/masking.py alone is repo-only.
#   - token-engine/token_engine_daemon.py, installed by token-engine/install.sh to
#     /usr/local/lib/token-engine/token_engine_daemon.py, root-owned (systemctl cat
#     token-engine.service) so a lumen-timer-safe read may see it as unreadable
#     rather than absent-or-differing — that shows up as a MISS either way, which is
#     the honest answer ("this check could not confirm the mirror is live"), not a
#     false pass. A root-privileged byte diff already exists at
#     ops/wq2333-token-engine-redeploy/probe_deployed.sh
#     (`lumen-run start --root -- bash ... `) for the case this handler cannot reach.
#     vactive below adds a read-only liveness signal (the daemon is up) that needs no
#     elevated read.
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"

if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  vfile "$SRC/../wq2316-mask-secrets-boundary/mask-secrets" /home/lumen/bin/mask-secrets
  vfile "$SRC/../../token-engine/token_engine_daemon.py" \
        /usr/local/lib/token-engine/token_engine_daemon.py
  vactive token-engine.service
  vdone
fi

echo "ops/wq2913-mask-union ships no installable files of its own."
echo "  bin/mask-secrets:  bash $SRC/../wq2316-mask-secrets-boundary/install.sh"
echo "  token-engine daemon: token-engine/install.sh (see its own README)"
