# wq#4321 — verify wq#4319 (PR#2220, 4x Spectrio + per-role config: exit node, sentinel, RE host, Limpet testbed)

**Verdict: CONFIRM.** wq#4319's done-notes hold under a fresh adversarial pass. The diff
is honestly scoped (config/scripts only, physical flash-to-eMMC queued as
`physical_tasks`, same posture as wq#3430/wq#4181), its executable claims check out
against the actual target scripts, and its own test suite passes clean. Found two
minor/cosmetic gaps, neither acceptance-relevant (below).

## Scope re-derived, independent of the author's report

PR#2220 is a single commit (`a364554b`, already merged into `main` as of this verify
pass's start — `git log main..origin/feat/wq4319-...` is empty, `merge-base main
<branch>` == branch head). 6 files touched, confirmed via `git show --stat a364554b`:
`roles.yaml`, `render_role.py`, `test_render_role.py`, `README.md` (all modified),
`tf1-c-adb-host.sh` (modified), `tf1-d-limpet-testbed.sh` (new).

wq#4319's ticket maps Units 3-6 onto tf1-a/b/c/d. Checked the "Units 3/4 already
covered by wq#4181" claim is not just asserted: `git show a364554b -- roles.yaml |
grep -n "^@@\|tf1-a\|tf1-b"` shows the `tf1-a-exit-node:`/`tf1-b-external-vantage:` role
bodies appear only as unmodified context lines in the `build_order` hunk — their
content is untouched by this diff, consistent with "nothing to do for Units 3/4."

## Things I tried to break and couldn't

1. **`hal_daemon.py run --foreground` and `limpet/daemon.py --interval 30`** — the
   exact invocations baked into `tf1-d-limpet-testbed.sh`'s two systemd units. Grepped
   both target files directly instead of trusting the commit message: `hal_daemon.py`
   (`ops/wq3809-limpet-hal/hal_daemon.py:208-211`) defines `argparse` with
   `command` choices `run`/`status` and a `--foreground` store_true flag — the exact
   CLI shape the unit calls. `limpet/daemon.py:32-39` defines `--interval` (default
   30) via argparse — same. Both scripts run their main loop in the foreground without
   self-forking (`HalDaemon.run()` blocks on `select.select`; `daemon.run()` blocks on
   `time.sleep`), matching the units' `Type=simple`.
2. **Import path for `limpet/daemon.py` under the unit's literal invocation** — the
   unit runs `python3 ${FOREMAN_CHECKOUT}/limpet/daemon.py --interval 30` (absolute
   script path), not the docstring's documented `python3 -m daemon`. `daemon.py` does
   a bare `from plugin_registry import Registry` (no package prefix), which only
   resolves if the importing directory is on `sys.path`. Checked whether the literal
   invocation breaks this: running a script by absolute path puts the script's own
   directory on `sys.path[0]`, and `plugin_registry.py` lives in the same directory
   (`limpet/`) as `daemon.py` — confirmed via `ls limpet/ | grep plugin_registry`.
   Import holds; not a bug, but worth flagging since the mismatch between the
   docstring's documented invocation and the unit's actual one is a trap for whoever
   next edits either file without rechecking this.
3. **MCP meta-server "stays disabled" claim** — checked both halves independently: (a)
   `render_role.py:89-94` rejects any `limpet_testbed` role with
   `meta_server_enabled != false` (confirmed by running the test suite's own negative
   case, and independently via a direct `validate_role()` call); (b)
   `tf1-d-limpet-testbed.sh` creates systemd units for `limpet-hal-daemon.service` and
   `limpet-registryd.service` only — grepped for `meta_server.py`/`ExecStart` together,
   zero hits, so no unit exists to run it regardless of the flag. The
   `manifest.example.json` detail in the commit message ("every manifest entry
   disabled") is imprecise taken alone — `cli_tools` entries (`strings`, `file`) ship
   `enabled: true` — but those aren't `mcp_servers` and aren't covered by the wq#3786
   hold; the three `mcp_servers` entries (`rea`, `ghidra`, `radare2`) are indeed all
   `enabled: false`. Not a defect in the shipped role, just a loosely-worded commit
   line.
4. **Full test suite, fresh run, not trusting the "20/20" claim as written:**
   `python3 -m pytest projects/spectrio-tf1/roles/test_render_role.py -q` →
   `20 passed in 0.06s`. Also ran `render_role.py --validate` and `render_role.py
   tf1-c-adb-host` / `tf1-d-limpet-testbed` directly (not just via pytest import) —
   all exit 0, output sane.
5. **Adversarial probe outside the test suite's own input population**
   (doctrine: an acceptance probe must be able to fail) — fed `validate_role()` an
   `adb_host` role with all 5 required `re_tools` plus one extra, unapproved entry.
   **This was accepted** (`RE_TOOLS_REQUIRED.issubset(...)` is a floor, not an exact-set
   check) — a real gap in the validator, but inert in practice: `re_tools` in
   `roles.yaml` is descriptive metadata only, never consumed by `render_role()` or by
   `tf1-c-adb-host.sh` (which hardcodes its own install list independent of the YAML).
   Not acceptance-relevant for this ticket; noting it rather than burying it.
6. **`bash -n` on both touched scripts directly** (not just via the pytest wrapper) —
   clean on `tf1-c-adb-host.sh` and `tf1-d-limpet-testbed.sh`.

## Minor findings (neither blocks CONFIRM)

- `README.md:31` still reads "harden-armbian.sh — shared hardening, applies to
  tf1-a/b/c alike" after this diff added a 4th role (tf1-d) that also runs
  `harden-armbian.sh` first (per `tf1-d-limpet-testbed.sh`'s own header comment,
  "Run after harden-armbian.sh"). Stale enumeration, cosmetic.
- `validate_role()`'s `re_tools` check is subset-only (point 5 above) — would let a
  future roles.yaml edit sneak an unapproved tool into the RE-host role's declared
  list without failing CI. Low severity since the field doesn't drive any generated
  artifact today.

## Not re-litigated

- The physical flash-to-eMMC / bench bring-up queue (`physical_tasks` in `roles.yaml`)
  — correctly out of scope for a worktree with no fleet-exec grant for TF1 units, same
  posture as wq#3430/wq#4181, and the ticket's own notes don't claim otherwise.
- Package-name correctness for the apt/pip installs in `tf1-c-adb-host.sh`
  (`rkdeveloptool`, `heimdall-flash`, `flashrom`, `mtkclient`, `pyamlboot`) — cannot be
  verified against a real Armbian/Debian apt tree or PyPI reachability from this
  sandbox, and the ticket's own posture is "scripts only, unapplied to hardware."

No contradiction found. Not escalating to opus-5@high.
