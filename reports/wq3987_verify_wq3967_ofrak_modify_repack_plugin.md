# wq#3987 — verify-pass of wq#3967 (LIMPET BUILD: OFRAK modify/repack plugin, wq#3944/A7)

PR #1936 (http://100.121.17.5:3000/lumen/claude-foreman/pulls/1936), branch
`feat/wq3967-limpet-build-ofrak-modify-repack-plugin--a1`, one commit `856adce1`.
Author model: claude-sonnet-5. Verifier: claude-sonnet-5@high (fresh instance).
Verdict: **CONFIRM** — no contradiction, no escalation to opus-5@high.

## Scope read cold

`git diff main..branch --stat` initially showed large unrelated deletions
(`limpet/critic.py` -161, `docs/agent-security-spec.md` -269,
`ops/wq4006-rea-security-rescope-a1/*` -300ish) — this is divergence noise from the
branch's base predating several since-merged main commits, not part of the PR. Re-diffed
against `git merge-base main branch` (`2a3c8a96`) to get the true scope: **14 files,
+1005/-5**, all additive — `TOOL_REGISTRY.md` (+43), `limpet/tool-catalog.json`
(+55/-5, new `ofrak` row), `limpet/examples/ofrak.toml` (new, manifest),
`limpet/test_limpet_ofrak_plugin.py` (new, registry-load test), and
`ops/wq3967-limpet-ofrak-a1/` (new dir: `ofrak_ops.py`, `ofrak_cli.py`, `selftest.py`,
`install.sh`, `README.md`, `requirements.txt`, tests + a committed `sample.tar` fixture).
Flagging this for whoever reads this report next: a plain `main..branch` diff is the
wrong tool once a long-lived branch has fallen behind an actively-moving main; use
`merge-base` first.

Claims: ships OFRAK unpack/list-resources/patch-bytes/patch-strings/repack at
**community tier** (not `vetted`, contrary to the original ticket's framing), because
OFRAK's real Community License excludes "Shared Use" — exactly how Limpet's shared
meta-server works. `cli_tools` entries added to the meta-server's
`manifest.example.json` with `enabled: false` pending an OFRAK Pro/Enterprise purchase
(queued as a physical task for Frank). 10 unit tests (OFRAK mocked) pass; 2 real-`ofrak`
integration tests skip in this sandbox (Python 3.14, `ofrak`'s pinned aiohttp Cython
extension doesn't build against 3.14's C API). ISSUE-LOG rows 1339–1341 cited.

## What I ran

1. **Reproduced every test claim exactly**, after `git checkout <branch> -- <paths>`
   into my worktree (reverted afterward, worktree left clean):
   - `python3 -m pytest ops/wq3967-limpet-ofrak-a1/tests -q` → **10 passed, 2 skipped**
     — byte-for-byte the author's claimed count. The 2 skips are gated by
     `pytestmark = pytest.mark.skipif(not _OFRAK_AVAILABLE, ...)`, not silently
     disabled or deleted.
   - `python3 -m pytest limpet/test_limpet_ofrak_plugin.py -q` → 1 passed (registry-load).
   - `python3 -m pytest limpet/test_limpet_manifest.py -q` (run from `limpet/`, a
     pre-existing sys.path quirk unrelated to this PR — fails on rootdir-relative
     import when invoked from repo root) → 24 passed, no regression.
   - `python3 -m pytest limpet/registry -q` → 25 passed (matches wq#3959's own verify
     baseline), no regression from the new catalog row.
   - `python3 -m pytest ops/wq3808-limpet-mcp-meta-server-a1/tests -q` (the meta-server
     whose `manifest.example.json` this PR edits) → 39 passed, no regression from the
     5 new `enabled: false` `cli_tools` entries.
   - `limpet/tool-catalog.json` parses as valid JSON; the `ofrak` row matches the
     README/TOOL_REGISTRY claims field-for-field (`trust: community`,
     `license_risk: non-osi-commercial-restricted`, `redistribution_posture:
     external-invoke`, health_check wired to `selftest.py`).
   - `manifest.example.json`'s 5 new `ofrak-*` entries are all `enabled: false` with an
     identical "LICENSING HOLD... do not flip to true without that purchase" note —
     matches the done-notes and TOOL_REGISTRY.md claim exactly; not silently left
     enabled.

2. **Independently re-verified the licensing claim against the primary source** (the
   single highest-leverage factual claim in the PR — it's the reason the ticket's
   original "vetted-tier" framing was overridden). `curl`'d
   `raw.githubusercontent.com/redballoonsecurity/ofrak/master/LICENSE` directly (not
   trusting the PyPI classifier either): confirmed verbatim —
   §1.6 *"'Shared Use' means any use of OFRAK where the person who set up a particular
   instance of OFRAK is not the same person interacting with that instance... or where a
   single instance of OFRAK is used by more than one person"*, and §2's grant excludes
   all Commercial Use and all Shared Use outside an Academic Purpose. This is exactly
   Limpet's meta-server model (one shared instance, driven by whichever agent session
   calls it) and exactly the posture the PR applies. Not fabricated, not a stretch.

3. **Independently verified the API-shape claim against real upstream OFRAK** — the
   failure mode this archetype is built to catch is a plausible-looking wrapper around
   an API that doesn't actually exist that way, backstopped only by a mock that
   enshrines the same wrong shape. Fetched both cited examples
   (`examples/ex1_simple_string_modification.py`, `examples/ex8_recursive_unpacking.py`)
   and `ofrak_core/src/ofrak/resource.py` directly from
   `github.com/redballoonsecurity/ofrak`:
   - ex1 confirms `OFRAK()` / `.run(main, ...)` /
     `ofrak_context.create_root_resource_from_file(path)` /
     `resource.get_data()` / `data.find(bytes)` /
     `BinaryPatchConfig(offset, data)` / `resource.run(BinaryPatchModifier, config)` /
     `resource.flush_data_to_disk(path)` — the exact call sequence `_patch()` uses.
   - ex8 confirms `unpack_recursively()` / `pack_recursively()` /
     `flush_data_to_disk()` — the exact calls `unpack()` uses.
   - `resource.py` confirms every remaining method the PR calls is real with a matching
     signature: `get_id()` (L117), `get_caption()` (L153), `get_data()` (L170),
     `get_data_range_within_root()` (L236), `get_tags(inherit=True)` (L887, PR calls
     `inherit=False`, a valid override), `get_descendants(max_depth=-1, ...)` (L1213,
     PR calls with no args, which is the documented "no maximum depth" default).
   No drift between the wrapper and the real library. The mocked unit tests are not
   rubber-stamping a fictitious API — the fake they drive (`FakeResource.run()` does a
   real `self._data[:offset] + patch_bytes + self._data[offset+len(patch_bytes):]`
   splice and `flush_data_to_disk` writes real bytes) matches this real shape.

4. **Reproduced the author's sandbox-constraint claim rather than trusting it**
   (instinct 8: an unverified claim of "can't run X" is exactly the kind of thing to
   check, not assume). `/home/lumen/.local/bin/python3.12 --version` → `This command
   requires approval` in my session too, same allowlist shape ISSUE-LOG #1340 describes
   ("python3.12 ... required approval"). This is a real, reproducible constraint in this
   worker sandbox, not an excuse invented by the author. Per the rules this is a
   non-retryable refusal shape (not an allowlist gap to route around), so I stopped
   there rather than burning attempts on a workaround — consistent with how wq#3973's
   verify treated the author's "search is still down" claim (reproduced live, not
   assumed).

## Attempted to break it

- Checked the one logic path with **zero test coverage**: `_patch()`'s
  `resource_id is not None` branch (patching a specific descendant resource rather than
  root). All 10 unit tests and both integration tests call `patch_bytes`/`patch_strings`
  with `resource_id=None`. Read the code: it loops `root.get_descendants()` for an
  `id().hex()` match, then runs the patch against that descendant resource directly —
  the same `target.run(Modifier, config)` / `root.pack_recursively()` shape as the
  root-only path, and consistent with how OFRAK expects a nested patch to be applied
  (per `resource.py`, `run()` is a method on any `Resource`, not root-specific). This is
  a plausible, correctly-shaped implementation but it is **genuinely untested** — a real
  gap, not a contradiction, since nothing in the diff claims otherwise.
- Checked `selftest.py`'s own health-check pipeline against its docstring's claim
  ("checks the repacked bytes differ only in the patched region"): the actual check is
  only `len(original) == len(repacked)` and `sha256 != original_sha256` — it does **not**
  verify contiguity of the diff region, despite the docstring implying it does. The
  *real* contiguity check (diff region is exactly one contiguous run, boundaries
  untouched) lives in `tests/test_ofrak_integration.py`
  (`test_unpack_patch_repack_round_trip_changes_only_patched_bytes_real_ofrak`), which is
  the artifact the ticket's AC actually points at ("an end-to-end test ... the repacked
  hash differs only where patched"). So the AC is met by the right test; `selftest.py`'s
  docstring is just slightly oversold relative to what `selftest.py` itself checks. Minor
  doc/implementation mismatch, not a contradiction of the PR's or ticket's claims.
- Checked for a dropped/weakened AC: `test_limpet_manifest` passes (24/24), registry
  load test passes, end-to-end test exists and is logically sound (verified above) even
  though it's skipped here. No AC silently dropped.
- Checked the `ops/wq3942.../tests/test_limpet_update.py` and
  `ops/wq4006.../HOSTILE_INPUT_POLICY.md` deletions that showed up in the naive
  `main..branch` diff actually exist, unmodified, on current `main` (confirmed via
  `git show main:<path>` for both) — proving they were never part of this PR's commit,
  just base-divergence artifacts as suspected in step 1, not content this PR silently
  destroyed.

## Conclusion

Every test the author claims passing reproduces exactly (10+1+24+25+39, all matching).
The two claims most likely to be wrong or fabricated under this archetype — the license
exclusion that justifies demoting the plugin from the ticket's assumed `vetted` tier to
`community`, and the OFRAK API shape the whole implementation rests on — both check out
byte-for-byte against primary sources (OFRAK's actual `LICENSE` file and actual
`resource.py`/examples), not just against the author's own citations. The sandboxed
Python-3.14 test-skip and the `python3.12`-allowlist blocker are both independently
reproducible, not excuses. The only real gaps found (resource_id-targeted patch path
untested; `selftest.py` docstring overstates its own check) are coverage/doc notes, not
defects that break the shipped behavior or contradict a claim in the PR. **CONFIRM.**
