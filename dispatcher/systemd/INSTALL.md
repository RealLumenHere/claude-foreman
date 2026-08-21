# Staged install — lumen-foreman-dispatch (wq#715, Frank's copy-paste)

The build ships these units **repo-only** (writing /etc is outside worker scope).
Nothing below is run by the fleet; DESIGN.md §10 is the contract.

## 1. Install, WITHOUT enabling (staged)

```bash
sudo cp /home/lumen/foreman/dispatcher/systemd/lumen-foreman-dispatch.service /etc/systemd/system/
sudo cp /home/lumen/foreman/dispatcher/systemd/lumen-foreman-dispatch.timer   /etc/systemd/system/
sudo cp /home/lumen/foreman/dispatcher/systemd/lumen-foreman-dispatch.logrotate /etc/logrotate.d/lumen-foreman-dispatch
sudo systemctl daemon-reload
systemctl is-enabled lumen-foreman-dispatch.timer   # expect: disabled
```

## 2. Prove it inert (optional, recommended)

```bash
python3 /home/lumen/foreman/dispatcher/foreman_dispatcher.py run --once --dry-run
tail -5 /home/lumen/foreman/dispatcher.log
```

All lanes ship `enabled: false` in dispatcher/config.json, so even an enabled timer
dispatches nothing until a lane is switched on by config PR (plus a LANE_REPO entry
in orchestrator/router.py — DESIGN.md §12.1: Frank confirms each repo path).

## 3. Enable (Tier-3, Frank only)

```bash
sudo systemctl enable --now lumen-foreman-dispatch.timer
```

## Rollback

```bash
touch /home/lumen/autonomy/DISPATCH_DISARM        # instant, no systemd needed
# or: sudo systemctl disable --now lumen-foreman-dispatch.timer
```
