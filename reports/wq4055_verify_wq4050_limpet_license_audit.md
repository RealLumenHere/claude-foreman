# wq#4055 — verify-pass on wq#4050 (limpet: license-audit the 41 LEGACY_UNAUDITED catalog entries)

PR: `claude-foreman#2004`, commit `6cc57385` on this branch (3 files, 172
insertions / 73 deletions: `limpet/license_policy.py`,
`limpet/test_license_policy.py`, `limpet/tool-catalog.json`). Author:
claude-sonnet-5 · Verifier: claude-sonnet-5@high (fresh instance, wq#4055).

## Claim under test

wq#4050 claims to have license-audited all 41 names in the `LEGACY_UNAUDITED`
ratchet baseline frozen by wq#4048, given each one real `license_risk` /
`redistribution_posture` values backed by a cited source (GitHub repo-license
API / fetched LICENSE file / on-disk doc), shrunk `LEGACY_UNAUDITED` to
`frozenset()`, and added tests (`test_live_catalog_has_no_policy_violations`,
a 7-tool GPL parametrize, a cleared-backlog assertion, and a monkeypatched
exemption test replacing the one that used to assert against a real legacy
name).

## What I checked

1. **Read the diff cold** (`git show 6cc57385`). `license_policy.py`: only the
   `LEGACY_UNAUDITED` definition changes, `frozenset({...41 names...})` →
   `frozenset()`; `check_entry`/`check_catalog`/`is_bundleable` untouched.
   `test_license_policy.py`: adds the parametrize + two new tests, replaces
   the old binwalk-against-the-real-catalog exemption test with a
   `monkeypatch.setattr(lp, "LEGACY_UNAUDITED", {...})` version — correct,
   since there's no real legacy name left to exercise that branch against.
   `tool-catalog.json`: metadata-only — total tool count is 44 before and
   after (`git show e109da65:limpet/tool-catalog.json` vs HEAD), so no rows
   were added/removed/renamed, only `license_risk`/`redistribution_posture`
   (+ `provenance` text, + one `license` correction for `fwtriage`) added.

2. **Ran the probe.** `python3 -I limpet/license_policy.py` →
   `0 license-policy violation(s)`. `python3 -I -m pytest
   limpet/test_license_policy.py -q` → 25 passed.
   `python3 -I -m pytest limpet/iso-builder/ -q` (downstream consumer of
   `is_bundleable`) → 17 passed, unaffected by the data change.

3. **Checked every one of the 41 names programmatically** (wrote a one-off
   script listing name/license/risk/posture for all 41 against the live
   catalog): all 41 now carry both fields, none missing. Extended the check
   to the *whole* catalog (44 tools, not just the 41) — zero entries anywhere
   lack the fields, which is a stronger result than the ticket asked for: the
   ratchet now covers 100% of the catalog, not just "no new violations."

4. **Tried to break it: is the audit real or fabricated?** This is the live
   wire for this class of PR — a plausible-looking `license_risk` value with
   an invented-sounding "GitHub's repo-license API" citation is exactly
   instinct 8's fabrication pattern. I had live network access in this
   sandbox (confirmed: `curl` to `raw.githubusercontent.com` works;
   `api.github.com` is rate-limited but that's a quota issue, not a block).
   Independently re-fetched and compared against the diff's cited reasoning
   for 8 of the 41, spanning every risk category the audit used:
   - `fwtriage` (license *changed* from `unverified`→`MIT`, the riskiest kind
     of edit to get wrong): `raw.../taffy210/fw-triage/.../LICENSE` is
     verbatim MIT. Matches.
   - `radare2`: diff quotes `COPYING.md` verbatim ("Most of radare2 is
     licensed under the LGPLv3... skip with `--without-gpl`"). Fetched the
     same file — exact match, not paraphrased.
   - `frida`: diff claims license field `wxWindows`, classified copyleft.
     Fetched `frida/frida/main/COPYING` — genuinely the wxWindows Library
     Licence (LGPL-derived with a static-linking exception). Confirmed.
   - `heimdall`: MIT confirmed against `Benjamin-Dobell/Heimdall/LICENSE`.
   - `sunxi-fel`: GPL-2.0 confirmed against
     `linux-sunxi/sunxi-tools/.../LICENSE.md` (GPLv2 text, README's own
     license badge also says GPL).
   - `fiedka`: diff resolves a prior `url=null`/`license=unverified` stub to
     `github.com/fiedka/fiedka`, MIT. Confirmed both the repo's existence and
     its MIT LICENSE file match.
   - `llm4decompile`: diff's most legally substantive claim — wrapper is MIT
     but model weights ship under a separate non-OSI "DeepSeek License
     Agreement v1.0" (`LICENSE-MODEL`), hence
     `non-osi-commercial-restricted`/`external-invoke`. Fetched
     `LICENSE-MODEL` from the same repo — it is exactly that license,
     verbatim preamble match. The two-license-files-in-one-repo nuance is
     correctly caught, not flattened into a single verdict.
   - `glitchkit`: the one entry *kept* `unverified` rather than guessed. Diff
     says a GitHub search for the exact name surfaced no matching
     fault-injection project. Independently ran `fleet-search web "glitchkit
     fault injection github"` myself — also no project named `glitchkit`
     among the results. Correctly left unverified instead of fabricating a
     repo.
   Every citation I spot-checked across MIT/Apache/GPL/LGPL/wxWindows/
   non-OSI/unverified categories was real, specific, and accurate — not
   generic plausible-sounding text. This is a genuinely performed audit.

5. **Checked the GPL-bundling test's "source offer" assertion isn't a
   tautology.** `test_gpl_bundling_tools_state_a_source_offer_obligation`
   asserts `"source offer" in entry["provenance"]` for 7 tools. Read the
   actual provenance strings (not just grep'd the substring) for `radare2`
   and `sunxi-fel` — the phrase appears in a load-bearing sentence ("Bundling
   permitted with a written source offer" / "copyleft permits bundling... not
   a bundling blocker"), not stuffed in to satisfy the test mechanically.

## Verdict

**CONFIRM.** The catalog-count invariant holds (44/44, no silent
additions/removals), the probe (`license_policy.py` + both test files)
reproduces 0 violations / 25 passed, the downstream ISO-builder consumer
still passes (17/17) against the new data, and spot-checking 8 of the 41
citations against live upstream sources — deliberately choosing the riskiest
ones (a changed `license` value, a multi-license-file nuance, the one entry
left `unverified`) — found every claim accurate. Nothing rises to a
contradiction under the ticket's judgment-trigger bar.

## Non-blocking observations (not part of the verdict)

- **Stale module docstring**: `limpet/license_policy.py:11-15` still reads
  "the 41 entries that predate the policy are frozen in `LEGACY_UNAUDITED`
  and exempted, so the backlog can be audited on its own ticket" — that
  backlog is now empty (this PR cleared it), so the docstring describes a
  state that no longer exists. The inline comment immediately above the
  `LEGACY_UNAUDITED` assignment (lines 36-39) *was* updated to say this; the
  module-level docstring a few lines up was not. Cosmetic only — no behavior
  depends on the docstring text. Trivial fix: update or drop that sentence.
- **Stale/inaccurate "fleet-search down" citation**: the `glitchkit`
  provenance says "fleet-search web remains down (wq#2595/2599/2601)". I ran
  `fleet-search web` live during this verify pass and it returned `ok: true`
  (three engines suspended, others answering normally) — not down. The
  underlying *decision* (leave `glitchkit` unverified) is still correct, since
  my own live search also found no matching project, but the stated reason
  ("search tooling unavailable") is outdated/inaccurate; the real reason is
  "no matching repo exists," which the same provenance string also says.
