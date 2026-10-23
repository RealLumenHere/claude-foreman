# Limpet documentation site

The user-facing docs for Limpet, built with [MkDocs](https://www.mkdocs.org/) and the
[Material](https://squidfunk.github.io/mkdocs-material/) theme and published to GitHub
Pages. Markdown in, static HTML out — zero hosting cost.

## Layout

```
limpet/docs-site/
├── mkdocs.yml              # site config + nav (Material theme)
├── requirements.txt        # mkdocs + mkdocs-material
├── gen_tool_reference.py   # generates docs/tool-reference.md from the live tool surface
├── ci/github-pages.yml     # ready-to-copy GitHub Actions deploy workflow
└── docs/
    ├── index.md
    ├── getting-started.md
    ├── user-guide/{desktop,agent-chat,target-wizard,workspaces}.md
    ├── tool-reference.md   # GENERATED — do not edit by hand
    ├── plugin-development.md
    ├── device-profiles.md
    └── architecture.md
```

## Build it locally

```sh
pip install -r limpet/docs-site/requirements.txt
python3 limpet/docs-site/gen_tool_reference.py        # refresh the generated reference
mkdocs serve -f limpet/docs-site/mkdocs.yml           # live preview at :8000
mkdocs build --strict -f limpet/docs-site/mkdocs.yml  # render to limpet/docs-site/site/
```

## The generated Tool Reference

`docs/tool-reference.md` is **generated**, not hand-written. It is built from two
sources of truth so it cannot drift from the shipped appliance:

- `limpet/tool-catalog.json` — the audited core tools.
- `plugins/*/limpet-plugin.toml` — the first-party plugin manifests.

Regenerate it after changing either:

```sh
python3 limpet/docs-site/gen_tool_reference.py
```

CI runs `gen_tool_reference.py --check`, which exits non-zero if the committed page is
stale — a tool added to the catalog without regenerating the docs fails the build.

## Deploying to GitHub Pages

Copy `ci/github-pages.yml` to `.github/workflows/` in the published repo (it is kept
here, inert, so it does not run against the monorepo's CI). On push to `main` it
installs the requirements, regenerates the reference, and runs `mkdocs gh-deploy`.
To deploy by hand:

```sh
mkdocs gh-deploy -f limpet/docs-site/mkdocs.yml
```
