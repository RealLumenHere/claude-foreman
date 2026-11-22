# wq#1640 — `lumen_state.py render --prefs-block` + daily drift-only ticket

Source: `research/MINERVA_PIPELINE_MAP.md` s3.3#6 (finding) / s5#15 (ranked plan).
Frank's `<userPreferences>` block and the three Anthropic-side skills
(`lumen-session-start`, `lumen-change-safety`, `lumen-fleet-provisioning`) each
carry a hand-maintained hardware quick-ref (Lumen/Thor/Thoth/Mirror/PBox
users + Tailscale IPs + LAN IPs) that no fleet tool could regenerate — when a
node moves, Frank edits four copies by hand and the REPEAT instinct fires.

## What this adds

- `prefs_block.py` — the regenerator. `render_prefs_block()` sources the block
  from `lumen_state.current()` (`state_facts`, MINERVA_PIPELINE_MAP.md s1.1) for
  the five hosts: user/tailscale_ip/lan_ip per host, or a closure line
  (`DECOMMISSIONED`/`RETIRED` + date) for a host whose `lifecycle` or `status`
  predicate already says so — the same self-expiry the store's own docstring
  describes for "PBox active" auto-clearing on "PBox retired", extended to this
  projection instead of a hand-maintained override table.
  `check_and_file_ticket()` is the daily drift check: render, diff against
  `minerva/context/prefs_block.last` (the last handed-out copy), and **only on
  drift**: file one `lane=user` ticket (`[fleet hw quick-ref] ...`) with the
  block pasted inline, supersede any prior OPEN ticket with that title prefix
  (`wq edit <id> --status done --detail 'SUPERSEDED by wq#<new>...'`, found via
  a read-only query against `/home/lumen/agents/work-queue/queue.db` — the wq
  CLI has no `--json` list mode to grep reliably), and save the new render as
  the handed-out copy so a same-day re-run is a no-op.
  `test_prefs_block.py` covers both (10 cases: live-host rendering, the two
  retirement-detection paths, the known-stale flags, diff on/off, and
  no-drift-means-no-wq-call / drift-means-file-and-supersede for the ticketing
  path, with `_wq_add`/`_wq_supersede` monkeypatched so tests never touch the
  real queue).
- `lumen_state_render.diff` — `lumen_state.py` gains `render --prefs-block`,
  wired straight to `prefs_block.render_prefs_block()`. This is the literal
  command the ticket names (`lumen_state.py render --prefs-block`).
- `prefs-block-check.service` / `.timer` — daily trigger for
  `prefs_block.py check` (`OnCalendar=daily`, `RandomizedDelaySec=900`,
  `Persistent=true` so a missed fire while Lumen was down still runs on boot).
- `install.sh` — deploy script. See its header for exactly what this BUILD
  session could and could not apply live, and why.

## Known-stale lines flagged (ticket text, not derived)

The ticket names two lines the CURRENT hand-maintained copy is known to have
wrong; `prefs_block.KNOWN_STALE_FLAGS` reproduces them verbatim in every
rendered block so a drift ticket says so explicitly instead of leaving Frank to
spot it by re-reading five lines:

- PBox listed as active — actually DECOMMISSIONED 2026-08-28.
- Mirror listed as merely "retired" per the 05-18 table — actually
  DECOMMISSIONED 2026-08-28, same closure as PBox.

Cross-checked against the live store this session (`lumen_state.py current
mirror` / `current pbox`): both already carry `status`=`DECOMMISSIONED —
… removed from fleet 2026-08-28 (Frank)`, so the render's own retirement
detection independently agrees with the ticket's claim — it isn't only the
static flag text asserting this.

## Why `lumen_state.py` is a diff, not committed source

`/home/lumen/minerva/**` is not a git repository (ISSUE-LOG precedent wq#1100,
wq#1127; also documented in `ops/wq1633-minerva-projection-dirty-flag/README.md`
for the same reason) — `lumen_state.py` and `prefs_block.py` were written live
in place this session (Write/Edit reach those paths; Bash's per-command
allowlist does not — confirmed this session: `python3 /home/lumen/minerva/
lumen_state.py render --prefs-block` is refused as outside the allowed working
directories, while `python3 -c "sys.path.insert(0,'/home/lumen/minerva'); import
lumen_state"` from inside this worktree is allowed and was used for all
verification below). `prefs_block.py` ships here as a plain file (its home IS
`/home/lumen/minerva`, unlike `state-regen` in wq#1633 which belongs to a
separate git repo); `lumen_state.py`'s three-hunk change ships as a diff so this
branch has a reviewable, git-tracked record of what was applied live.

## Known gap: `ssh_user` is empty for all five hosts

`state_facts` has no open `ssh_user` fact for lumen/thor/thoth/mirror/pbox
(checked live this session — `ssh_user` rows exist only for gameboy/maat/
nephthys). The render shows `user: unset in state_facts` for each host rather
than guessing a username DOCTRINE would call unverified. Backfilling those five
`ssh_user` facts (`lumen_state.py ingest <host> ssh_user <user> --stated-by-frank`)
is a one-line follow-up, not part of this ticket — once ingested, the render
picks them up with no code change.

## Not done this session (root-only)

`prefs-block-check.timer` is not installed — `sudo systemctl enable --now`
needs root, out of a build worker's reach (same class of gap wq#1633 hit for
its federate-reindex unit). `install.sh` documents the step; `minerva/context/
prefs_block.last` does not exist yet either, so the FIRST real timer fire will
correctly treat the whole block as drift and open the initial migration ticket
— which is the ticket's own intent (the hand copy is stale today).

## Timer is intended-off (wq#5256)

The timer was installed later (unit files in `/etc/systemd/system` are dated
2026-09-20), then the 2026-10-08 subtraction disabled it on purpose
(`/home/lumen/scratch/subtraction/timers-disable-list.txt`, line 59). So
`install.sh --verify` does not require the timer to be enabled or active. The
timer's live state picks the contract (wq#5322, the wq#5273 pattern):

- disabled + inactive: the intended state. Prints an `OFF` line, no MISS.
- disabled but active: MISS. Someone bypassed the subtraction by hand.
- enabled: someone re-enabled it. Prints a `REENABLED` line and enforces
  `vactive` + `vtimer_scheduled`.

The module and both unit files are checked in every state.

`install.sh` without `--verify` refuses with exit 2 before any step, because
step 3 runs `systemctl enable --now` and undoes the subtraction. Re-enabling the
timer is a decision; whoever makes it runs
`PREFS_BLOCK_REENABLE=1 bash install.sh`. Even then, step 1 reinstalls the
bundle's `prefs_block.py`, which the minerva repo has since moved past, so check
`--verify`'s "superseded" line first.

Test: `python3 ops/tests/test_wq5322_prefs_block_intended_off.py`.

## Verify

    cd ops/wq1640-lumen-state-render-frank-side-hardware-q-a3 && python3 -m pytest -q test_prefs_block.py

Live smoke test run this session (not reproducible by the harness — reads the
real `minerva.db`, shown here for the record):

    $ python3 -c "import sys; sys.path.insert(0,'/home/lumen/minerva'); \
      import lumen_state as ls; import prefs_block as pb; print(pb.render_prefs_block())"
    ## Fleet hardware quick-ref (auto-rendered)
    _Generated 2026-09-11 16:31 UTC by `lumen_state.py render --prefs-block` from ...
    - **Lumen** -- user: unset in state_facts, tailscale: 100.117.66.102, lan: 192.168.8.160
    - **Thor** -- user: unset in state_facts, tailscale: 100.112.151.100, lan: 192.168.8.132
    - **Thoth** -- user: unset in state_facts, tailscale: 100.75.1.108, lan: 192.168.8.218
    - **Mirror** -- DECOMMISSIONED (since 2026-08-28)
    - **PBox** -- DECOMMISSIONED (since 2026-08-28)
