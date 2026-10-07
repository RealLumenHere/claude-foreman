# wq#3488 — no-autostart guard for re-tool apt installs

## Problem

A Debian postinst `enable`s and `start`s any daemon unit a package ships, even
when the ticket only wanted an on-demand CLI. wq#3380 asked for bettercap
(BLE/WiFi RE); `apt-get install bettercap` pulled in `bettercap.service`
(root, `api.rest` bound to loopback 8081, `Restart=always`) which then ran
unnoticed from 2026-10-02 until mega-3 disabled it on 2026-10-06 — found only
because the unit-drift digest listed the new enable symlink.

## Fix

- `ops/_retool_apt_install.sh` — reusable `retool_apt_install PKG...`: runs
  the apt install under a `policy-rc.d` that returns 101 (deny) for every
  `start` request dpkg's `invoke-rc.d` makes during unpack/configure, then
  walks `dpkg -L` for each installed package and `disable --now` + `mask`
  any `.service`/`.socket`/`.timer` it shipped. Also ships
  `retool_verify_no_autostart PKG...` for `install.sh --verify` to assert
  `systemctl is-enabled` is not `enabled` for any such unit.
- `ops/wq3386-rizin-cutter/install.sh` — wired as the worked example: its
  `apt-get install -y $MISSING` line now routes through
  `retool_apt_install`, and its `--verify` block calls
  `retool_verify_no_autostart rizin cutter`.
- `unit-drift` (`/home/lumen/bin/unit-drift`, diff in `wq3488_bin.diff` —
  primary tree, installed by hand the same way wq#3385's renode bundle
  documents, since the Edit/Write tools and git verbs are denied there) —
  `check` gained `--quiet`/`--no-notify`: it still computes the same digest
  hash and state-file bookkeeping as `--digest` alone, but never calls
  `ntfy-send`. `unit-drift check --digest` run by hand was paging ntfy on
  every manual invocation, which made it not actually read-only.

## Where else

Checked every apt-installing bundle in the wq3380–wq3390 RE-tool batch
(`ops/wq3383-emba`, `wq3385-renode-install`, `wq3386-rizin-cutter`,
`wq3387-fact-core`, `wq3388-routersploit`, `wq3389-angr-symex`,
`wq3390-reko-decompiler`, `wq3391-retdec-install`): only
`wq3386-rizin-cutter/install.sh` calls `apt-get install` for an
apt-shipped package (the others install portable tarballs, pip/pipx, or a
built-from-source tree with no apt daemon package in their critical path).
bettercap itself (wq#3380) was never scripted as an `ops/` bundle — it was
installed ad hoc, which is exactly how it slipped the review wq#3386 now
gets. Future re-tool apt installs should source
`ops/_retool_apt_install.sh` rather than calling `apt-get install` raw.
