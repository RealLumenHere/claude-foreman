# wq#2472 vlogs-forwarder on the rest of the fleet

The wq#2051-fixed journald -> Bes VictoriaLogs forwarder ran only on Lumen and Bes, so VL
was not a fleet postmortem backstop. Installed 2026-09-26 on anubis, hathor, isis, maat,
sobek, thoth, khepri.

forward-journal.py is the live /home/lumen/services/vlogs-forwarder/forward-journal.sh,
byte-identical (the earlier a4 branch copy had an edited header and was not landed).

Differences from the Lumen/Bes unit: other nodes have no `lumen` user (the a4 installer
did `install -o lumen` and would have failed on every target), so the unit runs as
DynamicUser with SupplementaryGroups=systemd-journal and StateDirectory for the cursor.
Script lives in /opt/vlogs-forwarder. install.sh refuses on lumen/bes.

Install: copy this dir to the node, `sudo bash install.sh`. First tick ships the last hour.

Verify: coverage.sh "<since>" "<until>" compares journal line count (root, all journals)
with the VL count for that host over a closed window. Run 2026-09-26 01:02-01:04 UTC:
all 7 new nodes plus bes (control) exact, e.g. hathor 336=336, khepri 325=325, bes 583=583.

Not installed: thor (Frank's workstation, not touched), sia (sudo -n needs a password),
apep/gameboy/nephthys/seshat/wadjet (unreachable 2026-09-26). Same install.sh when they
are back.

Rollback per node: systemctl disable --now vlogs-forwarder.timer; rm the two units and
/opt/vlogs-forwarder; daemon-reload.
