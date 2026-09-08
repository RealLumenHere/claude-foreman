# wq#1478 — Liveness Gate Slices 3-4 install

Design: research/LIVENESS_GATE.md §12. One write path: harness/liveness_clear.py.

## Install (Lumen)
    python3 /home/lumen/foreman/ops/wq1478-liveness-gate/apply_wq_patch.py      # .bak'd, idempotent; --check to verify
    sudo install -m0644 /home/lumen/foreman/harness/systemd/lumen-liveness-clear.service /etc/systemd/system/
    sudo install -m0644 /home/lumen/foreman/harness/systemd/lumen-liveness-clear.timer /etc/systemd/system/
    sudo systemctl daemon-reload && sudo systemctl enable --now lumen-liveness-clear.timer
    /home/lumen/bin/wq whoami --register liveness-clear

## Operate
    python3 harness/liveness_clear.py                 # dry-run report (default, zero writes)
    python3 harness/liveness_clear.py --apply         # what the timer runs (+ --write-bindings)
    tail harness/runtime/liveness_audit.jsonl         # every close/flag, probe verdict verbatim
    cat  harness/runtime/liveness_state.json          # last verdict per ticket (TTL cache for the dispatcher hook)

## Rollback (any one of)
    FOREMAN_LIVENESS_CLEAR=0 in the unit (report-only everywhere: timer, dispatcher hook, wq creation gate becomes warn-only)
    sudo systemctl disable --now lumen-liveness-clear.timer
    cp /home/lumen/bin/wq.bak.<ts>_pre_wq1478 /home/lumen/bin/wq
    reopen a wrongly closed ticket: wq edit <id> --status <prior_status from the audit line>
