# wq#4008 — download-completion-marker class fix

**Class:** a completion marker (`.xubuntu_download_complete`) written
unconditionally after a fetch loop ran, instead of after the fetched
artifacts were verified.

## What was found

`/home/lumen/scratch/xubuntu-26.10/` held:

- `xubuntu-26.10-desktop-amd64.iso`, `xubuntu-26.10-minimal-amd64.iso`,
  `SHA256SUMS` — all 0 bytes.
- `.xubuntu_download_complete` present, and `xubuntu_download.log` ending
  with `Xubuntu 26.10 download complete at …`.
- `xubuntu_download.log` body showing four HTTP 404s against
  `cdimage.ubuntu.com/xubuntu/releases/26.10/release/` — that path was not
  published yet (GA 2026-10-15; a live re-check mid-ticket, logged in the
  wq#4008 advisory notes, found the directory listing had started
  returning 200 with only `snapshot-1..4` and `SHA256SUMS` still 404 —
  i.e. the exact failure mode can recur on this path even after the
  directory itself resolves).
- The scratch directory also holds `stonking-desktop-*.iso` files
  ("Stonking Stingray" is Xubuntu 26.10's codename — see
  `research/WQ3983_XUBUNTU_26_10_DISSECTION.md`), but these are **Ubuntu**
  ISOs, not Xubuntu, despite living in a Xubuntu-named directory.

## Root cause

The ad hoc fetch script that populated that scratch directory (not
tracked in this repo — no committed script anywhere in this worktree,
`/home/lumen/bin`, `/home/lumen/foreman`, `/home/lumen/kb`,
`/home/lumen/sentinel`, `/home/lumen/agents/work-queue`,
`/home/lumen/mailbot`, or `/home/lumen/pokemon` produces this log format
or marker name) ran a wget loop over the desktop ISO, minimal ISO,
manifest, and `SHA256SUMS`, then wrote `.xubuntu_download_complete`
unconditionally once the loop finished — without checking wget's exit
code, the resulting file size, or any checksum. A 404 response body
(HTML, a few hundred bytes) landed in the destination file exactly like
a real artifact would, so the completion marker could not distinguish
"fetched four 404 pages" from "fetched four gigabytes of ISO".

`limpet/iso-builder/build.sh` stage1 (wq#3946), the only in-repo consumer
of a fetched base ISO, was not itself the source of the bug — it already
required a caller-supplied SHA256 and verified it before extracting — but
its failure message for a 0-byte input was a generic checksum mismatch
rather than a message naming the empty-file case, which is worth closing
defensively since an empty file's SHA256 is a fixed well-known constant
an operator could paste by mistake.

## Fix

- **`limpet/iso-builder/fetch_base_iso.sh`** (new): the correctly-gated
  replacement fetch pattern. Fetches each artifact, fails and removes
  partial output on any non-2xx response or 0-byte result, then looks up
  each ISO's expected hash in the fetched `SHA256SUMS` and
  `sha256sum`-verifies it. `.xubuntu_download_complete` is `touch`ed only
  after every artifact passes. Any failure path also deletes a
  stale/partial marker, so the marker's mere presence is never stale
  evidence.
- **`limpet/iso-builder/build.sh`** stage1: added an explicit
  `[ -s "$BASE_ISO" ]` check ahead of the checksum check, so a 0-byte
  `LIMPET_BASE_ISO` fails with a message that names the empty-file case.
- **`limpet/iso-builder/test_fetch_base_iso.py`** (new): 3 cases run
  against a local `http.server` instance — all-404 (no marker, no
  artifacts left behind), checksum-mismatch (no marker), and a verified
  success (marker present, artifact bytes correct). `pytest -q
  limpet/iso-builder/` — 12 passed (9 pre-existing + 3 new).

## Scope not covered here

- Cleaning up the actual 0-byte artifacts, the empty `SHA256SUMS`, and the
  mislabeled `stonking-desktop-*.iso` Ubuntu ISOs under
  `/home/lumen/scratch/xubuntu-26.10/`, and re-running `fetch_base_iso.sh
  26.10 /home/lumen/scratch/xubuntu-26.10` once the 26.10 release path is
  genuinely live: `/home/lumen/scratch` is not in this worker's
  `--add-dir` grant.
- `research/ubuntu-26.10-release.md` section 5 (cited by the ticket): also
  under `/home/lumen/scratch`, outside this worker's reach; not read for
  this fix.

Logged at `kb/ISSUE-LOG.md` row 1350. PR:
http://100.121.17.5:3000/lumen/claude-foreman/pulls/1959
