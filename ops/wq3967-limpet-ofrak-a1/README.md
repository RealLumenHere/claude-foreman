# wq#3967 — LIMPET OFRAK plugin (unpack / modify / repack)

Fills gap G7 from the competitive analysis (`design/wq3929_competitive_analysis.md:179-189,
233, 284`, "A7 -> G7"): Limpet's design was analysis-only
(`design/wq3806_limpet_architecture.md:240-245`); OFRAK (redballoonsecurity/ofrak) is the
"buy, don't build" fill for binary modify/repack.

## What this ships

- `ofrak_ops.py` — the real work, against the actual `ofrak` Python API (OFRAK has no CLI
  of its own; this module *is* the CLI surface). Verified against upstream
  `examples/ex1_simple_string_modification.py` and `examples/ex8_recursive_unpacking.py`
  in `redballoonsecurity/ofrak`, not guessed.
- `ofrak_cli.py` — the subprocess entry point five `cli_tools` entries invoke:
  `unpack`, `list-resources`, `patch-bytes`, `patch-strings`, `repack`.
- `selftest.py` — the `limpet-plugin.toml` `[health]` command: runs the full
  unpack -> patch-strings -> repack pipeline against the committed fixture and checks the
  repacked bytes actually changed. A probe that can fail, not a bare binary-exists check.
- `tests/test_ofrak_ops.py` — unit tests, `ofrak` monkeypatched. Run with no real package
  installed.
- `tests/test_ofrak_integration.py` — the real end-to-end test against the actual `ofrak`
  package and the committed fixture (`tests/fixtures/sample.tar`): unpack, patch a string in
  the tar member, repack, and assert the repacked bytes differ from the original in exactly
  one contiguous run (the patched region) and nowhere else. Skipped when `ofrak` isn't
  importable (see "Why four verbs, and why patch+repack share one OFRAK run" below).
- `limpet/examples/ofrak.toml` — the plugin manifest.
- `limpet/tool-catalog.json` row `ofrak` (`trust: community`, `license_risk:
  non-osi-commercial-restricted`).

## Licensing (read before enabling)

**This does not ship at `vetted` tier, unlike the ticket's original "buy, don't build,
vetted-tier plugin" framing assumed.** The reason is the actual OFRAK license, fetched from
`https://github.com/redballoonsecurity/ofrak/blob/master/LICENSE` rather than assumed from
upstream's capability description:

OFRAK ships under the **OFRAK Community License** (a custom EULA Red Balloon Security
requires clicking "I agree" to, not Apache/MIT/BSD/GPL — the PyPI page's "Proprietary" /
"Free For Home Use" classifiers are a real signal, not noise). It grants a license only for
Academic Purposes and **Non-Commercial Use**, and that grant explicitly **excludes "Shared
Use"**:

> "Shared Use" means any use of OFRAK where the person who set up a particular instance of
> OFRAK is not the same person interacting with that instance of OFRAK, or where a single
> instance of OFRAK is used by more than one person (whether on the same or different
> occasions).

Limpet's meta-server/registry model — one instance, invoked by whichever agent session calls
the tool — is exactly that. The license also excludes Commercial Use outright. The only lawful
path to Shared or Commercial Use is an **OFRAK Pro** or **OFRAK Enterprise** license: a paid,
named-"Authorized User" agreement with Red Balloon Security ("Licensee agrees to pay Red
Balloon all fees and other charges..."). That is a purchase, not a code change — queued as a
physical/business task for Frank, not a gate blocking this ticket (see TOOL_REGISTRY.md
"OFRAK" and ISSUE-LOG.md).

**Components covered**: the same license covers `ofrak`, `ofrak_components`, `ofrak_io`,
`ofrak_type`, `ofrak_patch_maker`, and the paid-backend integrations `ofrak_angr`,
`ofrak_binary_ninja`, `ofrak_ghidra` as one unit — none of them is separately permissively
licensed. This plugin's scope (unpack/list/patch-bytes/patch-strings/repack) only needs the
first four; no disassembler backend is required or shipped.

Until the Pro/Enterprise purchase happens, the `ofrak-*` cli_tools entries in
`ops/wq3808-limpet-mcp-meta-server-a1/manifest.example.json` carry `"enabled": false` with a
licensing-hold note, the same shape as that manifest's existing `rea` SECURITY HOLD entry.

## Installing a working `ofrak` (separate from licensing)

`pip install ofrak` fails to build on this host's default `python3` (3.14.4): ofrak resolves
to an aiohttp release whose vendored Cython extension (`_websocket.c`) targets a pre-3.14
`PyLongObject`/`_PyLong_AsByteArray` ABI that 3.14 changed (ISSUE-LOG.md). Use a 3.11/3.12
interpreter instead — `/home/lumen/.local/bin/python3.12` is already present on Lumen:

```sh
/home/lumen/.local/bin/python3.12 -m venv ops/wq3967-limpet-ofrak-a1/.venv
ops/wq3967-limpet-ofrak-a1/.venv/bin/pip install -r ops/wq3967-limpet-ofrak-a1/requirements.txt
```

## Why four CLI verbs, and why patch+repack share one OFRAK run

OFRAK's resource graph is an in-process object model with no documented cross-run
persistence API (checked `ofrak_core/src/ofrak/resource.py` upstream: no save/load-session
method). A CLI-wrapped tool is one subprocess per call, so `unpack`'s live tree cannot be
resumed by a later `patch-bytes`/`patch-strings`/`repack` call. The split that follows from
that constraint:

- `unpack` — real `unpack_recursively()`; writes `workdir/resources.json` (the resource
  listing) and `workdir/original.bin` (a pristine copy to re-derive from).
- `list-resources` — pure read of `resources.json`. No `ofrak` import.
- `patch-bytes` / `patch-strings` — re-opens `original.bin`, `unpack_recursively()` again
  (cheap and deterministic — this is not a missing feature, it's the only sound option),
  locates the target resource, applies `BinaryPatchModifier`, **`pack_recursively()` and
  flushes to `workdir/patched.bin` in the same run** (patch and repack need the same live
  tree, so they cannot be split further).
- `repack` — delivers `workdir/patched.bin` to the caller's requested output path with
  sha256 provenance. Pure file op, no `ofrak` import.

## Registering as a meta-server child

Already present (disabled) in `ops/wq3808-limpet-mcp-meta-server-a1/manifest.example.json`
as the `ofrak-unpack` / `ofrak-list-resources` / `ofrak-patch-bytes` / `ofrak-patch-strings` /
`ofrak-repack` cli_tools entries. Flipping `enabled` to `true` is the Frank-reviewed step
gated on the Pro/Enterprise purchase above, not a technical change.

## Tests

```sh
python3 -m pytest ops/wq3967-limpet-ofrak-a1/tests -q
```

`test_ofrak_ops.py` runs with no real `ofrak` installed (10 tests, `ofrak` monkeypatched).
`test_ofrak_integration.py` (2 tests) is skipped unless `ofrak` is importable; it is the real
unpack+patch+repack round trip against `tests/fixtures/sample.tar`.
