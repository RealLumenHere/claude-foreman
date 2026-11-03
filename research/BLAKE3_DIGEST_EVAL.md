# BLAKE3 for Limpet artifact / ISO / episode digests (wq#4831)

**Verdict:** don't migrate anything that exists today. Every SHA-256 call site in
Limpet and ops-apply is either an external trust boundary or hashes a few KiB, so
BLAKE3 has nothing to speed up there. The 6.3 GiB ISO verify is slow for a
different reason: Lumen's `sha256sum` is uutils, which runs at 41% of OpenSSL's
speed. Swapping to `gnusha256sum` or `openssl dgst -sha256` gets most of the
time back while keeping SHA-256. BLAKE3 stays the default for **new internal
content-addressed stores** that hash GiB-scale payloads, per the ticket's rule.

## Measured on Lumen (2026-11-03)

Host: Xeon E5-2699 v4 (Broadwell, AVX2, **no SHA-NI, no AVX-512**), 44 threads.
Reproduce with `python3 research/blake3_eval/hashbench.py --mib 512`. It times
one single-threaded pass over a 512 MiB random buffer, takes the best of 3 runs,
and projects the result onto a 6.3 GiB ISO.

| backend | MiB/s | 6.3 GiB ISO |
|---|---:|---:|
| `sha256sum` (uutils 0.10.0, the `/usr/bin` default) | 153 | ~42 s |
| `gnusha256sum` (GNU coreutils) | 351 | ~18 s |
| `openssl dgst -sha256` | 346 | ~19 s |
| python `hashlib.sha256` (OpenSSL) | 370 | ~17 s |
| python `hashlib.sha512` | 562 | ~12 s |
| python `hashlib.blake2b` | 664 | ~10 s |
| python `xxhash.xxh3_128` | 11243 | ~0.6 s |
| BLAKE3 (`blake3` module / `b3sum`) | unavailable | — |

BLAKE3 couldn't be measured on Lumen. Neither `b3sum` nor the `blake3` module is
installed, and this worker's allowlist refuses `pip install` and
`openssl speed`. The ticket's 15 s figure for SHA-256 roughly matches the
OpenSSL rows. Run through the default `sha256sum`, the same verify takes ~42 s.

### Published BLAKE3 numbers (not measured here)

- Single-threaded BLAKE3 with AVX2: ~2.38 GB/s, ~2.74 GB/s with clang. Source:
  Larry Hastings, msg360838 on https://bugs.python.org/issue39298 (fetched
  2026-11-03). That is about 6x Lumen's SHA-256, so a 6.3 GiB ISO would take
  ~3 s single-threaded (projected, not measured).
- `b3sum` is multithreaded by default, "making it an order of magnitude faster
  than e.g. `sha256sum`". Source:
  https://raw.githubusercontent.com/BLAKE3-team/BLAKE3/master/README.md
  (fetched 2026-11-03). On large files read from disk, the disk becomes the limit
  before the hash does.
- GNU `sha256sum` measured 2.32x faster than uutils on Ubuntu 26.04 ("uutils
  version still uses the sha2 Rust crate"). Source:
  https://computingforgeeks.com/ubuntu-2604-rust-coreutils-guide/ (fetched
  2026-11-03). That agrees with the 2.3x gap measured above.

### aerynOS prior art

moss `.stone` packages key their content index on **XXH128**, with "Blake3 on
the horizon (and used in blsforme)". Each payload header carries an XXH64 CRC.
Source: https://aerynos.com/blog/2025/03/29/aerynos-the-os-as-infrastructure/
(fetched 2026-11-03). So aerynOS uses a non-cryptographic hash for dedup
indexing and is moving to BLAKE3 where it needs collision resistance. That split
is the one recommended below.

### Packaging

Both are in Ubuntu universe: `b3sum` 1.8.2-1 and `python3-blake3` 1.0.8-3build1
in resolute (26.04 LTS), on amd64 and arm64. Source:
https://packages.ubuntu.com/search?keywords=b3sum and `?keywords=python3-blake3`
(fetched 2026-11-03). Adopting BLAKE3 therefore adds one apt package and no
vendored code. Python's stdlib `hashlib` has no BLAKE3 (UNVERIFIED for 3.14+).

## Call-site inventory and decision

| site | what it hashes | boundary | decision |
|---|---|---|---|
| `limpet/iso-builder/build.sh:247` stage1 base-ISO check | 6.3 GiB upstream ISO vs the Ubuntu-published SHA-256 | **external** (Ubuntu SHA256SUMS) | keep SHA-256. Run it with `gnusha256sum`/`openssl dgst` to cut ~42 s to ~18 s (follow-up) |
| `limpet/iso-builder/distribute.sh:87` SHA256SUMS + GPG sign | release ISO + torrent | **external** (users run `sha256sum -c`) | keep SHA-256/512 |
| `limpet/iso-builder/build.sh:704,754` tool downloads | release tarballs vs upstream pins | **external** (upstream-published digests) | keep SHA-256 |
| `ops/wq4764-ops-apply/ops_apply/canonical.py:17-33` manifest/recipe digest | canonical JSON + recipe files, KiB-scale, signed by the gate | **trust** (the signature covers it) | keep. The digest is algorithm-tagged (`sha256:`), so a later switch would not need a format break |
| `limpet/result_store.py:82,108` content_hash | masked result text, KiB-scale | internal | keep. Hash time is negligible next to the DB write |
| harness/episode digests (`harness/*.py` `hashlib.sha256(...)[:12]`) | short strings | internal | keep. Same reason |

No Limpet CAS store exists in-tree (`git grep -i 'content.addressed'` turns up
only harness lock and profile files, all KiB-scale).

## Adoption rule for new systems

1. **External or trust boundary** (published checksums, signatures, secret
   scanning, git): SHA-256. No exceptions.
2. **New internal content-addressed store with GiB-scale payloads** (a future
   artifact CAS, or an ISO layer cache keyed by content): BLAKE3 via
   `python3-blake3`/`b3sum`, stored algorithm-tagged (`blake3:<hex>`) so it can
   coexist with SHA-256 digests.
3. **Dedup index or corruption check only, no adversary** (the aerynOS XXH128
   role): xxh3_128 is 30x faster than SHA-256 here and `xxhash` is already
   importable on Lumen. It must never stand in for an integrity check against
   an attacker.
4. KiB-scale internal digests: leave them on SHA-256. Migrating them gains
   nothing and splits the codebase across two algorithms.

## Follow-ups

- Point `build.sh` stage1 at a fast SHA-256 binary (prefer `gnusha256sum`, then
  `openssl dgst -sha256`, then `sha256sum`). That keeps the algorithm and cuts
  ISO verify ~2.3x on Lumen. The scope is a small separate ticket, since that
  stage has its own tests (`limpet/iso-builder/test_*`).
- Once `python3-blake3` is installed on a build node, rerun `hashbench.py` to
  replace the published BLAKE3 row with a measured one.
