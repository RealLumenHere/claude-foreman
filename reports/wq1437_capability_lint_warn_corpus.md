# wq#1437 — capability_lint (warn mode) tuned against the live corpus

Slice B of research/BROKER_WIRING.md (§2.1, §5). Ships `harness/capability_lint.py`
and dispatcher stage 3.7 in WARN mode: one `capability_lint` event per flagged
candidate, `applied=False`, nothing written to any ticket.

## Reproduce

```
python3 harness/capability_lint.py            # open+blocked, human-readable
python3 harness/capability_lint.py --json     # one object per flagged ticket
python3 harness/capability_lint.py --ticket 1133
```

Read-only (`file:...?mode=ro`), no queue writes, no spawns. The node roster comes from
`lumen_state.py current "" tailscale_ip|lan_ip`; an unreachable state store yields an
empty roster and every node mention becomes `unresolved` (fail closed, same as
`fleet-exec:60`).

## Numbers (2026-09-06, 111 open+blocked tickets, repo reach = none, the worst case)

| bucket | tickets |
|---|---|
| any derivation at all | 65 |
| `dir` tags | 50 |
| action tags (`ssh`/`restart`/`copy`) | 14 |
| `unresolved` | 23 |
| `refused` | 6 |

The dispatcher passes each ticket's real `repo` + archetype `add_dirs`, so the live
`dir` count per pass is lower than 50 — a path already inside the worker's reach
produces no tag.

`refused` (the whole point of the deny-list): wq#983 (`/home/lumen` itself), wq#1008
(`/home/lumen/secrets/reddit.env`), wq#1133 (`.secrets/github-frank.token`), wq#1270
and wq#1358 (dead worktrees under `/home/lumen/worktrees`), wq#1441
(`restart(tailscaled@thor)` — `never.nodes`). Zero of the six produce a tag; a ticket
that cites a secret is a Frank-hands ticket and stays that way.

The doc's §4 row-iv replay set lands exactly where BROKER_WIRING predicted:
wq#1401 → `dir(/home/lumen/instructions/playbooks)`, wq#1402 → `dir(/home/lumen/agent)`,
wq#1403 → `dir(/home/lumen/backups/wq-proposals)`,
wq#1407 → `dir(/home/lumen/.npm-global/bin)`.

21 tickets carry a v2 verb (`reboot` ×11, `adb` ×5, `sudo` ×4, `fastboot`,
`poweroff`, `power-cycle`, `shutdown`) and stay `unresolved` with the verb named —
including the doc's stuck set wq#354/#362/#665/#679/#684.

## Tuning decisions this corpus forced

1. **`ssh` in prose is not a node mention.** wq#879 ("bare ssh itself", "ran ssh to
   bes") and wq#325 produced three phantom "unknown node" lines. An `ssh <target>`
   whose target is not a roster subject is only reported when the target looks like a
   host (dot, hyphen, digit, or `user@`). A false `unresolved` is a ticket slice C
   would refuse to spawn; a missed one still fails closed at the broker.
2. **A non-existent cited path is `unresolved`, never walked up to an existing
   ancestor.** wq#364 cites `/home/lumen/projects/hapi-firmware/dumps/blade-`; walking
   up would have granted the whole project dir nobody asked for.
3. **Deny before existence.** `/home/lumen/.secrets/x.token` need not exist to be
   refused — the deny-list runs on the path as cited, then again on the directory it
   normalises to (so `/home/lumen/creds.token` refuses on the basename glob rather
   than resolving to `/home/lumen`).
4. **An empty derivation emits no event.** 46 of 111 tickets cite nothing; the decision
   log keeps its one-line-per-ticket property and `pass_end.lint_scanned/lint_flagged`
   carries the totals instead.

## Rollback

`FOREMAN_NEEDS_LINT_MODE=off` — stage 3.7 derives nothing and logs nothing.
`warn` (default) is this slice; `apply` is slice C and still applies nothing here.
