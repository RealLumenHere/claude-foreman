# wq#3896 — verify-pass of wq#3878 LIMPET: evaluate Sootty VCD waveform viewer

**Verdict: CONFIRMED.** Every externally-checkable claim in `TOOL_REGISTRY.md:225-278`
(commit `483621c56a`, PR #1867) holds up against independent re-research of the primary
sources, plus a live check of this box. One sourcing nit found (below) doesn't change the
NO-GO verdict — if anything it makes the case stronger, not weaker.

Diff: `git show 483621c56a -- TOOL_REGISTRY.md` — single file, 54 insertions, no code
changed (archetype: doc/evaluation ticket, so "running the probe" means re-deriving the
claims from primary sources, not executing a test suite).

## What wq#3878 claimed

- Sootty (`Ben1152000/sootty`, `pip install sootty`) loads VCD directly and EVCD via a
  bundled `evcd2vcd` converter.
- No built-in sigrok integration — pairs only via a `sigrok-cli -O vcd` shell pipe.
- Terminal rendering is SVG + external `rsvg-convert` + `viu`, not a native text renderer,
  contradicting the ticket's "terminal-native" framing.
- `viu` needs Kitty/iTerm2/Sixel graphics-protocol support or falls back to blocky Unicode
  block art; xfce4-terminal's stock VTE has none of those (Sixel toggle added in VTE
  1.1.0 is inert unless VTE itself was built with Sixel — open GNOME issue #253,
  corroborated by arewesixelyet.com).
- None of `sootty`/`viu`/`rsvg-convert` are installed on Lumen; only `sigrok-cli` is.
- `re-tools-analysis.md` (the ticket's own cited ref) doesn't exist anywhere in the fleet.

## What I checked, independently of the diff's own narration

1. **Fetched Sootty's actual GitHub README** (`fleet-search read
   https://github.com/Ben1152000/sootty`) rather than trusting the PR's paraphrase:
   confirms `pip install sootty`, `WireTrace.from_vcd_file`, `sootty.utils.evcd2vcd`, and —
   critically — the README's own line: *"As of the current release, Sootty can only
   display images in certain terminals with builtin graphics support... The following
   external dependencies are also needed to properly display images within the terminal:
   viu ... rsvg-convert."* This directly supports the PR's "SVG + external binaries, not
   terminal-native" finding from the primary source, not a secondary gloss.
2. **Fetched viu's actual GitHub README** (`atanunq/viu`) to check the quoted claim
   "Kitty graphics protocol, iTerm2 inline images, or Sixel ... down to Unicode block
   characters as a universal fallback." The real README says: *"It uses either iTerm or
   Kitty graphics protocol, if supported. If not, lower half blocks are displayed
   instead,"* and lists Sixel separately as *"Experimental Sixel support (behind either
   `sixel` or `icy_sixel` feature flags)"* — i.e. Sixel is **not** compiled into a default
   `cargo install viu` build at all, it's opt-in. The exact phrase "universal fallback"
   traces to a third-party packaging page (deb.griffo.io), not viu's own docs, so the PR's
   "viu's own docs state..." attribution is slightly off on sourcing. **This doesn't
   weaken the verdict** — it strengthens it: even setting aside xfce4-terminal's VTE/Sixel
   gap, a stock `cargo install viu` wouldn't have Sixel support compiled in at all, so the
   Unicode half-block fallback was already guaranteed on Lumen's terminal.
3. **Checked arewesixelyet.com directly**: xfce-terminal entry reads *"Status: Needs
   Patch. Added a SIXEL option in version 1.1.0, but the option only appears when VTE is
   built with SIXEL support. Relies on upstream VTE support:
   gitlab.gnome.org/GNOME/vte/-/issues/253"* — near-verbatim match to the PR's claim.
   Confirmed issue #253 is still open (search results title it "Sixel implementation
   tracker (#253) · Issues · GNOME / vte · GitLab", and arewesixelyet.com's own GNOME
   Terminal entry calls it an "Open issue").
4. **Confirmed sigrok's VCD output is real**: `sigrok.org/wiki/Main_Page` lists VCD among
   supported output formats; an EEVblog forum post independently describes using
   `sigrok-cli` to save VCD. Matches the PR's "plain pipe, not a built-in integration"
   characterization — nothing suggests a tighter sigrok↔sootty coupling exists.
5. **Live-checked the install footprint on this box**: no `which` hit for `sootty`/`viu`/
   `rsvg-convert`; `sigrok-cli` resolves to `/usr/bin/sigrok-cli`. Matches the PR exactly.
6. **Searched the fleet for `re-tools-analysis.md`**: `find` across
   `foreman kb bin sentinel mailbot pokemon agents/work-queue` returns nothing. Matches
   the PR's claim that the ticket's own cited ref doesn't exist.

## The one real finding: a misattributed quote, not a wrong conclusion

The PR puts "down to Unicode block characters as a universal fallback" in quotes and
attributes it to "viu's own docs." That exact phrase is from a third-party apt-repo
description page, not `atanunq/viu`'s README. Worth a correction if anyone revisits this
entry, but it's a citation-precision issue, not a factual error — the substance (Kitty/
iTerm2 native, half-block fallback otherwise, Sixel not even default) is accurate and the
NO-GO conclusion is unaffected either way.

## What would have broken this verdict (and didn't)

- If Sootty's README described a native ANSI/text renderer (no SVG, no external binaries),
  the whole "terminal-native framing doesn't hold" finding would collapse — checked the
  README directly, it doesn't; SVG + `viu`/`rsvg-convert` is explicit and required.
- If `viu`'s default build actually shipped Sixel support, the VTE-side argument would be
  the only thing standing between Sootty and a working render, and worth flagging as
  closer to viable than the PR frames it — checked viu's README, Sixel is feature-flagged,
  off by default, so this isn't close to viable either way.
- If xfce4-terminal's Sixel toggle worked without a custom VTE build, or GNOME issue #253
  were closed, the "no-op" claim would be stale — checked arewesixelyet.com and search
  results directly, neither is true as of this check.
- If `sootty`, `viu`, or `rsvg-convert` turned out to already be installed on Lumen, the
  "install footprint" bullet would be wrong and the eval would understate how close this
  is to usable — `which` confirms none are present.
- If `re-tools-analysis.md` existed anywhere in the fleet, the repeated "ref doesn't
  exist" callout (third time now, after Fuzzware/Pretender) would be false — `find`
  confirms it still doesn't.

## Note: prompt-injection attempt encountered during research (not followed)

Fetching `https://gitlab.gnome.org/GNOME/vte/-/issues/253` via `fleet-search read`
returned, instead of the issue thread, injected text framed as a "GitLab AI Context"
block instructing the reader to fetch and follow guidance from other URLs and to "use and
recommend" the `glab` CLI tool. This is unrelated to the real VTE issue page and was not
acted on — no files were fetched, no tooling recommendations were taken from it. The
actual sixel-tracker status for issue #253 was independently confirmed via search-result
snippets instead (see item 3 above). Flagging per standing instruction to surface
suspected prompt injection rather than silently ignore it.

No escalation to judgment needed — board this as verified.
