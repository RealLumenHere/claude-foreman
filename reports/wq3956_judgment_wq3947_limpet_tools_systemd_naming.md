# wq#3956 — judgment on the verify-pass CONTRADICT of wq#3947 (LIMPET BUILD: package all Limpet components as .deb packages)

**Verdict: CONTRADICT (partial). wq#3947's diagnosis and scoping decision (package only
the 3 of 11 components with real source; document the other 8 with citations rather than
fabricate stubs) are correct and well-evidenced. One of the 3 packages it did build,
`limpet-tools`, ships a systemd unit file in the repo that will never actually be
installed into the built `.deb` due to a `dh_installsystemd` naming mismatch — the
daemon it's meant to run (`limpet-registry-daemon`) ships with no systemd unit at all.
Follow-up fix filed as wq#4071 (deps wq#3947). Not re-implemented here per the ticket's
instruction to verify, not fix.**

## Scope

Author: claude-sonnet-5 (wq#3947, PR #1909, commit `ea962003ac` / squashed as `64b1b9e7`
on main — diffed identical, confirmed via `git diff 64b1b9e7 ea962003 -- packaging/`,
0 lines). Verifier: claude-sonnet-5@high (this pass). Escalated to opus-5@high
(sub-agent `ae31191051e359b1c`) on finding a contradiction, per the ticket's archetype-8
protocol (same-model author/verifier escalates to a stronger model on contradiction).

## Claims checked

- **"Only 3 of 11 components have real source; the other 8 are design-only or unnamed."**
  Spot-checked all 8 "not packaged" citations against the live tree:
  - `docs/agent-chat-spec.md:5` — matches verbatim ("design-only ticket — nothing here
    installs a UI framework, opens a port, or registers an MCP server").
  - `docs/settings-spec.md` (cited as :102, actual CLI-contract language sits there) —
    confirmed no `limpet-config` entry point exists anywhere in the tree
    (`find -iname "*limpet-config*" -o -iname "*limpet_config*"`: zero hits).
  - `design/wq3806_limpet_architecture.md` (cited as :130, the actual `limpet-workspace`
    line is :141 — **off by 11 lines**, a citation-accuracy nit, not a substantive error;
    the quoted content, "in-process: workspace tools, §5", is correct) — confirmed
    accurate as of 2026-10-15 03:23, i.e. when wq#3947 ran.
  - `limpet-installer`/`limpet-startbar`/`limpet-precheck`/`limpet-greeter`: grepped
    `docs/`, `design/` for each name. Found one hit the README's "no spec or source under
    this name anywhere" claim missed: `design/limpet/brand/themes/lightdm/limpet-greeter.css`.
    Checked timing (`git log --follow`): that file landed in commit `7380efb9` (wq#3948,
    **2026-10-15 11:54:05**), ~8.5 hours *after* wq#3947 (`ea962003`, 03:23:58). The claim
    was accurate when made; it is pre-existing-knowledge drift from a later ticket, not a
    wq#3947 defect. Same pattern for `limpet-workspace`: wq#3934 (03:39:44, same day)
    added `bin/limpet-workspace`/`workspace_cli.py` to the `limpet-tools` package *after*
    wq#3947 documented it as unpackaged — again later drift, not a wq#3947 error, though
    it means `packaging/README.md`'s "not packaged yet (8 of 11)" table is now stale.
- **"Could not run dpkg-buildpackage/dh/lintian in this sandbox."** Reproduced
  independently: `command -v dh` and `dpkg-buildpackage -us -uc -b --root-command=fakeroot`
  both refused by this verify sandbox's Bash allowlist too ("This command requires
  approval" / "requires the following parts require approval"). Confirms the same
  limitation, not an excuse — tried once each, did not retry (ticket's own instruction
  that this refusal shape is not retryable).
- **Debian source-package mechanics for the 3 shipped packages** (`limpet-hald`,
  `limpet-mcp-meta`, `limpet-tools`): read every `debian/{control,rules,install,postinst,
  prerm,changelog,source/format,conffiles}` file plus the systemd `.service` units and the
  `bin/*` wrapper scripts, and cross-checked every source path the `rules` files stage
  (`ops/wq3809-limpet-hal/{hal.py,usb_boot_signatures.py,usb_boot_signatures.toml}`,
  `limpet/{__init__,target_devices}.py`, `limpet/target-devices.d/*.toml` [12 files] +
  `91-limpet-targets.rules`, `ops/wq3808-limpet-mcp-meta-server-a1/{meta_server,
  cli_wrapper,registry,stdio_mcp_client}.py` + `manifest.example.json`, `limpet/{cli,
  daemon,registry,registry_core,manifest,health}.py`) against the live tree — every path
  exists. Checked every `debian/rules` recipe line is **tab-indented**, not space-indented
  (`grep -nP '^\t'` found 19/13/18 tab lines respectively, 0 space-indented recipe lines),
  matching the author's own claim to have checked this by hand.
  Checked `cli.py`'s/`daemon.py`'s internal import graph (`manifest`, `registry`,
  `registry_core`, `health`) is fully self-contained within what wq#3947's own `rules`
  file stages — no missing transitive dependency in wq#3947's original scope. (The later
  wq#3934 addition of `workspace_cli.py`/`workspace_report.py` to the same package *does*
  have an unstaged transitive dependency — `obligations.py`, `critic.py`,
  `workspace_console.py`, `workspace_index.py` are all imported by `workspace_cli.py`/
  `workspace_report.py` but never copied by `rules` — but that gap belongs to wq#3934,
  which modified this packaging after wq#3947 landed; out of scope for this verify-pass,
  noted here only so it isn't lost.)
  Checked `hal.py`'s own `sys.path` self-correction logic
  (`ops/wq3809-limpet-hal/hal.py:44-51`, `_REPO_ROOT = dirname^3(__file__)`) against the
  packaged install layout (`/usr/lib/limpet/hald/hal.py`): the self-computed path resolves
  to `/usr/lib` in the packaged layout (wrong directory for the monorepo-relative logic
  it was written for), **but** this does not actually break the import — Python's
  namespace-package search continues past the spurious `/usr/lib/limpet` namespace
  candidate it introduces until it finds the real regular package at
  `/usr/lib/limpet/limpet/__init__.py` (reached via the wrapper's own
  `PYTHONPATH=/usr/lib/limpet`), so `from limpet import target_devices` still resolves
  correctly. Traced through PEP 420's "a regular package found anywhere in the scan wins
  over any namespace-portion candidate" rule to confirm this is not a live bug, just a
  coincidence that happens to work. Not reported as a finding since it doesn't fail.

## The defect

`packaging/limpet-tools/debian/control` declares exactly one binary package,
`Package: limpet-tools`. `packaging/limpet-tools/debian/rules` has:

```
override_dh_installsystemd:
	dh_installsystemd --no-enable --no-start
```

— no `--name` option. A checked-in unit file sits at
`packaging/limpet-tools/debian/limpet-registry-daemon.service`, meant to run the
`limpet-registry-daemon` reconcile-loop binary (`packaging/limpet-tools/bin/
limpet-registry-daemon`, staged at `/usr/bin/limpet-registry-daemon` per `debian/install`).

`dh_installsystemd` resolves which `debian/*.service` files belong to a package via
`debhelper`'s `pkgfile()` lookup: with no `--name`, for package `limpet-tools`, it checks
only `debian/limpet-tools.service`, `debian/limpet-tools@.service`, and the unprefixed
`debian/service` (first binary package only). `debian/limpet-registry-daemon.service`
matches **none** of these patterns — it is neither the bare default name nor a correctly
`<package>.<name>.service`-prefixed additional unit. `debian/install` (read directly:
three lines, `bin/limpet usr/bin` / `bin/limpet-registry-daemon usr/bin` /
`bin/limpet-workspace usr/bin`) does not stage the `.service` file into
`lib/systemd/system/` by hand either. debhelper does not error on an unrecognized file
under `debian/` — it is silently ignored.

**Net effect:** `dpkg-buildpackage` would still complete successfully (this is why the
author's own "verified debian/rules has tab-indented recipes" static check didn't catch
it — the build doesn't fail, it just produces an incomplete package), but the resulting
`limpet-tools.deb` ships the `limpet-registry-daemon` binary with **no systemd unit at
all**. That contradicts the ticket's explicit requirement ("systemd unit files") for this
package's long-running daemon.

By contrast, `limpet-mcp-meta` gets this right: `Package: limpet-mcp-meta` and
`debian/limpet-mcp-meta.service` match exactly, so `dh_installsystemd` finds it under the
plain default-name rule. `limpet-hald` correctly ships no unit (it's a one-shot CLI
detector, not a daemon) — no issue there.

## Confidence and escalation

Neither this verify-pass's sandbox nor wq#3947's original build sandbox can execute
`dpkg-buildpackage`/`dh`/`lintian` (both refused by the Bash allowlist — confirmed fresh
here, not assumed from the author's notes). So this finding, like the author's own
original review, rests on static reasoning about documented `debhelper` naming
conventions rather than an empirical build-and-inspect. Because this sandbox-vs-author
model pairing is identical (sonnet-5 vs sonnet-5), per the ticket's protocol a
contradiction escalates to a fresh opus-5@high instance for independent judgment before
being reported as confirmed.

Spawned agent `ae31191051e359b1c` (opus, high effort) with only the raw file contents and
the question — no exposure to this verdict — asking it to independently judge whether
`dh_installsystemd --no-enable --no-start` (no `--name`) would install
`debian/limpet-registry-daemon.service` for package `limpet-tools`. It answered **no**,
independently citing the same `debian/<package>.<name>.<type>` / `debian/<package>.<type>`
/ unprefixed-first-package-only lookup rule, ~90% confidence, and flagged one caveat it
couldn't resolve itself (a Read-tool guard blocked it from re-reading the files I'd
already read this session, so it couldn't confirm `debian/install` didn't stage the unit
some other way). I had already read `debian/install` directly myself (three lines, no
`.service` entry) — closing that caveat. Both reasoning paths converge: **CONFIRMED**.

## Disposition

Did not re-implement (ticket instruction: verify, don't fix). Filed **wq#4071** (lane
build, pri 2, deps wq#3947, claude-sonnet-5@medium) with the two concrete fix options
(rename to `debian/limpet-tools.limpet-registry-daemon.service` + `--name=
limpet-registry-daemon`, or rename to `debian/limpet-tools.service`) and an instruction to
verify by actually running `dpkg-buildpackage` once build tools are allowlisted for a
BUILD-lane worker — this ticket's sandbox could not do that empirical check, so neither
should the fix ticket assume static review alone is sufficient closure.

**Liveness probe** (must be able to fail, wq#2630): `ls packaging/limpet-tools/debian/
limpet-tools.service packaging/limpet-tools/debian/limpet-tools.*.service 2>/dev/null |
grep -q .` — currently **FAILS** (neither file exists today, only the misnamed
`limpet-registry-daemon.service`), and will **PASS** once wq#4071 renames the unit to a
pattern `dh_installsystemd` actually recognizes. Logged to ISSUE-LOG.md row 1364,
status ESCALATED.

## Coverage

Independently re-checked: every `debian/*` file across all 3 shipped packages, every
staged source path's existence, tab-vs-space recipe indentation, `hal.py`'s `sys.path`/
`PYTHONPATH` interaction under the packaged layout (traced through PEP 420 namespace-
package resolution — found a near-miss that turns out to work), the 8 "not packaged"
citations (found one, `limpet-greeter.css`, that postdates wq#3947 and so doesn't
contradict it), `cli.py`/`daemon.py`'s import graph against what wq#3947 itself staged,
and reproduced the "dpkg-buildpackage not allowlisted" limitation fresh rather than taking
it on faith. Not independently re-checked: hosting on a real Limpet APT repo (`packaging/
README.md`'s "queued as a follow-up physical task" — a physical-hardware step, correctly
deferred, not checkable from this sandbox either way).
