# Limpet RE knowledge bases — SHIP / FETCH / LINK architecture — wq#3930

The appliance has to answer hardware-RE questions **offline, on a cold boot, in a
build sandbox with no egress** (fleet-wide, wq#2595), yet still reach live sources when
a network and an operator credential are present. One corpus can't do both: an Exploit-DB
mirror is 1–2 GB and must ride the ISO, a Vulners superset needs the operator's own API
key, and an FCC teardown is a web PDF that will never sit on disk. So each knowledge
source is classified by **how it integrates**, not by what it contains, into one of three
tiers:

- **SHIP** — flat files bundled on the ISO (CSV / JSON / JSONL / markdown / line lists):
  VID\:PID and JEDEC maps, baud-rate and pin references, default-credential sheets, the
  CWE/ATT&CK taxonomies. Loaded into one read-only index at `update` time; `search` is a
  single offline full-text query with no network and no DB server.
- **FETCH** — downloaded on demand by `pull <name>` (getsploit's ~1.7 GB FTS5 DB, the
  WikiDevi mirror, the volatile mobile/boot-ROM matrices, firmware-hash feeds). Too large,
  too volatile, or too credential-gated to pre-ship. A fetch stamps a dated marker so
  `status` can show staleness; the sandbox default is a dry plan (§8.5).
- **LINK** — an online helper only, **never indexed** (FCC-ID, iFixit, and the
  component-level part lookups in §7). `list` shows the URL template and the helper
  command; there is nothing on disk to search.

The front end that implements all three is `limpet-kb` (§8); this doc is the catalog it is
built against — every `kb-sources.d/*.toml` drop-in cites the section below that specifies
its tier, data format, and storage location.

## TL;DR — the catalog

| § | Source | Tier | Category | Format | Path / dest / url |
|---|--------|------|----------|--------|-------------------|
| 1.1 | Exploit-DB / SearchSploit | SHIP | exploits | csv | `exploits/files_exploits.csv` |
| 1.2 | getsploit / Vulners | FETCH | exploits | sqlite | `exploits/getsploit.db` |
| 2.1 | RouterSploit defaults.txt | SHIP | creds | lines | `creds/routersploit-defaults.txt` |
| 2.2 | DefaultCreds-cheat-sheet | SHIP | creds | csv | `creds/DefaultCreds-Cheat-Sheet.csv` |
| 2.2 | ics-default-passwords | SHIP | creds | csv | `creds/ics-default-passwords.csv` |
| 2.2 | many-passwords (wq#4087) | SHIP | creds | csv | `creds/many-passwords.csv` |
| 3.1 | OpenWrt Table of Hardware | SHIP | devices | json | `devices/openwrt-toh.json` |
| 3.2 | WikiDevi / TechInfoDepot | FETCH | devices | sqlite | `devices/devicedb.sqlite` |
| 3.3 | FCC ID teardowns | LINK | devices | — | `https://fccid.io/{query}` |
| 3.4 | iFixit | LINK | devices | — | `https://www.ifixit.com/api/2.0/search/{query}` |
| 3.5 | flashrom flashchips | SHIP | devices | csv | `devices/flashchips.csv` |
| 4 | HackTricks hardware tree | SHIP | hw | markdown | `hw/*.md` |
| 5.1 | MITRE CWE | SHIP | cwe | jsonl | `cwe/cwe.jsonl` |
| 5.2 | MITRE ATT&CK + CAPEC | SHIP | attack | jsonl | `attack/attack.jsonl` |
| 6 | Mobile / boot-ROM matrices | FETCH | mobile | sqlite | `mobile/mobiledb.sqlite` |

Each row ships as one TOML drop-in under `kb-sources.d/` (§8.6). Adding a source is a
drop-in, not a code change (the one-schema-many-files posture of `hardware.d` /
`target-devices.d`, wq#3940).

## 1. Exploit corpora

### 1.1 Exploit-DB / SearchSploit — SHIP

Parity with Kali: the operator expects `searchsploit` to work offline. The clone's
`exploits/files_exploits.csv` is the searchable index of the tree — title, type, platform,
CVE codes, and the path to the PoC file — so it indexes as a plain CSV (`title_fields =
["description"]`) without walking the 1–2 GB of exploit bodies. Refresh is the operator's
own `searchsploit -u`; the sandbox never fetches it (§8.5, scope held back per wq#3997).
Downloaded bodies are untrusted (§8.4).

### 1.2 getsploit / Vulners — FETCH

A ~1.7 GB SQLite FTS5 superset of Exploit-DB that also carries Metasploit, PacketStorm,
ZDI, and GitHub PoCs. It is **FETCH, not SHIP**, for two reasons: the size, and that
`getsploit --update` needs the operator's own `VULNERS_API_KEY` — a credential the ISO
can't carry. `pull getsploit` records the command and its key requirement; it only runs
under `--run` with the key present. Bodies are untrusted (§8.4).

## 2. Default credentials

The Target-Device Wizard's "try defaults" step reads this category. Three shapes of the
same question — *what ships as the factory login for this box* — kept as distinct sources
because they cover disjoint device populations.

### 2.1 RouterSploit defaults.txt — SHIP

The RouterSploit `user:pass` default-cred **wordlist**, one pair per line, BSD-licensed.
Ships as a `lines`-format source so the Wizard can extract it directly as a credential
list (no header, no columns). Small.

### 2.2 Product default-credential sheets — SHIP

Three CSVs, disjoint by population:

- **DefaultCreds-cheat-sheet** (ihebski, MIT, ~3,711 rows) — default user/pass by
  product, the broad consumer/enterprise sheet.
- **ics-default-passwords** (open) — ICS/SCADA vendor defaults with a `protocol` column,
  filling the OT gap the consumer sheets miss. Note the provenance caveat in
  `limpet/kb/creds/SOURCES.md`: the shipped data is SecLists' `scada-pass.csv` reshaped to
  this source's declared columns, not the 6-row upstream stub the drop-in names (wq#4087).
- **many-passwords** (GPL-2.0, wq#4087 addition) — IoT/router/admin-panel defaults
  (including MySQL/PostgreSQL), distinct from the two above.

All three index on vendor/product so a board label resolves to its candidate logins.

## 3. Device & board fingerprinting

Turning a photographed board into a SoC / flash / driver identity. The spine is SHIP; the
web-scale mirrors are FETCH or LINK.

### 3.1 OpenWrt Table of Hardware — SHIP

The device → SoC / flash MB / RAM MB / bootloader spine for the Wizard, CC-BY-SA, shipped
as a ~10–30 MB JSON dump (`title_fields = ["model","brand"]`). The first source consulted
when identifying a router-class board.

### 3.2 WikiDevi / TechInfoDepot (`devicedb`) — FETCH

Maps chipset → device → FCC ID → Linux driver name — the exact linkage for fingerprinting
an unknown board. Too large and wiki-shaped to pre-ship; mirrored into a local
`devices/devicedb.sqlite` on demand (`limpet-kb-fetch devicedb`, needs egress to a
wi-cat / archive.org mirror).

### 3.3 FCC ID teardowns — LINK

`fccid.io` / `fcc.report` internal-photo archives (the photos reveal the SoC). Web-scale
PDF/image archives, not shippable. On-demand helper only: `limpet fccid <ID>` opens
`https://fccid.io/{query}`.

### 3.4 iFixit — LINK

Public guide/teardown API (CC-BY-NC), board photography and component ID. API lookup, not
a ship: `limpet teardown <device>` over `https://www.ifixit.com/api/2.0/search/{query}`.

### 3.5 flashrom flashchips — SHIP

The flashrom flashchips DB, JEDEC `mfg:dev` ID → chip name / size / geometry, GPLv2.
Rendered from upstream `flashchips.c` to a flat `devices/flashchips.csv` (`limpet-kb-render
flashchips`) so the index resolves a bare RDID off `flashrom --flash-name` **without a C
parser in the query path** — the rendering is an `[update]` step, not a runtime dependency.

## 4. Hardware field references — HackTricks hardware tree — SHIP

Offline UART / JTAG / SWD / SPI / I2C field references: pinouts, baud rates, probe
procedures. One markdown doc per page (`hw/*.md`), indexed whole (`format = "markdown"`).
This is the tier the ticket's "baud rates, pin refs" line names — the operator wants these
at a dead workbench with no uplink, so they ride the ISO.

## 5. Defect & adversary taxonomies

Canonical labels for findings. Both ship **static** — they version slowly and must be
offline — and both are normalized to JSONL (one record per line) by an `[update]` step so
the index needs no XML/STIX parser in its path (the §8.3 posture).

### 5.1 MITRE CWE — SHIP

The weakness taxonomy (CWE IDs are the canonical defect-class label in a finding), ~15–40
MB, rendered from upstream XML/JSON to `cwe/cwe.jsonl` by `limpet-kb-render cwe`.

### 5.2 MITRE ATT&CK + CAPEC — SHIP

Technique / attack-pattern taxonomy, STIX 2.1. ATT&CK-ICS is the right framework for
firmware/OT findings. STIX bundles are normalized to `attack/attack.jsonl` (one technique
or attack-pattern per line) by `limpet-kb-render attack`.

## 6. Mobile / boot-ROM exploit matrices — FETCH

checkm8 / checkra1n compatibility, Magisk unlock status, and Android factory-image status.
Small but **volatile and web-shaped**, so it is scraped to `mobile/mobiledb.sqlite` on
demand with a fetch date (`limpet-kb-fetch mobiledb`) rather than frozen on the ISO where
it would rot. FETCH precisely because freshness, not size, is the constraint.

## 7. Candidate LINK component sources & noted gaps

The ticket names **Part-DB, Octopart, and NVD** as live-query targets. These are LINK by
nature — component-/CVE-level lookups keyed on a part number or CVE ID, far larger and more
volatile than anything indexable — and slot in as `kb-sources.d` LINK drop-ins (a `[link]`
url + helper, §8.6) with **no on-disk footprint** when an API/key story exists for each:

- **NVD** — CVE detail by ID (`https://services.nvd.nist.gov/rest/json/cves/2.0?cveId=...`),
  the live counterpart to the SHIP CWE taxonomy (§5.1) and the FETCH exploit corpora (§1).
- **Octopart / Part-DB** — component part-number → datasheet / package / sourcing, the
  component-level layer below the board-level device sources (§3).

Two SHIP/FETCH gaps the current catalog does not yet fill, recorded here so they are not
re-discovered:

- **usb.ids VID\:PID map** — a natural SHIP `lines`/`csv` source under `devices/` for the
  "VID\:PID maps" the ticket's SHIP line names; today only JEDEC IDs (§3.5) ship.
- **Firmware-hash feeds** — the ticket's FETCH "firmware hashes": a known-firmware hash
  corpus (e.g. an LVFS/vendor digest set) fetched to `devices/` or a new category, letting
  a dumped image be matched to a known release. FETCH, dated-marker, egress-gated like §6.

Neither ships now: adding either is a drop-in plus data, not a front-end change.

## 8. The `limpet-kb` front end — the mechanism

`limpet/kb.py`, shipped as the `limpet-kb` binary (one subsystem per binary: `limpet` =
plugin registry, `limpet-workspace` = reporting). One KB namespace, one updater.

### 8.1 Three tiers, one `tier` field

Every source carries `tier ∈ {ship, fetch, link}` in its `[source]` table. The loader is
catalog-driven: which sources exist is a question of which drop-ins ship, not of code. A
source is **indexable** iff it is SHIP with a format and a path (`Source.indexable`);
FETCH and LINK never enter the index.

### 8.2 Storage layout & categories

One root, flat and predictable:

```
/usr/share/limpet/kb/<category>/      # SHIP flat files + FETCH destinations
/usr/share/limpet/kb-sources.d/*.toml # shipped catalog
/etc/limpet/kb-sources.d/*.toml       # operator site overrides (later-dir-wins)
/var/lib/limpet/limpet-kb.db          # the built FTS5 index
```

`<category> ∈ {exploits, creds, devices, hw, cwe, attack, mobile}` — a closed set so the
namespace stays flat and the Target-Device Wizard reads categories by name. A SHIP `path`
and a FETCH `dest` are **relative to the KB root**; `--kb-root` / `--db` / `--sources-dir`
override the `/usr` and `/var` defaults so a test can point them at a tmp dir — a
falsifiable boundary outside the real install paths.

### 8.3 The index — one read-only SQLite FTS5 table

`limpet-kb update` loads every **present** SHIP source into a single `limpet-kb.db` FTS5
table, so `search` is one offline full-text query with no DB server. The loader is
format-dispatched over the source's glob (csv / json / jsonl / markdown / lines); a
per-source **delete-then-insert** keeps `update` idempotent (no double-count on re-run). A
source whose files are absent is skipped, not an error — the ISO can ship the catalog ahead
of the data. User search input is quoted token-by-token before it reaches FTS5 `MATCH`, so
punctuation (`OR`, `*`, `"`, `)`) is never read as query syntax.

### 8.4 Untrusted downloaded bodies

Anything a FETCH pulls — above all exploit PoC bodies (§1.1/§1.2) — is **untrusted data,
never auto-run**. The catalog records the *fetch command*; it never executes an exploit.
Extraction is safe-extract (no symlink follow, no path escape). This is the boundary the
getsploit and Exploit-DB drop-ins point at by name.

### 8.5 Update / pull / status

- `update` — rebuild the index from present SHIP files (offline, idempotent).
- `pull <name>` — FETCH only; a SHIP/LINK target is refused. **Dry by default** (prints the
  command + its requirement, e.g. getsploit's Vulners key) because the build sandbox has no
  egress; runs only under `--run`, stamping a dated marker.
- `status` — reads the markers so staleness is visible; `list` shows every catalog entry
  with its tier (and, for LINK, its URL).
- SHIP sources may carry an `[update].command` (`searchsploit -u`, `limpet-kb-render
  cwe`, …) — an operator-run refresh of the on-disk files, never auto-run in the sandbox.

Exit codes mirror `limpet/cli.py`: 0 success, 1 usage/not-found, 2 catalog/index error;
`--json` on every subcommand.

### 8.6 Catalog drop-in schema (the TOML contract)

```toml
[source]                 # required: name, tier, category (+ summary/license/size/version)
name = "flashchips"
tier = "ship"            # ship | fetch | link
category = "devices"     # one of the closed set, §8.2

[ship]                   # tier=ship: how to load the on-disk files
format = "csv"           # csv | json | jsonl | markdown | lines
path = "devices/flashchips.csv"      # glob, relative to KB root
title_fields = ["vendor", "name"]    # required for csv/json/jsonl
body_fields  = ["vendor", "name", "jedec_id", "total_kb"]
[update]                 # optional: operator refresh of the files (never auto-run)
command = "limpet-kb-render flashchips"

# tier=fetch -> [fetch] command (required) + requires + dest
# tier=link  -> [link]  url (required) + helper
```

`kb.py` `parse_source()` validates every drop-in against this contract and raises a
catalog error on a bad one, so a malformed source fails loudly at load, not silently at
search. Reference implementation and the validation rules: `limpet/kb.py`; landed-decision
log: `limpet/DESIGN-DECISIONS.md` (wq#4086/wq#4087).
