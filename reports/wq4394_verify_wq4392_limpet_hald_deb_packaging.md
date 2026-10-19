# wq#4394 — verify-pass of wq#4392 (LIMPET .deb packaging: hal_daemon.py install fix)

- Ticket: wq#4394 (verify, archetype 8), deps on wq#4392
- PR: http://100.121.17.5:3000/lumen/claude-foreman/pulls/2288
- Commits verified: `0fec2fcd` (content), `8f7dfe6f` (merge)
- Author model: claude-sonnet-5 — Verifier: claude-sonnet-5@high (fresh instance)
- Verdict: **CONFIRM** — no contradiction, no escalation.

## What wq#4392 claims

1. **Bug fix**: `packaging/limpet-hald/debian/rules` staged `hal.py` and
   `usb_boot_signatures.{py,toml}` but never installed `hal_daemon.py`, even
   though `systemd/limpet-hald.service`'s `ExecStart` points at
   `/usr/lib/limpet/hald/hal_daemon.py` — the built `.deb` would ship a unit
   that fails to start with "No such file or directory". Fix: added the
   missing `install -m 0755 ... hal_daemon.py` line.
2. **Four fabricated-evidence findings**, documented in `packaging/README.md`
   instead of acted on: the ticket's expanded wording (Sentinel+ResultStore,
   firstboot wizard, XFCE panel via dpkg, ISO-squashfs `.deb` baking) doesn't
   match the tree.

## Verification performed

### Bug fix — reproduced the failure and the fix

Read `packaging/limpet-hald/systemd/limpet-hald.service:8` —
`ExecStart=/usr/bin/env python3 /usr/lib/limpet/hald/hal_daemon.py run --foreground`.
Confirmed `debian/rules` pre-fix installed only `hal.py`,
`usb_boot_signatures.py/.toml`, never `hal_daemon.py` — matches the claim.

Built two scratch install roots by hand (no `dpkg-buildpackage` — not on this
worker's Bash allowlist, confirmed by refusal on bare invocation) replicating
exactly what `debian/rules`' `install` lines do:

- `/tmp/fworker-wq4394/probe_before/usr/lib/limpet/hald/` — only `hal.py`
  (pre-fix state). Running
  `python3 .../hal_daemon.py --help` against it:
  `python3: can't open file '.../hal_daemon.py': [Errno 2] No such file or directory`
  — reproduces the exact failure mode the commit message claims, falsifying
  the probe on the pre-fix tree as required (wq#2630).
- `/tmp/fworker-wq4394/probe/usr/lib/limpet/hald/` — `hal.py` +
  `hal_daemon.py` (mode 0755, matching the new `debian/rules` line) (post-fix
  state). Same command now prints the daemon's `--help` usage cleanly.

Also checked: `hal_daemon.py` only needs `hal` via a same-directory
`sys.path.insert` (`ops/wq3809-limpet-hal/hal_daemon.py:35-36`, confirmed by
reading the file) — no additional missing sibling file, so the one-line fix
is sufficient. Tab indentation on the new `install` line matches surrounding
Makefile lines (`cat -A`) — no Makefile syntax break.

### Four documentation findings — checked each citation against the tree

- **"Sentinel + ResultStore"**: `grep -rn "Sentinel\|ResultStore"` across
  `ops/wq3808-limpet-mcp-meta-server-a1/` and
  `design/wq3806_limpet_architecture.md` — zero hits. Confirmed absent.
  (Tree does contain an unrelated `design/wq4059_sentinel_agent_eval.md`, a
  different "Sentinel" concept not tied to the MCP meta-server — doesn't
  contradict the claim, which is scoped to the meta-server package.)
- **"firstboot wizard"**: `design/wq4060_limpet_deployment_distro_ux.md:144-151`
  reads "a provisioner, not a wizard ... a Tails-style mandatory greeter is
  explicitly rejected" — matches the citation. `limpet-wizard.desktop` exists
  only at `limpet/iso-builder/overlay/usr/share/applications/` and maps to the
  unrelated Target Device Wizard — confirmed.
- **"XFCE layout + panel scripts"**: both cited files exist
  (`.../xfce4-panel.xml`, `genmon-{workspace,toolhealth,hardware}.sh`) and
  `limpet/iso-builder/build.sh:191` is exactly
  `cp -a "$HERE/overlay/." "$CHROOT/"` — ISO overlay copy, not `dpkg`. Line
  number citation exact.
- **"Packages baked into ISO squashfs"**: `build.sh:196-205` is exactly the
  `stage3_inject_limpet_packages` block doing raw `cp`/`find -exec cp` of
  `limpet/*.py`, `registry/`, `hal.py`, and the four mcp-meta-server modules
  into the chroot — not `dpkg -i` of this directory's `.deb`s. Confirmed
  diverged delivery path, line numbers exact.

### Adjacent-gap sweep (not claimed by wq#4392, checked anyway)

Checked `limpet-mcp-meta` and `limpet-tools` packaging for the same
staged-but-not-installed shape: `limpet-mcp-meta.service`'s
`ExecStart=/usr/bin/limpet-mcp-meta ...` resolves via
`debian/install` (`bin/limpet-mcp-meta usr/bin`) to a wrapper script that
`exec`s `/usr/lib/limpet/mcp-meta/meta_server.py`, which IS installed by
`debian/rules`. No equivalent gap. `limpet-tools`' `debian/rules` already
carries its own prior fixes (wq#4071 unit naming, wq#3934 templates dir,
wq#4086 kb-sources) — no missing-file gap found. ISSUE-LOG.md row 1470
matches the PR's own narrative (prior-art check, no new finding beyond it).

## Conclusion

Every citation in the diff and its `packaging/README.md` addition checks out
against the current tree (exact line numbers, exact grep results). The core
bug claim reproduces both ways (fails pre-fix with the exact stated error,
succeeds post-fix) under a hand-built probe standing in for
`dpkg-buildpackage`, which this worker's allowlist refuses outright. No
contradiction found — **CONFIRM**, no escalation.
