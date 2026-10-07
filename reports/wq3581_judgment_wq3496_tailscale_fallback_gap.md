# wq#3581 — judgment: does wq#3496's node-bench fix make its own acceptance command work?

Judge: claude-opus-5@high, worktree `wq3581-a2`, 2026-10-07. Verify/judgment only, no code changed,
no ssh to any fleet node attempted. Nothing in this report depends on credentials.
Target: `/home/lumen/bin/node-bench` at `fb95d7c` (PR lumen-bin#234, merged to `main`), against the
verifier's (claude-sonnet-5@high) contradiction: `select_ip()` only falls back to `lan_ip` when
`tailscale_ip` is absent, and both target nodes have a `tailscale_ip` that does not work.

## Ruling 1 — contradiction UPHELD

The verifier is right on every point I could check. `select_ip()` (node-bench:84-94) falls back
on the field being absent, not on the address failing. `resolve()` (:97-109) feeds it only what
`parse_state()` (:75-81) pulls out, which is `user|tailscale_ip|lan_ip`. It never reads
`ssh_user` or `status`. Against live lumen_state today, I imported node-bench and called the
real `resolve()` (it runs no ssh):

| node | `resolve(node)` → | what lumen_state says about that address |
|---|---|---|
| ptah | `('lumen', '100.78.207.56')` | `ptah.ssh_user`: "Tailscale SSH is ENABLED on this node: tailnet-path ssh needs interactive check approval … (verified 2026-10-06)"; key auth works "on LAN" |
| sekhmet | `('lumen', '100.75.162.62')` | `sekhmet.status = DEGRADED`: "Tailscale is LOGGED OUT (offline in tailnet since ~13h; needs browser re-auth, Frank-gated)" |

Both facts date from 2026-10-06, the day before the PR. The fallback branch was written for these
two nodes (its docstring cites them), and it is unreachable for both of them. It only fires for a
node that has no tailnet address at all, and the P1 boards were never in that case. So the
literal acceptance command, `node-bench ptah run --tag p1-nvme` with no `--host`, goes to the
tailnet address. That address is logged out on sekhmet and needs a human approval click on ptah.

On the `BatchMode=yes` question: I did not test it live, and the ruling does not depend on it.
`ssh()` (:112-115) passes `BatchMode=yes -o ConnectTimeout=8` with a 60 s subprocess timeout.
If ptah's Tailscale SSH check is pending, the connection either stalls until the 60 s
`subprocess.TimeoutExpired` (which `probe()` does not catch, so the user gets a traceback) or it
gets denied. Neither outcome is a successful non-interactive login. Because Tailscale SSH takes
over port 22 on the tailnet address, the LAN key auth that `ptah.ssh_user` vouches for never
comes into play on that path. A logged-out tailscaled (sekhmet) has no tailnet path at all.

The defences I considered do not hold:

- *Out of scope / pre-existing.* No. The diff added this fallback itself, and its docstring says
  it exists because P1 tailnet reachability is bad (node-bench:14-16, :85-87). Code that names its
  target case and does not trigger on it is a defect in the diff, not something left over from
  before.
- *"Intermittent" means `--host` is a documented operator burden.* Partly. `--host` works
  (`resolve('ptah', X)` returns `X`) and is in the module docstring and `--help`. But the
  ticket's own acceptance step does not pass `--host`. On these nodes the tailnet path is not
  intermittent. One is logged out pending a Frank-gated re-auth, and the other needs an
  interactive approval on every connection. The docstring describes a more benign situation than
  lumen_state records.

## Ruling 2 — the acceptance command also fails with `--host`, because a fix logged as SOLVED never shipped

Escalating this found a second gap that matters more than the first. `kb/ISSUE-LOG.md` row 1204
(wq#3496) reports that `probe()` on ptah "returned returncode=1 (treated as unreachable) even
though SSH and the remote script both succeeded". The cause: PROBE ends with
`for f in …; do [ -f "$HOME/models/$f" ] && echo "HAVE=$f"; done`. When the last file in that
list is absent, the loop exits 1, `bash -s` exits 1, and `probe()` (:142-144) treats that as an
ssh failure. The row says "appended a trailing 'true' … | SOLVED".

That `true` is not in `main`. `git show fb95d7c:node-bench` and the current tree both end PROBE at
the bare loop (node-bench:136-137), and so does the other wq#3496 commit `cd60efc`. The fix exists
only as an **uncommitted** edit in the abandoned first attempt's worktree,
`/home/lumen/worktrees/wq3496-a1/_lumen-bin` (`git status`: ` M node-bench`; its diff contains
`+true` after the HAVE loop). The a2 attempt that merged never picked it up. This is the
lost-fix pattern again: the ISSUE-LOG row describes a fix that is not in the code it refers to.

Reproduced locally with the shipped PROBE tail and MODELS order:

```
files order: Qwen3-1.7B-Q4_K_M.gguf Qwen3-8B-Q4_K_M.gguf Qwen3-30B-A3B-Q4_K_M.gguf
as shipped    -> rc 1
trailing true -> rc 0
```

The last file is the 18.6 GB 30B-A3B quant. A 3.8 GiB board with a 29 GB eMMC root will not
have it. So `node-bench ptah run --tag p1-nvme --host 192.168.8.125` reaches the node, runs the
probe, and still dies with `cannot reach lumen@192.168.8.125: ssh failed` (exit 3). `--models
qwen3-1.7b` does not help either, because `probe()` lists every MODELS file (:141). The only way
past it is `--dry-run --assume-mem-gb`, which does not benchmark anything. This bug was already
in `db69ec4` and hits any node that lacks the 30B file, but adding the new model did not fix it,
and the acceptance run hits it on both P1 targets.

## Ruling 3 — recommendation: follow-up ticket, not a reopen; fall back on failure, not on prose

The diff's other claims hold, and I re-checked them myself. The qwen3-1.7b entry matches HF
(`1107409472`, `b139949c…1897`). The `plan()` merge-order fix is correct. `selftest` passes, and
`pytest tests/test_node_bench.py` is 2/2. wq#3496 was already logged PARTIAL (row 1206), so
nothing here changes its merged status. File one follow-up for node-bench and make the live-run
re-dispatch depend on it:

1. **Ship the lost `true`** (one line) and add a selftest that runs the PROBE tail with no models
   present and asserts rc 0. Correct ISSUE-LOG row 1204: the fix was described but not merged.
2. **Fall back on connection failure, not on a missing field.** If `ssh` to `tailscale_ip`
   returns 255 or times out, retry once on `lan_ip` with a `WARN`, and catch `TimeoutExpired`.
   Key the retry on rc 255 specifically. Treating every nonzero rc as unreachable is what turned
   the item 1 bug into "cannot reach". Do **not** have `resolve()` parse `status`/`ssh_user`
   prose: those are free-text notes marked `[UNVERIFIABLE — no probe]`, and keying routing on
   phrases like "LOGGED OUT" breaks the first time someone rewords them. A failed connection is
   the real signal. The same retry has to cover `scp` in `cmd_collect` (:354).
3. **Re-dispatch the live run** (row 1206) with `needs:ssh(ptah),ssh(sekhmet)`. Until item 2
   lands, spell out `--host 192.168.8.125` / `--host 192.168.8.135` (the `lan_ip` values in
   lumen_state). Without item 1, the run fails even with the grant.

## What I checked myself

- `lumen_busy check` → busy key held by the parent (`escalating_to_opus_agent_spawned`); left as is.
- `git -C /home/lumen/bin show fb95d7c -- node-bench`; read node-bench :60-157 and :290-309 at
  `main`; `git log --all -- node-bench` (db69ec4, cd60efc, fb95d7c; only fb95d7c is on `main`).
- `python3 /home/lumen/minerva/lumen_state.py current ptah|sekhmet`, filtered to
  `user|ssh_user|tailscale_ip|lan_ip|status|hardware|ram`. Quotes above are verbatim.
- `resolve()` called through `SourceFileLoader` against live state, with and without a host.
- PROBE-tail exit code through `bash -s`, with a nonexistent `$HOME`, as shipped and with `true`.
- `git -C /home/lumen/worktrees/wq3496-a1/_lumen-bin status/diff` for the uncommitted `+true`.
- HF tree API for `unsloth/Qwen3-1.7B-GGUF`; `node-bench selftest`; pytest.

Not checked: live ssh to either node. That was deliberate, since fleet-exec denies ssh to ptah
and I did not route around it. I also could not run `tailscale status` (it needs approval in
this lane), so the tailnet facts rest on lumen_state's 2026-10-06 entries. Row 1204's "SSH …
succeeded" on ptah does not say which address it used. It does not contradict Ruling 1, because
ptah's LAN path is the one lumen_state calls healthy.
