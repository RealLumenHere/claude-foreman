# wq#2713 LoadState lookup failure != retired

wq#2713 asked to delete redis-server from the healer watch table. redis-server is live
(loaded, active since 2026-08-31, 127.0.0.1:6379). The ticket came from the wq#1112
stale-target guard, whose `_unit_load_state` returned `not-found` on timeout, exception,
nonzero rc or empty stdout. A transient systemctl failure filed a delete-row ticket
against a live unit, and a worker opened PR#1083 to do it (closed unmerged).

Fix: lookup failure returns `unknown`; only a successful systemctl read can say
not-found/masked. The healer also needs STALE_CONFIRM=3 consecutive confirmed reads
before filing, and resets the streak on any loaded/unknown read. Same class fixed in
agents/split-watch/split_watch.py `_load_state`, where a failed lookup would have
triggered the destructive queue drain + permanent retire.

Deployed: /home/lumen/agent/lumen_self_healer.py, /home/lumen/agents/split-watch/split_watch.py
(backups .bak.20260926_003822). Test: python3 test_wq2713_loadstate.py [healer.py] [split_watch.py]
-> 15/15 on patched; originals in /home/lumen/backups/wq2748_wq2713_orig fail 7.
