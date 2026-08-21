# Staged install — lumen-foreman-dispatch (wq#715) + lumen-foreman-selfheal (wq#725)

The build ships these units **repo-only** (writing /etc is outside worker scope).
Nothing below is run by the fleet; DESIGN.md §10 (dispatcher) and self_heal.py's module
docstring (wq#725) are the contract.

## 1. Install, WITHOUT enabling (staged)

```bash
sudo cp /home/lumen/foreman/dispatcher/systemd/lumen-foreman-dispatch.service /etc/systemd/system/
sudo cp /home/lumen/foreman/dispatcher/systemd/lumen-foreman-dispatch.timer   /etc/systemd/system/
sudo cp /home/lumen/foreman/dispatcher/systemd/lumen-foreman-dispatch.logrotate /etc/logrotate.d/lumen-foreman-dispatch
sudo cp /home/lumen/foreman/dispatcher/systemd/lumen-foreman-selfheal.service /etc/systemd/system/
sudo cp /home/lumen/foreman/dispatcher/systemd/lumen-foreman-selfheal.timer   /etc/systemd/system/
sudo cp /home/lumen/foreman/dispatcher/systemd/lumen-foreman-selfheal.logrotate /etc/logrotate.d/lumen-foreman-selfheal
sudo systemctl daemon-reload
systemctl is-enabled lumen-foreman-dispatch.timer   # expect: disabled
systemctl is-enabled lumen-foreman-selfheal.timer   # expect: disabled
```

## 2. Prove it inert (optional, recommended)

```bash
python3 /home/lumen/foreman/dispatcher/foreman_dispatcher.py run --once --dry-run
tail -5 /home/lumen/foreman/dispatcher.log

python3 /home/lumen/foreman/dispatcher/self_heal.py run --once --dry-run
tail -5 /home/lumen/foreman/self_heal.log
```

All lanes ship `enabled: false` in dispatcher/config.json, so even an enabled dispatch
timer dispatches nothing until a lane is switched on by config PR (plus a LANE_REPO
entry in orchestrator/router.py — DESIGN.md §12.1: Frank confirms each repo path).
self-heal has no lane gate of its own — it only ever acts on tickets already blocked
with an `INFRA_CRASH:` reason (harness/fworker.py's classify_block_reason()), so it is
safe to enable independently of which lanes the dispatcher has turned on.

## 3. Enable (Tier-3, Frank only)

```bash
sudo systemctl enable --now lumen-foreman-dispatch.timer
sudo systemctl enable --now lumen-foreman-selfheal.timer
```

## Rollback

```bash
touch /home/lumen/autonomy/DISPATCH_DISARM        # instant, no systemd needed — both
                                                   # the dispatcher and self-heal check it
# or: sudo systemctl disable --now lumen-foreman-dispatch.timer
# or: sudo systemctl disable --now lumen-foreman-selfheal.timer
```
