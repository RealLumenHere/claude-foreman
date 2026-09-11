# wq#1659 — deploy: dangling After=litellm/lumen-pgvector + unit-drift lint

Source: `research/FLEET_SERVICE_ATLAS.md` #13 (wq#1627).

## Why this ships as diffs, not a live edit

The 5 affected unit files live under `/etc/systemd/system`; the unit-drift
baseline repo lives at `/home/lumen/fleet-units`. Neither path is inside this
worker's sandboxed worktree/`--add-dir` set, so this ticket could not read the
live files to confirm exact line numbers, nor write them directly (the
sandbox refuses paths outside the allowed roots — this is the correct,
intended behavior, not a bug to route around). Everything here is staged for
a session with access to those paths (or Frank) to apply.

## 1. Deploy the port-wait helper

    install -m 0755 wait-for-port /home/lumen/bin/wait-for-port

## 2. Edit the 5 unit files per units.diff

For each of:
- `/etc/systemd/system/lumen-fleet-cerebras.service`
- `/etc/systemd/system/lumen-fleet-mistral.service`
- `/etc/systemd/system/lumen-fleet-openrouter.service`
- `/etc/systemd/system/lumen-convo-learn.service`
- `/etc/systemd/system/lumen-research-index.service`

Apply the corresponding hunk in `units.diff` (drop the dangling
`After=`/`Wants=` line, add `After=docker.service` + `Requires=docker.service`
if not already present, add the `ExecStartPre=` port wait in `[Service]`).
`systemctl cat <unit>` first to confirm current content before editing, since
this ticket could not verify it directly.

## 3. Reload and verify

    systemctl daemon-reload
    systemctl cat lumen-fleet-cerebras lumen-fleet-mistral lumen-fleet-openrouter \
        lumen-convo-learn lumen-research-index | grep -E '^(After|Wants|Requires|ExecStartPre)='
    systemd-analyze verify /etc/systemd/system/lumen-fleet-cerebras.service   # repeat per unit

None of the 5 units should list `litellm.service` or `lumen-pgvector.service`
anywhere in the output above.

## 4. Deploy the extended unit-drift lint

    install -m 0755 staged/unit-drift /home/lumen/bin/unit-drift
    unit-drift snapshot   # re-baseline: unit content changed in step 2
    unit-drift check      # expect CLEAN, 0 dangling target(s)

`unit-drift check` now fails (exit 1) if *any* unit's `After=`/`Wants=`/
`Requires=`/`BindsTo=`/`PartOf=` names a target with no loadable unit file
anywhere in `SEARCH_PATHS` — this is the general lint the ticket asks for, not
just a point fix for these 5 units. Run `python3 -m pytest
tests/test_dangling_target_lint.py` first (or the repo's test runner) to
confirm the lint logic before installing.

## Rollback

`unit-drift show-baseline` / the pre-step-2 git commit in
`/home/lumen/fleet-units` holds the prior unit content if the edit needs
reverting; `/home/lumen/bin/unit-drift` itself has no destructive path (see
its own `apply` — read-only, dry-run only).
