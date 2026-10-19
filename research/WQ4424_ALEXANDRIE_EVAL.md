# wq#4424 — EVALUATE: Alexandrie as an Obsidian replacement for fleet knowledge

**VERDICT: REJECT as an Obsidian replacement. Optional human-facing wiki, low priority.**
Alexandrie is a healthy, MIT-licensed, actively maintained app. It doesn't fit how the
fleet uses Obsidian. The fleet writes notes headless as plain `.md` files
(`memory/obsidian_bridge.py`, OBSIDIAN.md "Why direct files"). Alexandrie keeps documents
as rows in MySQL behind a cookie-session web API, and its importer corrupts the fleet's
note format (probe in §4). Moving to it would replace a zero-service "write a file" path
with a 4-container stack plus a login-session client, and the fleet would lose
wikilinks, extra frontmatter fields, and grep-ability.

Source reviewed at `Smaug6739/Alexandrie@32a8c76da1380637cbaec14455761bacde50b6d3`
(main, 2026-10-18), shallow-cloned to `/tmp/fworker-wq4424/alexandrie`. Prior-art search:
`fleet-search web "Alexandrie Smaug6739 knowledge base obsidian import"` returned only the
upstream README/marketing pages. I found no third-party migration write-up.

## 1. Repo health (GitHub API, 2026-10-19)

| | ticket said | actual |
|---|---|---|
| stars | 2.8K | **3156** |
| license | — | MIT |
| last push | — | 2026-10-18 (active) |
| open issues | — | 35, not archived |

## 2. Resource requirements

Upstream documents no RAM/CPU figures (`docs/content/6.deploy/*` has none). The stack in
`docker-compose.yml` has four long-running services:

| service | image | role |
|---|---|---|
| mysql | `mysql:8.0` | **only** supported DB (`backend/go.mod:44` go-sql-driver/mysql; no sqlite/postgres anywhere in backend) |
| rustfs | `rustfs/rustfs:latest` | S3 store; optional, but **export/backup requires it** (`docs/content/3.use/3.import-export.md:10-12`) |
| backend | `ghcr.io/smaug6739/alexandrie-backend:latest` | Go 1.26 / gin API, :8201 |
| frontend | `ghcr.io/smaug6739/alexandrie-frontend:latest` | Nuxt SSR (Node), :8200 |

My estimate, not measured: about 0.7–1 GB RAM idle. Most of that is MySQL 8 at its
defaults (~400 MB+). The Nuxt SSR process adds roughly 150–250 MB, and the Go backend and
RustFS are tens of MB each. Lumen has the headroom (4 vCPU, 62 GB, ~48 GB available per
`free -m` this session), so resources are not what blocks adoption. I could not measure
real usage because this worker may run only `docker ps/logs` (the guard blocks even
`grep` on a path containing "docker"; see ISSUE-LOG row 483).

Ops notes, all from the compose file:
- Every image is `:latest`, so nothing is pinned.
- Default secrets are placeholders (`JWT_SECRET`, MySQL/RustFS passwords) that must be
  overridden.
- Signup is open by default (`CONFIG_DISABLE_SIGNUP=false`), and `ALLOW_UNSECURE=true`.
- MySQL publishes host port 3307 and RustFS publishes 9000. The fleet's existing MinIO
  convention may clash on 9000.

## 3. Can it import the existing Obsidian vault?

Partly. It cannot do so faithfully. Alexandrie's markdown importer runs in the browser
(`frontend/app/helpers/backups/Importer.ts`), and you feed it files or a folder through
the web UI. Archives are not accepted (`import-export.md:48`), and the docs say large sets
"may need to split into smaller batches" (`:50-52`). The "import from Obsidian" FAQ answer
(`frontend/i18n/locales/en/landing.ts:80`) means this generic markdown upload. No
Obsidian-specific code exists: `obsidian` appears only in i18n landing copy, README and docs
marketing.

What the importer preserves and what it loses:
- **Folder tree: kept.** `Importer.ts:93-118` turns path segments into category nodes.
- **Frontmatter: only `title`, `description`, `tags` survive** (`helpers/node.ts:81-83`).
  Every other key is dropped. For fleet notes that means `ticket`, `kind`, `date`, `ref`,
  which are exactly the fields `obsidian_bridge` uses for search and the review loop.
- **Frontmatter parsing is line-based, not YAML** (`node.ts:71-84`). Flow lists, block
  lists, and CRLF files all come out wrong (probe below).
- **Wikilinks: not supported.** Alexandrie's internal-link syntax is `#<15–20 digit
  snowflake id>` (`docs/content/4.markdown/3.advanced.md:115-117`). `[[wq4292]]` is
  imported as literal text, so every link in the fleet log (`obsidian_bridge.py:118`
  writes `[[wq<id>]]`) becomes dead text.
- **No backlinks or graph.** `backlink` appears nowhere in the code or docs. The only "back
  link" is the footnote return arrow (`advanced.md:90`). Hierarchy is parent/child
  (team → workspace → category → document, `node.ts:3`), not a link graph.

## 4. Probe: fleet note through Alexandrie's own parser

`research/wq4424_alexandrie/`:
- `vendored_node_helpers.ts`: `node.ts:67-99` copied verbatim at the pinned SHA.
- `fixture_wq4424.md`: rendered by the real `memory/obsidian_bridge.render_note`.
- `probe.py`: a line-for-line Python port of the parser. Node is not on the worker
  allowlist, so the TS itself could not run here.

The probe exits 1 when fleet tags fail to round-trip, so it is able to fail.

```
$ python3 -I research/wq4424_alexandrie/probe.py   # exit=1
{"case": "fleet_note", "tags": ["[\"eval\"", "\"obsidian\"]"], "dropped_keys": ["ticket", "kind", "date", "ref"], "frontmatter_left_in_body": false, "wikilink_left_as_text": true}
{"case": "block_list_tags", "tags": [], ...}
{"case": "crlf", "tags": [], "frontmatter_left_in_body": true, ...}
```

Results:
- The bridge's `tags: ["eval", "obsidian"]` imports as two junk tags, `["eval"` and
  `"obsidian"]`.
- Obsidian's own default block-list form (`tags:\n  - x`) imports with no tags at all.
- A CRLF file gets no metadata extracted, and its frontmatter is left in the document body.

## 5. Headless write path (the real blocker)

The fleet's whole Obsidian design is "an agent writes a file; no app, no service"
(OBSIDIAN.md). Alexandrie has no file-backed mode and no API keys or personal access
tokens:
- `backend/middlewares/authentification.go:26` reads the JWT only from an `Authorization` **cookie**.
- That JWT comes from password login (`router/routes/auth.go:16`) with refresh (`:17`) and
  optional TOTP.

A fleet writer would need a service account, a cookie jar, refresh handling, and a
snowflake-id mapping to make any link work. That is a new, credentialed, stateful client
in place of `open(path, "w")`. Search is MySQL `FULLTEXT ... NATURAL LANGUAGE MODE`
(`repositories/node.repository.go:298-309`): fine for people, but it no longer allows the
`grep`-over-markdown retrieval that `memory/` relies on.

## 6. Where it could fit

As a human-facing team wiki or Kanban for Frank, alongside the vault and not replacing it.
It has a PWA, OIDC/LDAP, and workspaces. That is a "want a Notion" decision, not a
fleet-memory one, and nothing in the fleet needs it today. If someone deploys it later:
pin image digests, set `CONFIG_DISABLE_SIGNUP=true`, override every default secret, keep
it behind the tailnet, and don't publish 3307/9000.

## 7. No follow-up build ticket

Reject. The Obsidian vault path (`obsidian_bridge.py`, files plus sync) stays.
