#!/usr/bin/env bash
# wq#1630 deploy — run from a session with write access to /home/lumen/minerva,
# /home/lumen/bin (lumen-bin repo), /home/lumen/kb and systemd. The BUILD worker's guard
# (wq#1719/wq#900) makes those primary trees read-only, so this could not be applied from
# the ticket's session. Idempotent: every step is safe to re-run.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
HANDOFF=/home/lumen/HANDOFF.md
OLD_KB_FILE=/home/lumen/kb/SESSION-HANDOFF.md

# wq#1696 deploy-verify contract (ops/_verify.sh). Read-only; MUST stay before any install step: a
# script that did not answer --verify would run the whole install (it did, on 2026-09-28).
# session-handoff is also shipped by wq1294 (older), so it is matched by the marker this bundle
# added, not byte-identity.
if [ "${1:-}" = --verify ]; then
    . "$HERE/../_verify.sh"
    vcontains /home/lumen/minerva/context/claude_context.sh 'wq#1630'
    vcontains /home/lumen/bin/session-handoff 'session-handoff:begin'
    vunit_file "$HERE/../wq1294-session-handoff/session-handoff.service" /etc/systemd/system/session-handoff.service
    vcontains "$HANDOFF" '^<!-- session-handoff:begin'
    vcontains "$HANDOFF" '^### In-flight workers'
    [ ! -e "$OLD_KB_FILE" ] || _vmiss "second handoff file exists: $OLD_KB_FILE"
    vdone
fi

python3 -m pytest -q "$HERE/test_session_handoff.py"

# 1. session-handoff (lumen-bin repo primary tree): renders into HANDOFF.md, no kb file, no git commit.
install -m 0755 "$HERE/session-handoff" /home/lumen/bin/session-handoff

# 2. claude_context.sh --core hook (marker line is `wq#1630`); minerva/* is not a git repo.
if ! grep -q 'wq#1630' /home/lumen/minerva/context/claude_context.sh; then
    patch -p1 -d /home/lumen < "$HERE/claude_context_render_handoff.diff"
else
    echo "claude_context.sh: wq#1630 hook already present, skipping patch"
fi

# 3. the cosmetic unit Description now names the real target.
sudo install -m 0644 /home/lumen/foreman/ops/wq1294-session-handoff/session-handoff.service \
    /etc/systemd/system/session-handoff.service
sudo systemctl daemon-reload

# 4. one clean cycle: render for real, then prove the region landed with the in-flight list
#    and the hand prose below it is still there.
python3 /home/lumen/bin/session-handoff --render
grep -q '^<!-- session-handoff:begin' "$HANDOFF"
grep -q '^<!-- session-handoff:end -->' "$HANDOFF"
grep -q '^### In-flight workers' "$HANDOFF"
grep -q 'VERBATIM CONSTRAINTS' "$HANDOFF"

# 5. only now retire the second handoff file and its git-commit step (the script no longer
#    writes it). The notes log stays where it is but stops being committed.
if [ -e "$OLD_KB_FILE" ] || git -C /home/lumen/kb ls-files --error-unmatch SESSION-HANDOFF.md >/dev/null 2>&1; then
    git -C /home/lumen/kb rm -q --ignore-unmatch -f SESSION-HANDOFF.md
    git -C /home/lumen/kb commit -q -m "kb: retire SESSION-HANDOFF.md — merged into HANDOFF.md generated region (wq#1630)" \
        -- SESSION-HANDOFF.md || true
fi
if git -C /home/lumen/kb ls-files --error-unmatch .session-handoff-notes.log >/dev/null 2>&1; then
    git -C /home/lumen/kb rm -q --cached .session-handoff-notes.log
    echo '.session-handoff-notes.log' >> /home/lumen/kb/.git/info/exclude
    git -C /home/lumen/kb commit -q -m "kb: untrack session-handoff notes log (no commit step, wq#1630)" \
        -- .session-handoff-notes.log || true
fi
test ! -e "$OLD_KB_FILE"

# 6. acceptance: a fresh --core boot shows the in-flight worker list (needs a registered wq
#    identity, wq#1127); no second handoff file exists.
if /home/lumen/minerva/context/claude_context.sh --core 2>/dev/null | grep -q '^### In-flight workers'; then
    echo "OK: --core boot shows the in-flight worker list"
else
    echo "WARN: --core boot did not show the in-flight list (unregistered wq identity?)" >&2
fi
ls /home/lumen/kb/SESSION-HANDOFF.md 2>/dev/null && { echo "FAIL: second handoff file exists" >&2; exit 1; } || true
