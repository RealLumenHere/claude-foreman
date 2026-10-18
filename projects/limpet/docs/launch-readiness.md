# Limpet pre-launch readiness — the Show HN gate (wq#4182)

> Source: `projects/limpet/docs/community-strategy.md` §3 (research-f-40b629,
> 2026-10-17), distilled into the dispatching ticket. That doc lives in the
> untracked projects repo; this file is the git-tracked gate. **Frank decides
> the launch date; this checklist decides whether a date may be set at all.**

## The rule

The Show HN does not go out until **all eight items below are DONE**. First
impressions don't get a second launch — an HN crowd that hits a 40-minute
install, an undated tool catalog, or a missing threat-model page writes Limpet
off as another AttifyOS and never comes back. Each item lists its acceptance
test, current status with evidence, and the remaining work. Status values:
**DONE** (evidence cited, re-checkable), **PARTIAL** (some artifacts exist,
acceptance not yet met), **OPEN** (not started).

Flip an item to DONE only with a `file:line` or command citation a stranger
can replay. Prose claims don't count (ISSUE-LOG #17).

## The checklist

### 1. README: 5-second pitch, 30-second asciinema, energize gate, HAL hardware table — PARTIAL

Acceptance: a reader who gives the README 5 seconds knows what Limpet is;
one who gives it 30 gets an asciinema of HAL auto-detect → agent suggestion →
energize-confirm refusal; the energize gate and the supported-hardware table
are both on the page.

- DONE — 5s pitch: `docs/limpet_readme.md:27-31` ("The Universal Reverse
  Engineering Agent Appliance" + the five-trait line).
- DONE — energize gate: `docs/limpet_readme.md:68`.
- DONE — HAL hardware table: `docs/limpet_readme.md:213` ("Supported
  Hardware", probes/programmers/analyzers/targets).
- OPEN — asciinema: no recording exists (`grep -rn asciinema docs/` finds
  nothing). Record against a real detected device (Bus Pirate or ST-Link),
  showing detect → suggest → energize-confirm prompt. ≤30s, hosted on
  asciinema.org + a GIF fallback embedded in the README.
- OPEN — consistency: README claims the catalog's canonical count is 104
  (`docs/limpet_readme.md:211`) while the tracked
  `limpet/tool-catalog.json` says `tool_count: 44` / version 0.2.4,
  generated 2026-10-15. Reconcile before anyone on HN diffs them.

### 2. Install path under 15 minutes (ISO + wizard) — PARTIAL

Acceptance: a stopwatch run from "ISO written to USB" to "first `limpet
detect` succeeds" under 15 minutes, by someone who didn't build it.

- PARTIAL — ISO builder exists: `limpet/iso-builder/build.sh`,
  `limpet/iso-builder/iso_layers.py`. The wizard is spec'd in the untracked
  projects repo (`install-wizard-spec.md`, referenced at
  `docs/deployment-spec.md:164`), and `docs/deployment-spec.md:423` notes the
  current path is the stock Xubuntu installer — the wizard is not built.
- OPEN — timed end-to-end install run on real hardware, logged (who, what
  hardware, wall-clock minutes). If it's over 15 minutes, that's a blocker,
  not a footnote.

### 3. Published, dated tool-catalog.json + cadence statement — PARTIAL

Acceptance: the public catalog carries a generation date, a version, and a
written update-cadence promise. This is the anti-AttifyOS item: AttifyOS died
by silent staleness, and the promise is the differentiator.

- DONE — dated + versioned: `limpet/tool-catalog.json` (`generated:
  2026-10-15`, `catalog_version: 0.2.4`), license policy mechanically
  enforced by `limpet/license_policy.py:47-66`.
- OPEN — cadence statement: no "cadence" text exists in the catalog or README.
  Add a `provenance.update_cadence` field plus a README sentence (e.g.
  "catalog re-verified monthly; every entry carries a last-verified date"),
  and make the promise one we can keep — a cadence we miss is worse than none.

### 4. Governance / threat-model page — OPEN

Acceptance: one public page covering the energize gate rationale, the SDR TX
refusal posture, the offline-by-default stance, and the plugin-spec trust
model.

- Raw material exists: energize gate (`docs/limpet_readme.md:68`), SDR TX
  OPEN DESIGN FLAG (`docs/plugin-spec.md:370` — wq#4147, undecided), plugin
  supply-chain trust gate (`design/wq4056_limpet_plugin_supply_chain_trust_gate.md`),
  agent security spec (`docs/agent-security-spec.md`).
- OPEN — the page itself. It must state the SDR TX question honestly as an
  open flag (per wq#4147) rather than pretend it's settled; HN rewards
  visible judgment calls and punishes glossed-over ones.

### 5. Plugin contributing guide, buspirate as the worked example — OPEN

Acceptance: a CONTRIBUTING-plugins doc that walks
`plugins/limpet-plugin-buspirate-mcp/` end to end (manifest
`limpet-plugin.toml`, `install.sh`/`health.sh`/`uninstall.sh`, `tests/`,
LICENSE with upstream attribution) and ends with "copy this layout."

- Raw material: the plugin is complete (`plugins/limpet-plugin-buspirate-mcp/`),
  spec at `docs/plugin-spec.md`, manifest schema at
  `design/limpet/plugin-manifest.schema.json`.
- OPEN — the guide. No contributing doc references the plugin layout today.

### 6. Issue templates: unrecognized-hardware + plugin-request — OPEN

Acceptance: `.github/ISSUE_TEMPLATE/` on the public repo with (a)
`unrecognized-hardware.yml` instructing `limpet detect --dump` output
attachment, and (b) `plugin-request.yml`.

- OPEN — no `.github/` directory exists in this repo, and `limpet/cli.py`
  has no `detect --dump` flag yet. The flag is a prerequisite: ship the dump
  subcommand (redacting serials by default — it will be pasted publicly),
  then the template that asks for it.

### 7. Name / trademark + domain check — OPEN

Acceptance: a dated note recording a trademark knockout search for "Limpet"
in the security-tools class, domain availability/acquisition
(limpet.sh / getlimpet.io or similar), and the GitHub org name.

- `docs/limpet-naming.md` is internal name canonicalization (wq#4080), not a
  trademark/domain check — nothing covers this today.
- OPEN — run the searches, record findings + the chosen domain. Registering
  the domain is a Frank physical task (his account, his card); queue it as
  one, don't gate the rest of the checklist on it.

### 8. License decision: AGPL core + permissive plugin API — OPEN

Acceptance: a recorded decision (doc + LICENSE files in-tree) for the
candidate split: AGPL for the core, permissive (MIT/Apache-2.0) for the
plugin API so third-party plugins aren't forced copyleft.

- No AGPL text or license-decision doc exists in `design/` or `limpet/`
  today; plugins already ship MIT (e.g.
  `plugins/limpet-plugin-buspirate-mcp/LICENSE`), which is consistent with
  the candidate split.
- OPEN — the decision is Frank's to ratify; the prep work (one-page
  comparison: AGPL vs MPL-2.0 vs Apache-2.0 for the core, with the plugin-API
  boundary defined precisely enough to license separately) is ours and can be
  done now. This item gates everything public: no repo goes public without a
  LICENSE.

## Launch channels (community-strategy §2, recorded so nobody improvises)

1. **Same morning, Tue–Thu 08:00–10:00 PT:** HN Show HN, then r/netsec,
   r/ReverseEngineering, r/hardwarehacking.
2. **After launch, not before:** Discord/Matrix — a dead chat at launch reads
   worse than no chat.
3. **Talks track:** DEF CON HHV, Hardwear.io, REcon village talks.
4. **Explicitly not:** Product Hunt — wrong audience for an RE appliance.

## Scoreboard

| # | Item | Status |
|---|------|--------|
| 1 | README pitch + asciinema + gate + HAL table | PARTIAL |
| 2 | <15 min install path | PARTIAL |
| 3 | Dated catalog + cadence statement | PARTIAL |
| 4 | Governance / threat-model page | OPEN |
| 5 | Plugin contributing guide (buspirate) | OPEN |
| 6 | Issue templates (+ `detect --dump`) | OPEN |
| 7 | Name/trademark + domain check | OPEN |
| 8 | License decision (AGPL core candidate) | OPEN |

0 / 8 DONE. No launch date until the right column reads DONE eight times.
Each OPEN/PARTIAL item should be worked as its own wq ticket citing this file;
flip a row only in the same PR that lands the evidence.
