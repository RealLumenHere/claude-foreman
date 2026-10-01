#!/usr/bin/env python3
"""wq#3301 — verify the 3 dead-rung aq rows (2579/2583/2585) are resolved and
that the live completion probe no longer carries their hosts.

wq#3212 (merged a48711d4) built remove_dead_rungs.py but deferred --apply to
the Mon 2026-10-05 litellm-upgrade window, coupling the config edit to that
container recreate "so there is exactly one recreate". That coupling is not
load-bearing for THIS fix: litellm-completion-probe enumerates hosts by
reading litellm_config.yaml directly (ops/wq3169-litellm-completion-probe/
litellm-completion-probe:49, CONFIG_PATH), not from the running container's
loaded model state. Removing the 3 dead entries from the config file is
therefore sufficient on its own to drop them from the probe's host set and
clear their aq rows -- no container recreate required. wq#3301 ran
`ops/wq3212-litellm-dead-rungs/remove_dead_rungs.py --apply` out-of-band
(aq 2579/2583/2585 had been open 30h+ unassigned, x62, well past a routine
wait-for-the-window case) instead of waiting four more days; the
model_list-consistency reason for riding the window recreate still applies
and is untouched by this script.

Exits 0 only if all three rows are resolved AND none of the three dead hosts
(anthropic, host.docker.internal:8000, host.docker.internal:8091) appear in
the live probe's host set.
"""
import subprocess
import sys

sys.path.insert(0, "/home/lumen/agents/alert-queue")
import aq  # noqa: E402

TARGET_ROWS = {2579: "anthropic",
               2583: "host.docker.internal:8000",
               2585: "host.docker.internal:8091"}


def rows_resolved() -> list:
    c = aq.db()
    bad = []
    for rid, key in TARGET_ROWS.items():
        row = c.execute("SELECT status, key FROM alerts WHERE id=?", (rid,)).fetchone()
        if row is None or row[0] != "resolved" or row[1] != key:
            bad.append((rid, key, row))
    c.close()
    return bad


def dead_hosts_still_probed() -> list:
    out = subprocess.run(["python3", "/home/lumen/bin/litellm-completion-probe", "--print"],
                          capture_output=True, text=True, check=True).stdout
    return [h for h in TARGET_ROWS.values() if h in out]


def main() -> int:
    bad_rows = rows_resolved()
    for rid, key, row in bad_rows:
        print(f"NOT RESOLVED: aq#{rid} key={key} actual={row}")

    still_probed = dead_hosts_still_probed()
    for h in still_probed:
        print(f"STILL IN PROBE SET: {h}")

    if bad_rows or still_probed:
        return 1
    print("wq#3301: aq 2579/2583/2585 resolved; anthropic/:8000/:8091 "
          "absent from the live completion-probe host set.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
