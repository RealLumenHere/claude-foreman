# wq#3905 — LIMPET reporting and export system

An investigation produces a workspace (`design/wq3806_limpet_architecture.md:206-229`);
this document specifies how that workspace becomes a *report* an operator can hand to
someone else. The deliverable of a Limpet engagement is not the agent's chat log — it is a
reproducible, evidence-backed report in a format the recipient can open (HTML to read, PDF
to file, Markdown to diff, JSON to feed another tool). This spec defines the report data
model, the collect→render pipeline, the format renderers, the custom-template system, and
the full `limpet-workspace export` CLI/MCP surface.

This is a **design-only** document. It specifies behaviour, interfaces, and the data model;
it installs no renderer and registers no MCP server. It builds directly on the Limpet
architecture (`design/wq3806_limpet_architecture.md`, especially §5 workspace isolation and
its evidence-bundle concept) and reuses the fleet's existing report posture
(the `firmware-security-reports` skill: executive summary + CVSS-3.1 findings + PoC +
remediation + evidence). Where this spec and the architecture doc disagree, the architecture
doc wins — this is the reporting layer on top of the workspace, not a redefinition of it.

## 0. Constraints this design must respect (read first)

Four facts on the ground shape every decision below. Ignoring them would be winging it.

1. **The cited `workspace-spec.md` / `limpet-workspace export` stub does not exist.** The
   ticket says the export CLI is "already stubbed in workspace-spec.md". A repo-wide search
   (2026-10-14) finds no `workspace-spec.md` and no `limpet-workspace` CLI or tool anywhere
   — the only `limpet-workspace` reference in the tree is the in-process meta-server tool
   *named* in `design/wq3806_limpet_architecture.md:130`, which has no implementation or
   command surface yet. This is the same situation §0.2 of the architecture doc handled for
   the missing `tool-catalog.json`: the stub is a *target*, not an input. This spec
   therefore designs the `limpet-workspace export` command **from the workspace model that
   actually exists** (`architecture.md:206-229`) rather than elaborating a stub that isn't
   there, and §7 is written so it *is* that command's specification when a build ticket
   writes it.

2. **A report is derived, never authored.** Every byte of a report is computed from
   committed workspace state — `manifest.json`, the per-workspace evidence log, the files
   under `evidence/`, and `workspace.git` history (`architecture.md:211-218, 148-149`). The
   generator never accepts free-text findings on the command line and never writes into the
   workspace's analysis state. This is what lets the report itself be evidence: anyone can
   re-run the export against the same commit and get the same document.

3. **Reproducible = evidence-grade.** Exporting the same workspace at the same commit must
   produce a byte-identical report save for a single clearly-delimited generation timestamp
   (and even that is suppressible with `--source-date`). Determinism is not a nicety here;
   a report that cannot be re-derived is not evidence. This forces: stable key ordering in
   JSON, pinned template versions, no wall-clock or RNG inside renderers, content-hash
   references instead of absolute paths.

4. **Headless-first and untrusted-input-safe (fleet instincts 1 and the per-workspace
   untrusted-data rule, `architecture.md:220-223`).** Limpet has no console: export is a
   CLI/MCP tool and the PDF path must render with no display server. The report quotes
   strings extracted from the *target* (filenames, version banners, decompiled identifiers)
   — those are untrusted data. HTML output escapes them; no renderer executes workspace
   content or fetches a remote resource; the generator reads evidence by path argument and
   never runs an interpreter from inside `target/`.

## 1. Where this fits

```
workspace (read-only source)                 report artifacts (new, outside analysis state)
/var/limpet/workspaces/<target-id>/           evidence/reports/<report-id>/
  manifest.json        ─┐                        report.json      (the IR, §2)
  evidence/            ─┤  collect (§3)           report.html
  notes/               ─┤ ───────────────▶       report.md
  workspace.git        ─┘                         report.pdf
                                                  bundle.tar.zst + bundle.tar.zst.sig (§8)
```

Export is a **read-only consumer** of the workspace. Its one write is into
`evidence/reports/<report-id>/` (append-only, like the rest of `evidence/`), which is itself
a committed workspace change — so "a report was generated" is a replayable step in the git
history, consistent with `architecture.md:208-218`. The exporter is surfaced two ways with a
shared core: the `limpet-workspace` CLI (§7) for SSH/headless use, and a `generate_report`
router tool on the meta-server (§7.4) for the agent, trust-gated and audited like any other
tool (`architecture.md:203-204`).

## 2. Data model — the Investigation Record (IR)

All four formats render from one canonical intermediate representation, assembled once and
validated against a JSON Schema (`design/limpet/report-ir.schema.json`, a companion artifact
for the build ticket). JSON export *is* the IR; Markdown/HTML/PDF are views of it. This is
the single-source-of-truth decision that makes the formats agree and the output reproducible.

```jsonc
// InvestigationReport (report.json)
{
  "report_meta": {
    "schema_version": "1",
    "report_id": "rpt-<short-hash>",         // derived from workspace_commit + template, deterministic
    "template": "full-technical@1",          // name@version actually used
    "generator_version": "limpet-report/<semver>",
    "workspace_commit": "<40-hex>",          // the commit exported; the report is pinned to it
    "generated_at": "<RFC3339>",             // the ONE non-deterministic field; see --source-date (§7.2)
    "source_date_epoch": 1760000000          // what was actually used; 0 if timestamps suppressed
  },
  "target": {                                // from manifest.json (architecture.md:217)
    "target_id": "...", "class": "firmware|apk|native|web|...",
    "identity": { "vendor": "...", "model": "...", "version": "..." },
    "checksums": [ { "path": "target/fw.bin", "sha256": "...", "size": 8388608 } ],
    "hardware_bound": [ { "capability": "debug_probe", "adapter": "ST-Link/V2", "vid_pid": "0483:3748" } ]
  },
  "timeline": [                              // merged + ordered, §3.2
    { "ts": "...", "kind": "commit|tool_invocation|note|finding",
      "ref": "<commit|audit-seq>", "summary": "extracted rootfs via unblob", "actor": "agent|operator" }
  ],
  "tools_used": [                            // folded from the audit log (architecture.md:148-149)
    { "plugin": "binwalk", "version": "2.3.4", "trust": "core",
      "invocations": 3, "first_ts": "...", "last_ts": "..." }
  ],
  "firmware_checksums": [                     // every artifact hashed, target + derived
    { "path": "evidence/rootfs/bin/busybox", "sha256": "...", "note": "extracted" }
  ],
  "git_summary": {                            // workspace.git, §3.3
    "commit_count": 42, "first_commit": "...", "head": "...",
    "shortlog": [ { "sha": "...", "ts": "...", "subject": "..." } ]   // capped, newest-first
  },
  "findings": [ /* Finding, §2.1 */ ],
  "artifacts": [                              // files referenced by the report, by hash
    { "id": "art-1", "path": "evidence/decompile/auth.c", "sha256": "...", "git_blob": "<40-hex>", "media": "text/x-c" }
  ]
}
```

### 2.1 Finding and evidence chain (the integrity spine)

```jsonc
// Finding
{
  "id": "F-001",
  "title": "Hardcoded admin credential in /etc/shadow",
  "severity": "critical|high|medium|low|info",
  "cvss": { "version": "3.1", "vector": "CVSS:3.1/AV:N/...", "score": 9.8 },  // optional
  "summary": "...",
  "evidence_chain": [                         // ORDERED; each link is content-addressed
    { "artifact_id": "art-1", "git_blob": "<40-hex>", "locator": "line 12",
      "excerpt_hash": "sha256:...", "produced_by": "<audit-seq>" }
  ],
  "reproduction": [                           // each step cites a committed tool invocation, not prose
    { "audit_seq": 17, "plugin": "unblob", "args_digest": "sha256:...", "workspace_commit": "..." }
  ],
  "remediation": "..."
}
```

The evidence chain is the load-bearing part. A finding never embeds a raw blob of tool
output; it references the artifact by its **git blob hash** in `workspace.git` plus a
locator and an excerpt hash. A reader verifies any claim with
`git -C workspace.git cat-file blob <git_blob>` and re-hashing — no trust in the report text
required. This is the §2 "report is evidence" guarantee made checkable, and it mirrors REA's
`export_evidence_bundle` concept (`architecture.md:227-229`) so Limpet findings stay portable.

## 3. The export pipeline

`collect → assemble → validate → render → (sign/bundle)`. Each stage is pure given the
workspace commit, which is what buys §0.3 reproducibility.

1. **collect** — four read-only collectors, each scoped to one source:
   - `manifest_collector` → `target`, hardware bindings (`manifest.json`).
   - `audit_collector` → `tools_used`, reproduction refs, and tool-invocation timeline
     events (the per-workspace evidence log, `architecture.md:148-149`).
   - `git_collector` → `git_summary` and commit timeline events (`workspace.git`, read at
     `--at` or HEAD).
   - `evidence_collector` → `artifacts`, `firmware_checksums`, and the finding records that
     the agent/operator committed under `evidence/findings/*.json` during analysis (findings
     are *collected*, not authored at export time — §0.2).
2. **assemble** — merge collector output into one `InvestigationReport`, dedupe, order
   (§3.2), fill `report_meta`. `report_id` is `sha256(workspace_commit + template)[:12]`, so
   it is stable across re-runs.
3. **validate** — check the IR against `report-ir.schema.json`; a malformed IR aborts the
   export with a nonzero exit before any format is written. Catches a collector bug before
   it ships a wrong report.
4. **render** — one renderer per requested format, each consuming only the validated IR
   (§4). Renderers share nothing but the IR, so adding a format cannot change another.
5. **sign/bundle** — optional (§8).

### 3.2 Timeline ordering

Events come from two clocks (git author time; audit-log monotonic sequence). Order by
`(ts, kind_rank, seq)` with `kind_rank` breaking same-timestamp ties deterministically
(commit < tool_invocation < finding < note), and carry the audit `seq` so two events in the
same second still sort stably across re-runs. No event is dropped; a tool invocation with no
committing change still appears, tagged to the commit it ran against.

### 3.3 Git summary

`git_summary.shortlog` is newest-first and capped (default 200 entries; `--full-log` lifts
the cap). `commit_count`, `first_commit`, and `head` are always exact. The cap keeps a
long investigation's report bounded without hiding that commits were elided (the count
never lies).

## 4. Formats and renderers

| Format | Renderer | Why it exists | Determinism notes |
|---|---|---|---|
| **JSON** | emit validated IR | machine-readable; the canonical source other tools consume | keys sorted, UTF-8, `\n`, 2-space indent; byte-stable |
| **Markdown** | IR → template | portable, diffable, lands in `evidence/` and reviews cleanly | template pinned by version; no locale formatting |
| **HTML** | IR → template | the human read: one self-contained file, offline | CSS/JS inlined; **no external fetch**; all target strings escaped |
| **PDF** | HTML → PDF (headless) | the archival/hand-off artifact | rendered via WeasyPrint (no browser, no display); fonts embedded |

- **HTML is a single self-contained file.** CSS is inlined and there is no `<script>` and no
  remote `src`/`href` to fetch — the report opens with no network and cannot be turned into
  an exfiltration or XSS vector by a malicious target string (§0.4). Every value drawn from
  the target or tool output is HTML-escaped at render; the template cannot opt a field out of
  escaping (§5).
- **PDF uses WeasyPrint, not a headless browser.** Rationale: the appliance is an
  iGPU-only M70q with no display server (`architecture.md:3, 233-239`); a Chromium headless
  pipeline drags in a browser and a running GPU/display stack, whereas WeasyPrint renders the
  same self-contained HTML to PDF as a pure library call. One HTML renderer feeds both the
  HTML and PDF outputs, so they never disagree. (A build ticket may swap the engine; the
  contract is "HTML string in, PDF bytes out, no display required".)
- **JSON is the IR verbatim** after schema validation — not a re-serialization — so
  `--format json` output is exactly what every other format was rendered from.

## 5. Template system

Custom report formats are the ticket's explicit ask. Rendering (Markdown/HTML) is
template-driven via a **sandboxed Jinja2 environment**; JSON and the PDF-from-HTML step are
not templated (JSON is the IR; PDF derives from the HTML template's output).

- **Template contract.** A template receives the validated IR as its context and nothing
  else — no filesystem, no network, no `eval`. The environment is Jinja2 with
  `autoescape=True` for HTML templates (forced; a template cannot disable it or mark a field
  `|safe` — the filter is removed from the environment), sandboxed
  (`jinja2.sandbox.SandboxedEnvironment`) so attribute access on the IR can't reach dunder
  internals, and given a fixed, side-effect-free filter set (date formatting with an explicit
  format string, `sha_short`, `severity_badge`, `cvss_color`).
- **Resolution order.** `--template NAME` resolves against, in order: the workspace's own
  `report-templates/` (an engagement can ship a bespoke format), then
  `/etc/limpet/report-templates.d/` (site templates), then the built-ins baked into the
  package. First match wins; `--template PATH` (a path) bypasses the search for a one-off.
- **Template package.** A template is a directory with a `template.toml`
  (`name`, `version`, `formats = ["html","md"]`, `title`, `description`) plus
  `report.html.j2` / `report.md.j2`. The version is recorded in `report_meta.template` so a
  report names the exact template that produced it (§0.3). A template declaring a format it
  lacks a file for fails validation at load, not mid-render.
- **Built-ins.** `full-technical` (default: everything — target, timeline, every finding
  with full evidence chain, tools, checksums, git shortlog), `exec-summary` (identity +
  severity-ranked findings + remediation, no raw evidence), `evidence-only` (findings and
  their evidence chains, for an appendix), `sbom-checksums` (just `target` + all checksums +
  `tools_used`, for a provenance attestation). All four consume the same IR.
- **Listing/validation.** `limpet-workspace templates list` enumerates resolvable templates
  with source and declared formats; `templates validate <path>` dry-renders against a
  fixture IR so a custom template is proven before an engagement relies on it.

## 6. Redaction and masking

Reports quote tool output, and tool output can contain secrets (a firmware image with a
baked-in API token, a config dump with a password). Before any renderer sees a string, the
assembled IR is passed through the fleet's masking pass (fleet instinct 4): known credential
prefixes and `NAME=<secret>` shapes are replaced with `«MASKED:sha256[:8]»` so a reader can
still correlate "same secret seen twice" without the secret leaking into an HTML/PDF that
gets emailed around. `--no-redact` exists for a sealed internal bundle but prints a loud
stderr warning and stamps `report_meta.redacted=false` into the IR so an unredacted report
is self-identifying. Masking runs on the IR (once), not per-format, so every format redacts
identically.

## 7. The `limpet-workspace export` CLI (full design)

This replaces the non-existent stub (§0.1). It matches the existing `limpet` CLI conventions
(`limpet/cli.py:120-151`): a global `--json` for machine-readable *status* output, subcommands
with `set_defaults(func=...)`, and `--*-dir` overrides of the `/var/limpet`, `/etc/limpet`
defaults.

### 7.1 Surface

```
limpet-workspace export <workspace>            # path or <target-id> under /var/limpet/workspaces
    --format {html,pdf,md,json,all}   (default: all)
    --template NAME|PATH              (default: full-technical)
    --out DIR                         (default: <workspace>/evidence/reports/<report-id>/)
    --at COMMIT                       (default: HEAD; pins the report to a workspace commit)
    --source-date EPOCH|iso|none      (default: now; "none" zeroes generated_at for byte-repro)
    --full-log                        (lift the git shortlog cap, §3.3)
    --no-redact                       (§6; warns)
    --sign                            (§8; detached signature over the bundle)
    --bundle                          (§8; also emit bundle.tar.zst of report + referenced artifacts)
    --commit / --no-commit            (default --commit: record the new report dir in workspace.git)
    --json                            (machine-readable status: paths written, report_id, exit reason)

limpet-workspace templates list
limpet-workspace templates validate <path>
limpet-workspace verify <report-dir>           # re-check every evidence_chain hash against workspace.git
```

### 7.2 Behaviour notes

- **`--at` + `--source-date none`** is the reproducibility contract: the same workspace
  commit and template produce byte-identical `report.json`/`.md`/`.html` across machines and
  dates. `verify` (below) and CI use this.
- **Determinism of PDF.** WeasyPrint embeds a `/CreationDate`; the exporter sets it from
  `source_date_epoch` and fixes the PDF `/ID` from `report_id`, so `--source-date none` makes
  the PDF reproducible too (otherwise PDF bytes drift and only JSON/MD/HTML are byte-stable).
- **`--out` outside the workspace** is allowed (hand a report straight to a share) but then
  `--commit` is a no-op (nothing to commit) and the tool says so rather than silently
  skipping.
- **`verify <report-dir>`** is the acceptance-grade check and the reason the evidence chain
  is content-addressed (§2.1): it walks every finding's `evidence_chain`, resolves each
  `git_blob` in `workspace.git`, re-hashes, and exits nonzero on the first mismatch. A report
  whose evidence no longer resolves is a failed report, loudly.

### 7.3 Exit codes

`0` success; `2` bad usage (argparse); `3` workspace/commit not found; `4` IR schema
validation failed (§3.3); `5` template load/validate failed; `6` a renderer failed; `7`
`verify` found an evidence mismatch; `8` signing requested but no key available. Distinct
codes so an automated caller (or the agent) can branch without scraping text.

### 7.4 MCP parity

The meta-server exposes one router tool, `generate_report(workspace, format, template,
at)`, that drives the same core and returns `report_meta` plus the written paths (never the
rendered bytes — reports are megabytes; they are written to the workspace and *referenced*,
per `architecture.md:144-147`). The tool is trust-gated and audited like every other tool
(`architecture.md:203-204, 148-149`); generating a report is itself a logged, replayable
workspace event.

## 8. Signing and the evidence bundle

`--bundle` packs the report directory plus every artifact its findings reference (resolved
from `workspace.git` by blob hash, so the bundle is self-contained) into
`bundle.tar.zst` with a top-level `MANIFEST.json` listing each member and its sha256.
`--sign` adds a detached signature (`bundle.tar.zst.sig`) over that tarball using the
appliance signing key via `systemd-run`-managed key access — never a key literal on the
command line (fleet instinct 4/5). This is the §5 "evidence bundle … single signed bundle"
deliverable (`architecture.md:227-229`) specialized to a report: a recipient verifies the
signature, unpacks, and can re-run `limpet-workspace verify` against the embedded artifacts
with no access to the original box.

## 9. Testability (how a build ticket proves this works)

Each is a probe that *can fail*, and the reproducibility and template probes are drawn from
**outside** the default population (fleet acceptance-probe rule):

1. **Determinism.** Export a fixture workspace twice at a pinned commit with
   `--source-date none`; assert `report.json`, `.md`, `.html` are byte-identical and the PDF
   bytes match. (Fails if any renderer leaks wall-clock/RNG.)
2. **IR schema.** Validate the assembled IR against `report-ir.schema.json`; a fixture with a
   deliberately malformed finding must abort with exit `4`.
3. **HTML escaping (untrusted target string).** Seed the fixture target identity with
   `<img src=x onerror=alert(1)>` and `{{7*7}}`; assert the HTML output contains the escaped
   text and renders no `49` and no live tag — proves §0.4 and the forced-autoescape of §5.
4. **Evidence chain resolves.** `limpet-workspace verify` on a freshly exported report exits
   `0`; then rewrite one byte of a referenced evidence blob's recorded hash and assert exit
   `7`. (Proves §2.1 is actually checked, not decorative.)
5. **Custom template from outside the built-in set.** Render with a template that lives only
   in the fixture workspace's `report-templates/` (not a built-in, not in
   `/etc/limpet/report-templates.d/`) and assert it is selected over the built-in of the same
   name — the resolution-order probe drawn from the non-default population, so it can falsify
   §5 resolution rather than confirm it.
6. **Redaction.** Plant a `GITHUB_PAT=`-shaped secret in a collected evidence string; assert
   it appears as `«MASKED:…»` in all four formats and the raw value appears in none.
7. **Headless PDF.** Render PDF with no `DISPLAY` set; assert a nonzero-size valid PDF.

## 10. Scope boundary

Design-only. No renderer, template engine, or CLI is installed or registered here; the
companion build artifacts a provisioning ticket should produce are
`design/limpet/report-ir.schema.json` (the IR schema §2) and the built-in template package
(§5). This spec consumes the workspace model and evidence-bundle concept of
`design/wq3806_limpet_architecture.md:206-229` and does not redefine them; the absent
`workspace-spec.md` (§0.1) is not a prerequisite — §7 is the specification of the
`limpet-workspace export` command the architecture doc names at line 130.
