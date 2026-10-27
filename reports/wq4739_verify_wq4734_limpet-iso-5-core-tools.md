# wq#4739 — verify-pass on wq#4734: LIMPET ISO bakes 5 core RE tools, fail stage4 on unresolved core tool

Archetype 8 adversarial verify. Author: claude-sonnet-5. Verifier: claude-sonnet-5@high
(fresh instance). Verdict: **CONFIRMED** — no contradiction, no escalation.

Diff under test: commit `8dc651d4` on `feat/wq4739-verify-wq-4734-limpet-iso-5-core-tools-n-a1`,
touching `limpet/iso-builder/build.sh`, `limpet/iso-builder/gen_tool_packages.py`,
`limpet/iso-builder/test_gen_tool_packages.py`, `limpet/tool-catalog.json`.

## What wq#4734 claimed
5 RE tools (hackrf, jadx, apktool, scrcpy, searchsploit) never reached the ISO despite
being catalogued at the "core" trust_tier:
- hackrf/apktool/scrcpy: `trust` field said `community` while `trust_tier` said `core`;
  `gen_tool_packages.py`'s `BAKE_TIERS = ("core", "vetted")` gate reads `trust`, not
  `trust_tier`, so the mismatch silently excluded all three despite their apt packages
  being verified present. Fix: promote `trust` to `core` on these three catalog entries.
- jadx: already `trust=vetted` (in `BAKE_TIERS`), but had no `BINARY_RECIPES` entry at
  all — unresolved for a different reason (missing recipe, not trust). Fix: add a
  `jadx-1.5.6.zip` binary recipe, and fix a zip-unwrap bug that assumed every binary
  release zip has exactly one top-level wrapper directory (jadx's doesn't).
- searchsploit: apt's `exploitdb` package is Kali-only, absent on Ubuntu 26.04. Fix: a
  new `source-build` install method that fetches a sha256-pinned GitLab commit archive.
- Also: `verify_re_tools` previously only checked tools that made it into the manifest's
  per-method install sections; a tool `gen_tool_packages.py` reported fully `unresolved`
  never failed the build. Fix: hard-fail stage4 if any `unresolved` entry has
  `trust == "core"`.

## What I verified independently
1. **Trust-field diff is exactly as described.** `git show 8dc651d4 -- limpet/tool-catalog.json`
   touches only the `trust` field (community→core) + provenance prose on exactly 4 entries
   (hackrf, apktool, scrcpy, searchsploit); `trust_tier` itself is untouched by the diff
   (only referenced in prose) — confirmed with `grep '^[+-].*trust_tier'` on the diff, 0 hits
   on an actual `+`/`-` line. jadx's `trust` field is unchanged (stays `vetted`, already in
   `BAKE_TIERS`), consistent with its bug being "no recipe," not "wrong trust."
2. **Both pinned hashes verified bit-for-bit against live upstream**, not just checked for
   hex-string shape:
   - `curl` the real `jadx-1.5.6.zip` from `github.com/skylot/jadx` release v1.5.6 →
     `sha256sum` = `545ea2be9c...e8974`, exact match to `BINARY_RECIPES`.
   - `curl` GitLab's `-/archive/<full-40-hex-commit>/exploitdb-<commit>.tar.gz` for the
     exact commit and archive_name the catalog/code use → `sha256sum` =
     `370b8e481e...d36be`, exact match to `SOURCE_BUILD_RECIPES`. (First attempt using the
     *abbreviated* commit in the URL produced a different hash — GitLab's archive naming
     embeds the ref string verbatim, so a short vs. full ref changes the tarball bytes.
     Re-ran with the exact 40-hex commit the catalog/code actually pass and it matched;
     this was my own test error, not a defect in the diff — `stage4e_build_source_tools`
     always uses the full commit from the catalog, never a shortened one.)
   - Re-fetched the GitLab archive a second time: byte-identical (`cmp` clean), so the
     pin is reproducible, not flaky.
3. **jadx zip structure matches the comment's claim.** `python3 zipfile` on the real
   downloaded zip: top-level entries are `LICENSE`, `README.md`, `bin/`, `lib/` — 4
   entries, 2 directories, no single wrapper dir. The new `build.sh` logic
   (`top_entries -eq 1 && top_dirs -eq 1` → unwrap one level, else copy flat) takes the
   "copy flat" branch for this exact shape, which is correct.
4. **searchsploit archive contains the claimed binary.** `tarfile` on the verified
   download: after `--strip-components=1`, `searchsploit` exists at the archive root —
   matches `bin_src: "searchsploit"`.
5. **Full test suite, re-run cold on the worktree:** `pytest limpet/iso-builder/` →
   **173 passed**, 0 failed, including all 11 new/changed tests for this diff
   (`test_gen_tool_packages.py`: source-build resolution, commit-pin validation, recipe
   gating, manifest shape, the real-catalog check across all 5 tools, and the
   unresolved/unresolved_core split).
6. **Manifest generator run against the real production catalog** (not a fixture):
   `python3 gen_tool_packages.py --out manifest.json` →
   `totals = {baked:18, binary:2, cargo:1, pip:4, source_build:1, unresolved:22, unresolved_core:0}`.
   `binary_artifacts` = `[ghidra..., jadx-1.5.6.zip]`, `source_build_tools` = `[searchsploit]`,
   apt baked set includes `hackrf`/`apktool`/`scrcpy`. All 5 claimed tools resolve by the
   claimed method, on the real catalog, with `unresolved_core == 0`.
7. **Probed `verify_re_tools`'s new gate directly** (sourced `build.sh`, called the
   function with hand-built manifests, not through the PR's own tests):
   - Manifest with one `unresolved` entry at `trust: "core"` → `verify_re_tools` returns 1
     and logs `core-tier RE tool(s) unresolved at stage4 verify time (1; ...)`.
   - Same shape but `trust: "community"` → returns 0, no log line. Confirms the gate is
     scoped to `trust == "core"` exactly as coded, and doesn't false-positive on the
     (expected, by-design) community backlog.
8. **Probed `stage4e_build_source_tools` directly** (sourced `build.sh`, pointed `repo` at
   a `file://` fixture so no live network dependency in the probe itself):
   - Wrong `sha256` pinned → `fail`s with "sha256 mismatch for searchsploit source
     archive: got ... want ..." *before* anything lands under `$CHROOT` (chroot dir stayed
     empty). Fail-closed, as claimed.
   - Correct `sha256` → archive fetched, verified, extracted with `--strip-components=1`
     to the right path; the only thing that didn't complete was the final `chroot ...
     chmod/ln` step, which needs real `CAP_SYS_CHROOT` this sandbox doesn't grant — an
     environment limitation of my probe, not a code defect (the extracted binary was
     present on disk at the right path before that point).

## Things I tried to break (did not pan out as defects in this diff)
- **The new "fail stage4 on unresolved core tool" gate only protects `trust == "core"`,
  not the broader `BAKE_TIERS` set (`core` **or** `vetted`).** jadx itself is `trust:
  vetted` / `trust_tier: core` — if jadx's `BINARY_RECIPES` entry were ever lost again in
  a future edit, this new gate would **not** catch it (it would silently fall back to the
  pre-wq#4734 behavior of just logging an unresolved count), because jadx's `trust` field
  is `vetted`, not `core`. Checked the full catalog: 7 other entries besides the 5 in
  scope here have the same `trust=community` vs `trust_tier=vetted` mismatch shape
  (`cutter`, `sootty`, `netzob`, `rea`, `ghidra-mcp`, `pyamlboot`, `sunxi-fel`) and are
  left unpromoted — also not protected by the new gate if any of them were later promoted
  via `trust_tier` alone. This is a real scope boundary, but it matches the ticket's own
  framing ("fail stage4 on unresolved **core** tool", singular tier) and the catalog's
  documented `trust_tiers` doctrine, which treats `core` as the only "proven, hard
  requirement" tier and `vetted` as a softer bar — so it reads as an intentional,
  documented scope choice, not a bug in what was asked. Flagging as a residual gap for a
  future ticket, not a contradiction of this one.
- **Pre-existing `trust`/`trust_tier` mismatches outside the named 5** (the 7 tools listed
  above, plus several `deferred`-tier ones) are untouched by this diff. Confirmed this is
  deliberate scope (ticket title says "5 core tools," commit touches exactly 4 `trust`
  values), not an omission — those tools' `trust_tier` is `vetted`/`deferred`, a lower bar
  than `core`, so they were never claimed to be in scope.
- **`jq`'s `(.source_build_tools // [])` defensive fallback** in `verify_re_tools` matters
  only for hand-crafted/legacy fixture manifests (like the pre-existing symlink test's
  fixture, which omits the key); every manifest `build.sh` actually generates at stage4
  time comes fresh from `gen_tool_packages.py`, which always emits `source_build_tools`
  (possibly `[]`) — so there's no real-world path where the key is missing and the
  fallback silently hides a bug.

## Verdict
CONFIRMED. Both newly-pinned artifact hashes verify bit-for-bit against live upstream for
the exact commit/ref the code uses, the claimed zip/tar structures match reality, the
full `limpet/iso-builder` test suite passes (173/173) on the worktree, the manifest
generator resolves all 5 named tools by the claimed method against the real production
catalog with `unresolved_core == 0`, and direct probes of both new code paths
(`verify_re_tools`'s core-unresolved gate, `stage4e_build_source_tools`'s fail-closed sha
check) behave exactly as the diff claims, including the negative case (non-core
unresolved tools don't trip the gate) and the fail-before-write case (bad sha256 never
touches `$CHROOT`). No escalation to opus-5@high.
