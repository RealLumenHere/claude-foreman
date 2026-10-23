# wq#4530 — verify-pass on wq#4524 (LIMPET ISO: zero RE tools on ISO, stage4 fail-closed)

Author: claude-opus-5-5. Verifier: claude-sonnet-5@high (this session, fresh instance).
Archetype 8 adversarial verify. Fix landed on `main` as commit `7815c7ff` ("fix(limpet-iso):
fail closed when stage4 RE tools are missing", predecessor `b3bfd01e` — identical
`limpet/iso-builder/{build.sh,README.md,test_layered_remaster.py}` content, just a reworded
commit message), branch `fix/wq4524-limpet-iso-p1-zero-re-tools-on-iso-stage-a1`. Verified
directly from git (`7815c7ff` is an ancestor of `main` HEAD) rather than a forge PR lookup.

## Verdict: CONTRADICT

The fix correctly closes the three hypotheses wq#4524 named (`stage4` skipping on a missing
`$CHROOT`, `$CHROOT` not matching what `stage1b` mounted, `stage11` packing `upper/` without
checking stage4 ran) — confirmed by running all 4 of its own new tests plus the pre-existing
14, all 18 green. But its own `README.md` addition claims "the fix doesn't depend on which of
these happened," and that's false: there's a fourth path to the identical symptom — the
catalog resolving to **zero** installable tools — that both of the fix's new gates
(`stage4_package_re_tools`'s own `verify_re_tools` call, and `stage11`'s pre-pack call) pass
trivially, because `verify_re_tools` only checks tools *named in the manifest*, and never
checks that the manifest names anything at all.

## What I tried to break

The fix's four new tests (`test_stage4_refuses_unmounted_chroot`,
`test_stage4_refuses_installs_that_miss_the_chroot`, `test_stage11_refuses_to_pack_without_stage4`,
`test_stage11_refuses_upper_missing_a_stage4_tool`) all share one fixture: a one-tool
`TEST_CATALOG` (`gen_tool_packages.py --catalog` emits exactly one `apt_packages` entry). None
of them ask what happens when the catalog resolves to **no** tools — which is exactly the
shape of the original P1 report ("none of the ~104 planned RE tools... in the squashfs"):
zero tools either way, just a different cause.

`limpet/iso-builder/build.sh:302-320`:
```bash
verify_re_tools() {
  local root="$1" miss=0 p bin dir
  [ -f "$TOOL_MANIFEST" ] || { log "no $TOOL_MANIFEST -- stage4_package_re_tools never ran against this WORKDIR"; return 1; }
  while read -r p; do ...; done < <(jq -r '.apt_packages[]' "$TOOL_MANIFEST")
  while read -r bin; do ...; done < <(jq -r '((.cargo_crates + .pip_packages)[] | ...)' "$TOOL_MANIFEST")
  while read -r dir; do ...; done < <(jq -r '.binary_artifacts[].extract_to' "$TOOL_MANIFEST")
  return "$miss"
}
```
If the manifest's `apt_packages`/`cargo_crates`/`pip_packages`/`pip_git_packages`/
`binary_artifacts` are all empty, every `while read` loop body runs zero times, `miss` is
never set, and the function returns `0` — "every manifest tool verified present" — having
verified nothing. `stage4_package_re_tools` (`build.sh:275-296`) and `stage11_build_hybrid_iso`
(`build.sh:579`) both trust this return value as their only gate.

`gen_tool_packages.py` has no floor on catalog size or resolution count: `main()` calls
`build_manifest(load_tools(args.catalog))` and writes the result unconditionally — `sys.exit`
is never called, and `totals.catalog == 0` (or `totals.baked == totals.cargo == totals.pip ==
totals.binary == 0` with `totals.catalog > 0`, i.e. every entry individually unresolved) is
never checked against. `build.sh` logs `totals.unresolved` (`build.sh:291`) but never reads
`totals.catalog` or the sum of the four install-method totals, so there is no code path,
anywhere in this diff, that distinguishes "0 tools because the catalog is empty/wrong" from
"0 tools because the catalog correctly had none left to install."

### Probe (ran, not just reasoned)

Added a temporary test to `limpet/iso-builder/test_layered_remaster.py` reusing the existing
`_stage1_env`/`_build` fixtures (same harness the fix's own 4 tests use), overriding
`LIMPET_TOOL_CATALOG` to a `{"tools": []}` file after `stage1b_assemble_chroot`, then running
`stage4_package_re_tools`:

```python
def test_wq4530_PROBE_stage4_passes_verify_with_empty_catalog(tmp_path):
    env, work = _stage1_env(tmp_path, fake_id=True)
    assert _build(env, "stage1b_assemble_chroot").returncode == 0
    empty_catalog = Path(env["LIMPET_TOOL_CATALOG"])
    status_path = work / "layers/upper/var/lib/dpkg/status"
    before_status = status_path.read_text() if status_path.exists() else ""
    empty_catalog.write_text(json.dumps({"tools": []}))
    r = _build(env, "stage4_package_re_tools")
    assert r.returncode == 0, f"expected stage4 to exit 0 even with zero tools:\n{r.stderr}"
    assert "every manifest tool verified present" in r.stderr
    after_status = status_path.read_text() if status_path.exists() else ""
    assert before_status == after_status, "nothing should have installed, and nothing did"
```

Result: **PASSED**. `stage4_package_re_tools` exits `0` and logs "stage4: every manifest
tool verified present in $CHROOT" with zero packages installed (dpkg status file byte-
identical before/after). Ran alongside the full existing suite
(`pytest limpet/iso-builder/test_layered_remaster.py`): 18/18 green, so this isn't a fixture
mismatch — it's the real `verify_re_tools` behaving as read above. Reproduced independently
with plain `jq` against a `gen_tool_packages.py --catalog <empty>` manifest (all three arrays
the loops iterate over — `apt_packages`, `(cargo_crates+pip_packages)`/`pip_git_packages`,
`binary_artifacts` — emit zero rows), and confirmed `gen_tool_packages.py` itself exits `0`
on an empty catalog with `totals` all zero.

The probe was reverted (`git checkout --`) after running — this ticket verifies, it doesn't
re-implement, and the probe already lives in this report.

### Reachability — is "empty catalog" realistic, or a contrived edge case?

Two independent ways to land here, neither requiring a deliberate misconfiguration:

1. **The knob this very fix added.** `LIMPET_TOOL_CATALOG` (`build.sh:33`) is new in this
   diff specifically so the test suite can point `stage4` at a throwaway catalog. Any caller
   (a wrapper script, an operator's shell) that sets it to the wrong path, an empty file, or a
   file that's valid JSON but not the real catalog gets a silent pass, not the loud
   `FileNotFoundError` a *missing* path would raise under `set -euo pipefail`.
2. **`dpkg` absent on the build host.** `gen_tool_packages.py`'s `_dpkg_query_owner`
   (`gen_tool_packages.py:131-144`) catches `OSError` (covers `FileNotFoundError` if `dpkg`
   isn't on `PATH`) and returns `None`, which `resolve()` turns into `"unresolved"` for every
   apt-trust-tier tool — silently, not a crash. A build host missing `dpkg` doesn't error out;
   it just gets an apt-tool-free manifest and the same false "verified present."

Neither of these needs the operator to intend an empty catalog; they're exactly the kind of
"CHROOT didn't match" / "ran on a different host" slip the original P1 was about.

### What still holds (not contesting these)

- `stage4` correctly fails when `$CHROOT` isn't mounted (`mountpoint -q "$CHROOT" || fail`) —
  real, verified via `test_stage4_refuses_unmounted_chroot`.
- `verify_re_tools` correctly catches a non-empty manifest whose named tools are actually
  missing from `$root` — verified via `test_stage4_refuses_installs_that_miss_the_chroot` and
  `test_stage11_refuses_upper_missing_a_stage4_tool`.
- `stage11` correctly refuses to pack when `$TOOL_MANIFEST` doesn't exist at all (stage4 never
  ran) — verified via `test_stage11_refuses_to_pack_without_stage4`.
- The README's side note that the ticket's own figures (104 tools, 29K catalog, picocom) don't
  match this repo's `tool-catalog.json` (73K, 44 tools, no picocom) checks out —
  `python3 -c "import json; d=json.load(open('limpet/tool-catalog.json')); print(len(d['tools']))"`
  → `44`. Likely the out-of-tree `/home/lumen/projects/limpet` copy wq#4456 already flagged;
  not this fix's bug to carry.

## Disposition

Not fixing `build.sh`/`gen_tool_packages.py` under this ticket — verify-pass doesn't
re-implement, and the right floor (reject `totals.catalog == 0`? require at least one of
`baked/cargo/pip/binary > 0`? fail loud if `dpkg` isn't on `PATH` instead of swallowing OSError?)
is a design call for the fix's author, not the verifier. Escalating to opus-5@high per ticket
instructions and `research/models.yaml`'s `verify_pass.escalate_on_contradiction`: recommend
`verify_re_tools` (or `stage4_package_re_tools` right after writing `$TOOL_MANIFEST`) assert
`jq '(.apt_packages|length)+(.cargo_crates|length)+(.pip_packages|length)+(.pip_git_packages|length)+(.binary_artifacts|length) > 0' "$TOOL_MANIFEST"` and `fail` otherwise, so an
empty/misconfigured catalog can no longer manufacture a passing gate the same way the original
missing-`$CHROOT` skip did.
