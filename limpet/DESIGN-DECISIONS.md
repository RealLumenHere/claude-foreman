# LIMPET — design decisions log

A running record of load-bearing choices in the Limpet build, newest first. Each entry
says what was decided, why, and where the code or spec lives, so a later ticket can see the
reasoning without re-deriving it. (This file did not exist before wq#3934 —
`limpet/tool-catalog.json` noted its absence; it is created here and entries for earlier
subsystems can be backfilled as those tickets touch it.)

## wq#3934 — reporting/export system

**Decision.** `limpet-workspace export` renders a workspace to **Markdown (default), HTML,
and JSON** from a single Investigation Record (IR) assembled once from committed workspace
state. Reports are written to `<workspace>/reports/<report-id>/`. Custom formats are Jinja2
templates resolved from the workspace, then `/etc/limpet/report-templates`, then packaged
built-ins. The agent chat `/export` command drives the same core. Spec:
`docs/reporting-spec.md` (Part II). Code: `limpet/workspace_report.py`,
`limpet/workspace_cli.py`, `limpet/report-templates/`.

**Why these calls:**

- **One IR, many views.** JSON *is* the validated IR; Markdown and HTML are Jinja2 views of
  it. Formats rendered from one source cannot disagree, and the JSON feeds downstream tooling
  verbatim. (Part I §2.)
- **A report is derived, never authored.** Every field is read from `manifest.json`,
  `evidence/findings/*.json`, and `workspace.git`; the generator never takes free-text
  findings on the command line. This is what lets a report be re-derived byte-for-byte at a
  pinned commit (`--source-date none`) and so count as evidence. (Part I §0.2–0.3.)
- **Masking once, on the IR.** Tool output can carry baked-in secrets; the IR is passed
  through a credential mask before any renderer sees it, so every format redacts identically
  (fleet instinct 4). `--no-redact` exists for a sealed internal copy but stamps
  `report_meta.redacted=false` so an unredacted report is self-identifying.
- **HTML is self-contained and escapes everything.** CSS inlined, no `<script>`, no remote
  resource, forced Jinja2 `autoescape` with the `safe` filter removed — a malicious target
  string (a filename, a version banner) cannot become XSS or an exfil beacon in a report that
  gets emailed around. The appliance is headless, so HTML opens offline with no display server.

**Scope held back from the Part I (wq#3905) design, on purpose.** wq#3934 ships the
operator-facing core; it does **not** build PDF/WeasyPrint output, `--sign`/`--bundle`
evidence bundles, the `verify` subcommand, or the MCP `generate_report` router tool. The
operator (not a paying client) is the reader, and those features add a browser/GPU or signing
key surface that the three text formats do not need. Part I remains the target for them;
`--format` rejects anything but `md|html|json|all` until then.

**Divergences from Part I worth flagging** (Part I is the north star; the build honoured the
wq#3934 ticket where they differed): reports land in `<workspace>/reports/` (ticket), not
`evidence/reports/` (Part I §1); default format is `md` (ticket), not `all`; the site template
dir is `/etc/limpet/report-templates` (ticket), not `report-templates.d` (Part I §5).

**Packaging.** Added to `limpet-tools`: the `limpet-workspace` bin shim, `workspace_cli.py`
+ `workspace_report.py` + the `report-templates/` tree under `/usr/lib/limpet/tools/`, an
empty `/etc/limpet/report-templates` site drop-in, and `python3-jinja2` + `git` as
dependencies. (`packaging/limpet-tools/debian/{rules,install,control}`.)
