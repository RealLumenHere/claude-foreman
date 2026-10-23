# Download, verify, and flash the Limpet ISO

> User-facing walkthrough for the artifacts `limpet/iso-builder/distribute.sh`
> produces (wq#4604). Follows the decisions already recorded in
> `docs/deployment-spec.md` §1-2 (wq#4082/wq#4061) rather than restating them —
> if this page and that spec ever disagree, the spec wins.
>
> **Status:** the Limpet release signing key has not been minted yet (it's a
> one-time act queued alongside the still-open license/domain/trademark
> decisions in `projects/limpet/docs/launch-readiness.md` items 7-8), so
> `SHA256SUMS.gpg` and the fingerprint below don't exist today. This page
> describes the process a release follows once that key exists;
> `distribute.sh stage_sign` refuses to fabricate a signature with no key
> configured.

## 1. Download

Each release publishes, next to the ISO:

| File | What it's for |
|---|---|
| `limpet-<version>-amd64.iso` | the installable ISO itself |
| `limpet-<version>-amd64.iso.torrent` | torrent metainfo — opentrackr tracker + DHT/PEX, seeded directly from Lumen |
| magnet link (in the release notes) | same swarm, no `.torrent` file to fetch first |
| `SHA256SUMS`, `SHA512SUMS` | checksums over the ISO and the `.torrent` |
| `SHA256SUMS.gpg` | detached GPG signature over `SHA256SUMS`, made with the Limpet release key |

Pick whichever download path fits:

- **Direct HTTP** — fastest when the host has spare bandwidth to spare you.
- **Torrent/magnet** — preferred for large or offline-mirror downloads; works
  even if Lumen's direct seed is briefly offline, as long as one other peer
  has a complete copy. Open the magnet link or the `.torrent` file in any
  BitTorrent client (Transmission, qBittorrent, Deluge, ...).

## 2. Verify

Do this before writing anything to a USB drive. A bad download that isn't
caught here becomes a machine that won't boot, or worse, boots into a
tampered image.

**Checksum (catches corruption):**

- Linux: `sha256sum -c SHA256SUMS`
- macOS: `shasum -a 256 -c SHA256SUMS`
- Windows: `certutil -hashfile limpet-<version>-amd64.iso SHA256`, compare by
  eye against the matching line in `SHA256SUMS`

**Signature (catches tampering — do this too, not instead of the checksum):**

```sh
gpg --verify SHA256SUMS.gpg SHA256SUMS
```

Fetch the Limpet release key over a *second* channel before trusting it —
not only from the same download page the ISO came from, so a compromised
page can't hand you both the image and the key that vouches for it. The
key's fingerprint is published in `docs/limpet_readme.md` and on the
download page; cross-check the two match. If `gpg` reports "Good
signature" but the fingerprint doesn't match what's printed in
`docs/limpet_readme.md`, stop — treat the download as untrusted.

If a mirror's checksum doesn't match: discard the file and re-download,
preferably from the torrent swarm (it self-corrects per-piece) rather than
retrying the same HTTP mirror.

## 3. Flash to USB

**balenaEtcher is the only flashing path this guide documents** —
`docs/deployment-spec.md` decision 4 deliberately does not cover `dd`: a
mistyped device node is the one mistake here that destroys a disk, and
Etcher only lists removable drives and verifies the write when it's done.

1. Install [balenaEtcher](https://etcher.balena.io/) (Windows/macOS/Linux).
2. Flash from file → select the verified ISO → select the USB drive → Flash.
3. Wait for Etcher's post-write verification to finish before removing the
   drive.

Rufus (in "DD Image" mode), Ventoy, and GNOME Disks also work with this
image — it's a standard BIOS+UEFI hybrid ISO, not an Etcher-specific
format — but this guide only walks through Etcher step by step.

## 4. Boot

Boot the target PC, open its one-time boot menu (F12/F10/Esc, vendor
-dependent), and pick the USB device. The GRUB menu defaults to "Boot from
first hard disk" after a 10s timeout — a box that boots the USB by accident
and sits unattended leaves the install untouched. Select **Install Limpet**
to proceed (`docs/deployment-spec.md` §3).

## Troubleshooting

- *Checksum mismatch* → re-download, don't flash it anyway.
- *USB won't boot* → re-verify `SHA256SUMS`, re-flash with Etcher; a short or
  corrupted write is far more common than a bad ISO.
- *Signature doesn't verify* → do not flash. Re-fetch the key from a second
  channel and re-check; if it still fails, treat the asset as compromised
  and say so loudly rather than proceeding anyway.
