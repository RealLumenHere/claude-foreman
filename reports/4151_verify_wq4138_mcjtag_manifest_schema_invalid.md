# wq#4151 — verify-pass on wq#4138 (LIMPET: build limpet-plugin-mcjtag, secondary/alternate JTAG)

PR `claude-foreman#2089` (http://100.121.17.5:3000/lumen/claude-foreman/pulls/2089),
merge commit `24298236` on `main` (branch
`feat/wq4138-limpet-build-limpet-plugin-mcjtag-second-a1`). 11 files / 394 insertions,
scoped to `plugins/limpet-plugin-mcjtag/`. Author: claude-sonnet-5 (fworker-wq4138, one
dispatch, AC1 citation `limpet-plugin.toml:59-64`). Verifier: claude-sonnet-5@high
(fresh instance, wq#4151).

## Claim under test

The PR packages Ryan Malloy's `mcjtag` (PyPI v2026.2.18) as a secondary/alternate
JTAG/SWD plugin to `limpet-plugin-openocd-mcp`, with a detailed `limpet-plugin.toml`,
install/uninstall/health scripts, udev rules, two OpenOCD configs, a hardware manifest,
a README, and an MIT LICENSE reconstructed from upstream metadata. The implicit claim
under test is that this is a correctly-formed, installable Limpet plugin.

## What I checked

1. **Diff cold**: `git show --stat 24298236` / per-file `git show a267ea5e:<path>` — all
   11 files match the PR's claimed scope; nothing outside `plugins/limpet-plugin-mcjtag/`
   touched.
2. **Independently re-verified every factual claim against the real upstream package**
   (downloaded `mcjtag-2026.2.18.tar.gz` fresh from PyPI via `curl`, since `pip install`
   is not on this worker's allowlist — bypassed, not blocked by, the harness gap):
   - `license_expression: MIT`, classifier `License :: OSI Approved :: MIT License` —
     confirmed via PyPI JSON API.
   - No `LICENSE` file in the sdist, and `pyproject.toml`'s
     `[tool.hatch.build.targets.sdist].exclude` contains `"*.cfg"` — confirmed by
     listing/extracting the real tarball; no `.cfg` file exists anywhere in it.
   - `src/mcjtag/resources.py` defines exactly 9 `@mcp.resource("jtag://...")` decorators;
     upstream's own `docs/.../reference/resources.mdx` documents only 7 (missing
     `jtag://breakpoints` and `jtag://swd/dap`) — confirmed by grepping both files. The
     README's resource table (`README.md:47-57`) states this exact 7-vs-9 discrepancy
     correctly.
   - Safety-default claims (`README.md:92-103`) all confirmed in source:
     `MCJTAG_SAFE_WRITE_RANGES` defaulting to SRAM `0x20000000-0x20100000`
     (`mixins/helpers.py:55-58`), `MCJTAG_ALLOW_RAW` deny-list bypass
     (`mixins/raw.py:55,83,110`, deny-list at `mixins/raw.py:17-32`),
     `MCJTAG_MAX_SEARCH_RANGE` (`mixins/memory.py:31`).
   - Port-6666 conflict with `limpet-plugin-openocd-mcp` (the "SAME-PORT WARNING"):
     confirmed both `mcjtag`'s `connection.py:29,73` and openocd-mcp's own
     `mcp/classify_access.py:167,242,299` default to TCL port 6666.

   Every one of these specific, checkable claims held up. This plugin's documentation is
   unusually diligent and not fabricated.
3. **Ran the actual acceptance tool for this file type**: `limpet/cli.py` documents
   itself as "`limpet validate <path>` — check a `limpet-plugin.toml` before installing
   it." I ran it (via package-qualified import from repo root, the workaround
   ISSUE-LOG#1302 documents for `limpet/cli.py`'s own sys.path breakage, to rule out that
   bug producing a false result) against the exact file this PR ships:

   ```
   INVALID plugins/limpet-plugin-mcjtag/limpet-plugin.toml:
     - [plugin].kind None must be one of ('tool', 'hardware', 'data')
     - [plugin].category 'hardware-interface' must be one of ('disassembler',
       'decompiler', 'debugger', 'fuzzer', 'firmware', 'sdr', 'flash-programmer',
       'network', 'data', 'other') (or omitted)
     - [dependencies] 'requires' constraint must be a string
     - [dependencies] 'system_packages' constraint must be a string
     - [dependencies] 'conflicts' constraint must be a string
     - [health] must set a non-empty string `command`
   ```

   This is not a hypothetical: it's the literal output of `limpet validate
   plugins/limpet-plugin-mcjtag/limpet-plugin.toml`, reproduced twice (once via
   `exec(open('limpet/cli.py').read())` with `limpet/` on `sys.path`, once via
   `from limpet.manifest import parse_manifest` from the repo root) to rule out an import
   artifact. `limpet/manifest.py`'s `_validate_deps_table` (`manifest.py:187-202`)
   requires `[dependencies]` to be a table of `name = "constraint"` pairs, not the
   `requires = []` / `system_packages = [...]` / `conflicts = [...]` list shape this PR
   (and every other plugin in the family) uses; `manifest_from_dict` (`manifest.py:312-322`)
   requires a `command` key under `[health]`, not `check`. `[plugin].kind` is required
   and was never set.

4. **Checked whether this is unique to this PR**: ran the same `limpet validate` against
   `plugins/limpet-plugin-openocd-mcp/limpet-plugin.toml` (the already-shipped "primary"
   plugin this PR explicitly complements/conflicts with) — byte-for-byte the same 6
   errors. So the `plugins/limpet-plugin-*/limpet-plugin.toml` authoring convention has
   drifted from `limpet/manifest.py`'s actual schema across the whole plugin family, not
   just this diff; this PR's author correctly followed the (broken) established sibling
   pattern rather than inventing a new defect. Logged as ISSUE-LOG row 1404.
5. **Independently re-derived the `[health].check` logic bug** that the schema mismatch
   currently masks: even ignoring the `check`/`command` key-name issue, the check string
   itself — `python3 -c 'import mcjtag' 2>&1 | grep -qv Error` — is inverted under the
   exact runner `limpet/health.py`'s `_shell_runner` uses
   (`subprocess.run(command, shell=True, ...)`, i.e. `/bin/sh` → `dash` on this host).
   Reproduced with the real subprocess call: a *successful* `import` (plugin healthy)
   yields exit 1 → `HealthResult.ok = False`; a *failing* `import` (module missing,
   plugin broken) yields exit 0 → `HealthResult.ok = True`. Healthy and unhealthy are
   swapped. (Root cause: GNU grep's `-q` combined with `-v` returns 0 on empty stdin
   with no lines at all, and returns 0 as soon as the first non-matching line of a
   traceback is seen; neither sibling plugin uses this pattern — they all gate on the
   bare import's own exit code, or a positive `grep -q <success-marker>` — see
   `plugins/limpet-plugin-{buspirate,hackrf,openocd}-mcp/limpet-plugin.toml`'s
   `[health].check` lines.)

## Verdict

**CONTRADICT.** The plugin's documentation and safety claims are unusually well
verified against real upstream artifacts — better diligence than most plugins in this
family get credit for. But the shipped `limpet-plugin.toml` fails the project's own
`limpet validate` tool with 6 concrete schema errors, so as written it cannot be loaded
by `plugin_registry.py`'s drop-in scan path once a manifest actually reaches that
schema. This mirrors `reports/wq4156_verify_wq4143_classify_access_halt_bypasses_energize_gate.md`'s
shape — a PR that is internally consistent and well-tested against its own stated
design but fails the surrounding system's real contract — except here the gap is
structural (the file doesn't parse) rather than behavioral, and it is shared by every
plugin in the family rather than unique to this diff.

Per archetype 8, this escalates to opus-5@high for judgment: whether to (a) fix
`limpet-plugin-mcjtag`'s manifest alone to match `limpet/manifest.py`'s real schema, or
(b) open a cross-cutting ticket to realign the whole `plugins/limpet-plugin-*/` family
(mcjtag, openocd-mcp, buspirate-mcp, hackrf-mcp, and whatever else exists) with the
schema `limpet/manifest.py` actually enforces, including the `[health].check` grep
inversion once the key is renamed to `command`.

ISSUE-LOG row 1404 (this finding).
