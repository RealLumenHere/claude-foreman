# wq#3990 — judgment: wq#3985 vs wq#3983 (Xubuntu seed package-presence claims)

**Ruling, both questions, up front.**

1. **Not an isolated grep slip, and not a doc-wide pattern — a bounded failure
   class of three sentences.** The predictor of error is not "a grep ran against
   the wrong file"; it is "the sentence encodes a prior belief about what the
   Xubuntu flavor does by default." Every claim of that shape in the doc is
   wrong (3 of 3). Every package-presence claim *without* that shape re-verified
   correct (9 of 9), on top of the ~30 version/presence claims wq#3985 already
   sampled clean.
2. **Fast-follow correction in place, no reopen of anything.** wq#3983 (PR
   #1930), wq#3946 (#1907) and wq#3947 (#1909) are all merged and all three
   stay merged: no shipped code carries the error — the landed ISO builder
   independently got both facts *right*, against the doc. The correction is a
   doc patch, applied on this branch, which is the whole remediation. Precedent:
   `kb/ISSUE-LOG.md` row 142 / wq#1352 — land a doc defect as a fast-follow
   patch, do not reopen a landed ticket to add prose.

## The three wrong sentences

Verified against the two manifests committed by wq#3983, pinned by blob:
`research/wq3983_xubuntu-26.10-snapshot4-desktop-amd64.manifest` (blob
`40edfefc`, 1,920 lines) and `…26.04.1-desktop-amd64.manifest` (blob
`4c97d3a6`, 1,916 lines), both added by commit `a0a0465f`.

| § | Doc claimed | Manifest says |
|---|---|---|
| 3 | "grepped the manifest for `whisker`, zero hits" — Limpet must add it | `xfce4-whiskermenu-plugin` 2.10.1-1 — 26.10 line 1853, 26.04.1 line 1849 |
| 3 | Xubuntu ships the stock Applications Menu, `xfce4-appmenu-plugin` | that package is in **neither** manifest; what ships is `xfce4-appfinder` 4.20.0-2build1, `xfce4-panel` 4.20.8-1, `xfce4-panel-profiles` 1.0.15-1build1 |
| 2 | "`libreoffice*` not present in this manifest (Xubuntu dropped it from the default seed already — verified absent)" | 19 `libreoffice-*` packages 4:26.8.0.3-0ubuntu1 — 26.10 lines 1096-1114; 19 in 26.04.1 as well |

wq#3985 found the first. The second and third are new here: the
`xfce4-appmenu-plugin` substitute is a value the author introduced (instinct 8
shape — asserting a package name nothing in the artifact references), and the
LibreOffice claim is the same error as Whisker with higher stakes, because §10
budgets the 2–4 GB bloat-removal target and the suite it omits is the largest
strippable block in the seed.

The nine claims that held, all re-grepped by exact package name against the
26.10 manifest: `git`, `cmake`, `pip`, `python3-pip`, `dkms`, `orca`,
`pulseaudio`, `xwayland`/`wayland-protocols`, `ubiquity`/`calamares`. Each is a
tool whose presence the author had no flavor-level expectation about, and each
is genuinely absent exactly as written.

## Why no reopen, and why the exposure was forward-only

Nothing consumes the dissection doc today: a repo-wide grep for
`WQ3983_XUBUNTU` outside `research/` returns only wq#3985's own verify report.
The ticket's premise that wq#3947 was "told to apply this doc's section 2
rules" is the doc's own forward reference (§2 names wq#3947); in fact wq#3947
landed as PR #1909, *before* the doc's PR #1930, and packaged Limpet's own
components rather than triaging the base seed.

More to the point, the already-landed ISO builder contradicts the doc on both
facts and is correct on both:

- `limpet/iso-builder/bloat-removal.list:14-15` purges `libreoffice-core` and
  `libreoffice-common` — written as wq#3946's "own reasoned call, not a
  transcription" (that file's header), which is why the doc's false §2 claim
  never reached it.
- `limpet/iso-builder/overlay/.../xfce4-panel.xml:27` sets
  `plugin-1 = whiskermenu`, and `build.sh` installs only the tool-catalog's apt
  packages (`build.sh:121-127`) — no whiskermenu install step anywhere. That
  overlay works *only because* the base seed already ships the plugin, i.e. the
  builder already assumed the truth that §3 denied.

So the live risk was never a shipped bug; it was a future reader trusting §3/§2
and either adding a redundant package, or "fixing" the working overlay, or
sizing a strip budget that leaves a full office suite on an RE appliance. That
risk is closed by correcting the doc before wq#3948 (branding, still open) and
before any future seed-strip ticket reads §2/§10.

## What landed on this branch

`research/WQ3983_XUBUNTU_26_10_DISSECTION.md`: §3's Whisker/appmenu sentence
rewritten with manifest line citations and a pointer to the overlay that relies
on it; §2's Strip bullet rewritten to enumerate the 19 LibreOffice packages;
§10's bloat target amended to lead with them; and a dated Corrections note in
the header stating the failure class, so the next reader knows which sentence
shape to distrust ("<flavor> already does/doesn't X") rather than distrusting
414 lines wholesale.

## Probe (could have failed; drawn from outside the fix's scope)

The ruling's scope parameter is "absence claims in §2/§3 of one doc, checked
against the 26.10 manifest they were written from." Probes from outside it:

- **The other manifest** (26.04.1, blob `4c97d3a6`) — not the file either wrong
  sentence was written against: whisker at line 1849, 19 `libreoffice-*`. Had
  26.04.1 lacked them, "stale/wrong file" would have been the correct diagnosis
  and the ruling would have flipped.
- **Outside `research/`** — the landed builder artifacts (`bloat-removal.list`,
  `xfce4-panel.xml`, `build.sh`), which is what turned "needs a reopen" into
  "doc-only": had the builder installed whiskermenu itself or omitted
  LibreOffice from the purge list, shipped code would have been carrying the
  error and the disposition would have been a code fix, not a prose fix.
- **The 9-claim re-grep** — a falsifiable sample of the whole class, not of the
  known-bad sentence. Had those failed too, the ruling on question 1 would be
  "pattern, re-verify the doc end to end."
- **Read doctrine** (`/home/lumen/kb/ISSUE-LOG.md`, outside this repo): row 142
  / wq#1352 is the governing precedent for question 2 and supplied the
  disposition.

Prior-art search was attempted per doctrine
(`fleet-search web "Xubuntu default whisker menu applications menu panel"`) and
failed with all four general engines suspended/CAPTCHA'd and no Brave fallback
key — the already-logged wq#2595 outage, not re-logged. The committed manifests
are better evidence than secondary sources for this question anyway.

## Residual

Out of scope, noted for whoever next touches the builder:
`limpet/iso-builder/bloat-removal.list:21` purges `xubuntu-icon-theme` under
the comment "Games bundled by the xubuntu-desktop metapackage" — the purge may
well be intended (branding, wq#3948) but the comment misfiles it.
