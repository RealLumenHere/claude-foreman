# wq#4009 — WQ3983 errata + GA Xubuntu 26.10 re-run status

Deliverable for wq#4009 ("[limpet] wq3983 doc corrections + re-run against GA
Xubuntu 26.10"). The actual doc edits live in-place in
`research/WQ3983_XUBUNTU_26_10_DISSECTION.md` (new "Corrections (wq#4009,
post-GA pass, 2026-10-15)" header block plus inline §6/§12/§13/§14/§16 notes);
this file is the standalone errata record the ticket's deliverable gate names.

All facts below were live-fetched 2026-10-15 (~09:30–09:45 UTC), not carried
over from the earlier `/home/lumen/scratch/research/ubuntu-26.10-release.md`
research note — that note is corroborating prior art, re-verified here, not
the source of truth.

## 1. §16/§6 kernel+AppArmor delta — NOT re-run against the real GA ISO

**Blocked on upstream, not on this pipeline.** As of this pass:
- `cdimage.ubuntu.com/xubuntu/releases/26.10/release/` lists only
  `snapshot-1`–`snapshot-4` (no new snapshot cut on GA day); `.../release/SHA256SUMS`
  returns 404.
- `releases.ubuntu.com/26.10/` (Ubuntu main, not even the Xubuntu flavor) still
  titles its own page "Ubuntu 26.10 (Stonking Stingray) **Beta**".
- `xubuntu.org`'s web pages say released; the image mirrors do not have the
  artifact yet. Same lag class wq#4008 already flagged (false "download
  complete" from 404 bodies) — gate any future fetch on a non-empty
  `SHA256SUMS` + verified checksum, never on a web page or a 200 on the
  directory itself.

**One GA number is available from a primary source without the ISO:** kernel
`7.3.0-9.9`, based on `7.3-rc6` — `documentation.ubuntu.com/release-notes/26.10/`'s
own text: "the kernel in the release images (`7.3.0-9.9`) is based on
`7.3-rc6`" (upstream 7.3 final lands after GA). This supersedes snapshot-4's
measured `7.2.0-5.5` as the number to cite, but the module-tree path/count
(`usr/lib/modules/7.2.0-5-generic/`, 8,225 files) has **not** been
re-measured at 7.3 — that needs the real ISO.

AppArmor's GA version is **not stated** anywhere in the official release
notes (checked: no version number for `apparmor` in the page). Snapshot-4
measured `6.0.0~alpha1-0ubuntu1` vs 26.04.1's `5.0.2-0ubuntu1~26.04.1` — this
stays an open, unconfirmed-for-GA number, not a re-verified one.

**Action for whoever re-runs this once the ISO publishes:** re-download per
`xubuntu-26.10` (not snapshot-4), verify against the real `SHA256SUMS`,
re-run the same `7z`/`unsquashfs` extraction wq#3983 used, and re-check
`usr/lib/modules/<ver>/` and `dpkg status`'s `apparmor` stanza specifically.

## 2. §12 — Xubuntu 26.04 LTS length corrected: 3 years, not 5

Original wq#3983 text said "26.04's 5-year LTS window." **Wrong for Xubuntu**
— 5 years is Ubuntu main only. Xubuntu's own release page
(`xubuntu.org/release/26.04/`, live 2026-10-15) states:
- Release Date: April 23, 2026
- **End of Life: April 26, 2029**

That is 3 years, not 5. Fixed in `WQ3983_XUBUNTU_26_10_DISSECTION.md` §12 and
its header Corrections block. The cadence analysis in §12 (Limpet inheriting
a short upstream security window if based on 26.10) is directionally right in
shape; the magnitude was overstated by 2 years on the 26.04 side and needed
correcting before being used for a real cadence decision.

## 3. 26.10 support-window EOL date — three conflicting primary values, not resolved by GA

Per the ticket: "Xubuntu notes say 26.10 supported nine months until January
2027 but 9 months from Oct 2026 is July 2027 — read xubuntu.org/release/26.10
EOL field before any date goes in a Limpet doc." Read it, live, 2026-10-15
(~09:35 UTC). Result: **a third, different value**, not a resolution.

| Source | Value | Read live 2026-10-15 |
|---|---|---|
| `xubuntu.org/releasedocs/26.10/release-notes/` (prose) | "supported for nine months until January 2027" | yes |
| `xubuntu.org/release/26.10/` (structured `End of Life` field) | **January 15, 2029** | yes |
| Arithmetic: Oct 15 2026 + 9 months | ~July 15, 2027 | n/a |
| Secondary (ItsFOSS/FOSSLinux/LinuxCompatible, unconfirmed) | June–July 2027 | no (secondary) |

None of the three primary/arithmetic values agree, and GA arriving did not
settle it — the live EOL field reads January 15, 2029, which is suspiciously
close to 26.04's own 3-year LTS window (April 2029) and reads like a
templating bug (the EOL-field generator likely defaulted to a release+3y
pattern rather than release+9mo). The release-notes "Support lifespan"
section mentioned in prior research is now populated with the January 2027
prose but the structured field disagrees with its own page.

**Decision for Limpet docs (per this ticket's own instruction: read the field
before any date goes in a doc):** do not commit to a single EOL date for
26.10. State it as "~9 months post-GA (nominally ~July 2027 by arithmetic);
upstream's own structured EOL field currently says January 15, 2029, which
conflicts with upstream's own release-notes prose (January 2027) — neither
date should be treated as final until Canonical/Xubuntu reconcile their own
pages." This is the wording now in `WQ3983_XUBUNTU_26_10_DISSECTION.md` §12.

## 4. Base-OS record conflict — resolved in `lumen_state`

Conflict: an earlier same-day research note
(`/home/lumen/scratch/research/ubuntu-26.10-release.md`, written
2026-10-14 ~23:30 PDT) recorded "this session's brief: Limpet base OS
(currently Xubuntu 26.04 LTS)" and flagged that it conflicted with wq#3983's
ticket text, "BASE OS CHANGED: Xubuntu 26.10 supersedes 26.04 as Limpet
foundation (Frank decision 2026-10-15)" (wq#3983, `created_at` 2026-10-15
04:43:34Z — confirmed via `wq show 3983`).

**Resolution:** wq#3983's ticket text is the later of the two same-day,
both-attributed-to-Frank statements (the session brief predates wq#3983's
existence by hours), so it wins. Recorded as the live fact:

```
limpet.base_os = Xubuntu 26.10 (Stonking Stingray) — supersedes 26.04 as Limpet foundation
  (lumen_state.py ingest limpet base_os "..." --stated-by-frank --new-predicate "...")
```

Verified readable via `lumen_state.py current limpet base_os`. The caveat
carried forward into that fact's note: 26.10 is non-LTS (~9 months upstream
support, §3 above) vs 26.04's 3-year LTS (EOL 2029-04-26, §2 above) — a real
cadence cost the base-OS decision accepts, recorded but not second-guessed
here (full-autonomy doctrine: record the reasoning, don't park on it).

## 5. Logged

`kb/ISSUE-LOG.md` row 1351 — mirror-lag class (GA web pages ahead of GA image
mirrors), the upstream EOL self-contradiction, and the 5yr/3yr LTS mixup root
cause, each with the live evidence above.

## Open follow-up (not closeable by this ticket)

The actual re-run of wq#3983's package-version delta table against a real GA
ISO remains pending **upstream publication of the artifact** — re-attempt once
`cdimage.ubuntu.com/xubuntu/releases/26.10/release/SHA256SUMS` returns
non-404 and a downloaded ISO's own checksum verifies against it (the wq#4008
failure mode: do not trust a "download complete" marker or a 200 on the
directory listing alone).
