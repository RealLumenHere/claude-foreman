#!/usr/bin/env bash
# wq#4416 qemu-mcp: no deploy step exists yet. server.py is not wired into any
# systemd unit, HAL role, or caller on the fleet — same "new component, not yet
# registered anywhere" state ops/wq3881-openocd-mcp/install.sh documents for
# itself, and this bundle isn't even listed in ops/wq3808-limpet-mcp-meta-server-a1's
# manifest.example.json alongside the ghidra/radare2/openocd placeholder entries.
# There is nothing installed to byte-match or any unit to check is-active.
#
# The only live signal available right now is that the bundle's own code
# actually behaves as shipped: a bare "does this file exist in git" check would
# be exactly the repo-only check ops/VERIFY_CONTRACT.md (wq#1696) rules out —
# it cannot fail from anything that actually breaks the client. Running its
# test suite can: tests/test_qemu_client.py spawns tests/fake_qemu.py as a real
# subprocess speaking the real QMP wire protocol over a real AF_UNIX socket, so
# boot()/quit(), screendump/read_serial/wait_serial/send-key framing, and the
# invalid-input rejections (missing ISO, bad memory/smp, not-yet-booted, double
# boot, unmapped keystroke, negative timeout) all show up as a real failure
# here if the client regresses. server.py's FastMCP wrapper itself stays
# unexercised by this check: the `mcp` package isn't installed in this sandbox
# (no pip/pip3 entry in harness/worker-settings.json, same constraint
# ops/wq1151-lumen-kb/requirements.txt documents for its own server.py, logged
# at kb/ISSUE-LOG.md row 1309) — but it is a one-line-per-tool pass-through
# with no logic of its own to diverge from qemu_client.py's tested behavior.
#
# Running an actual qemu-system-x86_64 boot (the real ISO-install workflow
# this server exists for) is a separate concern tracked against the pipeline
# ticket, wq#4417 (projects/limpet/docs/iso-test-pipeline.md), which already
# logged the KVM-node/--add-dir allowlist gap a real boot run hits
# (kb/ISSUE-LOG.md row 1393). This bundle's own verification only needs the
# fake QMP fixture, not a real qemu binary or a KVM-capable host.
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"

if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  if ! python3 -I -m pytest "$SRC/tests" -q >/dev/null 2>&1; then
    _vmiss "primary-tree test suite fails: ops/wq4416-qemu-mcp/tests"
  fi
  vdone
fi

echo "wq4416-qemu-mcp has no install step yet: server.py is not wired into any" >&2
echo "systemd unit, HAL role, or caller on the fleet. Registering it as a real" >&2
echo "child server (e.g. in wq3808's meta-server manifest) is a separate change." >&2
echo "verify: bash $SRC/install.sh --verify" >&2
echo "tests:  python3 -I -m pytest $SRC/tests -q" >&2
exit 0
