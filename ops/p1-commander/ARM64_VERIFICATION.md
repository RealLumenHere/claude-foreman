# arm64 verification — Commander stack on Firefly Station P1 Pro (wq#2744)

Hardware identified (web search, 2026-09-26): Firefly **Station P1 Pro** =
Rockchip **RK3399**, 2x Cortex-A72 + 4x Cortex-A53, arm64/aarch64. NOT x86 —
every claim below was checked against arm64, not assumed from "it's a mini PC."

Method: for every package Lumen already runs the Commander stack with, I
read the ACTUAL installed code/package.json on Lumen (not the package's docs,
not memory) to see what it does for `linux-arm64` specifically. Versions are
Lumen's live pins, 2026-09-26.

## Verdict summary

| Component | Version (pinned) | arm64? | Notes |
|---|---|---|---|
| Node.js | v22.23.2 | **YES** | Official nodesource/nodejs.org linux-arm64 builds exist for all current LTS lines. |
| supergateway | 3.4.3 | **YES** | Pure JS (`express`, `ws`, `body-parser`, `cors`, `uuid`, `yargs`, `zod`, MCP SDK) — zero native deps, checked `package.json` directly. |
| @wonderwhy-er/desktop-commander | 0.2.46 | **YES, with one caveat below** | See per-dependency breakdown. |
| cloudflared | 2026.7.3 (candidate 2026.9.3 in apt) | **YES** | Every recent GitHub release (checked back to 2021, and the current 2026.8.x releases) ships `cloudflared-linux-arm64` + `cloudflared-linux-arm64.deb`. Lumen's own install method (`pkg.cloudflare.com/cloudflared`, `focal main`, apt) is architecture-aware — the same repo line works unchanged on an arm64 host; apt picks the arm64 package automatically. |

## desktop-commander@0.2.46 dependency breakdown (the part that actually needed checking)

Read directly from `/home/lumen/.npm-global/lib/node_modules/@wonderwhy-er/desktop-commander/node_modules/` on Lumen:

- **sharp@0.34.5** (image ops) — ships `@img/sharp-linux-arm64` +
  `@img/sharp-libvips-linux-arm64` as `optionalDependencies`. npm resolves
  the matching arch package automatically at install time on the arm64 box —
  no compile step, no risk.
- **@vscode/ripgrep@1.18.0** (search) — has moved to the same
  per-architecture `optionalDependencies` pattern (`@vscode/ripgrep-linux-arm64`
  is a published, real package) rather than a postinstall download script.
  Safe.
- **puppeteer@25.4.0** (pulled in transitively via **md-to-pdf**, used for
  DC's markdown->PDF conversion tool) → **@puppeteer/browsers@3.0.6** —
  **THIS IS THE ONE GAP.** Read `lib/browser-data/chrome.js` and
  `lib/browser-data/chrome-headless-shell.js` directly:
  ```js
  case BrowserPlatform.LINUX_ARM:
  case BrowserPlatform.LINUX:
      return 'linux64';   // <- both full Chrome AND chrome-headless-shell
  ```
  `detectBrowserPlatform()` correctly identifies an arm64 Linux host as
  `LINUX_ARM`, but this version's download-URL/executable-path resolver
  routes `LINUX_ARM` to the SAME `'linux64'` (x86_64) asset as plain `LINUX`.
  There is no genuine Linux-arm64 Chrome-for-Testing target in this
  dependency's pinned version. On an arm64 box this downloads and "installs"
  an x86_64 binary that will throw `exec format error` the moment something
  tries to run it.
  - **Blast radius: narrow.** None of the MCP tools Claude actually calls
    through Commander (`start_process`, `read_file`, `write_file`,
    `edit_block`, `list_directory`, `interact_with_process`, `get_config`,
    search) touch Puppeteer. Only DC's optional markdown→PDF conversion
    feature (backed by `md-to-pdf`) would hit this, and only at the moment
    someone actually invokes it.
  - **Silent failure mode, not a loud one.** `puppeteer/install.mjs`'s
    top-level `try/catch` swallows the download error and does not exit
    non-zero — `npm install -g @wonderwhy-er/desktop-commander@0.2.46` will
    almost certainly report SUCCESS on the P1 box, having quietly installed
    an unusable binary. The failure only surfaces later, at first PDF-tool
    use.
  - **Mitigation shipped in `install.sh`:** `PUPPETEER_SKIP_DOWNLOAD=true`
    on the npm install, so the download is skipped cleanly instead of
    silently corrupted. This means DC's markdown→PDF tool will error
    immediately and legibly if invoked on a P1 node, rather than crashing
    opaquely months later. If PDF conversion is ever actually wanted on a
    P1 node: `apt-get install chromium` (arm64 Debian/Ubuntu Chromium
    packages exist and work fine) and set `PUPPETEER_EXECUTABLE_PATH` to
    point at it — not attempted here since it's out of scope for a
    control-plane appliance and adds an unrelated package to a minimal box.
  - Also worth noting for whoever revisits this: Chrome for Testing's
    **chrome-headless-shell** channel (as opposed to full Chrome) picked up
    genuine linux-arm64 builds at some point in 2026 per third-party
    reporting (Quarto's blog, April 2026, switching their own headless
    renderer to it specifically because it's "arm64-ready" where bundled
    Puppeteer Chromium wasn't) — but that is NOT what
    `@puppeteer/browsers@3.0.6`'s code does today, per the source read
    above. A newer `@puppeteer/browsers`/`puppeteer`/`md-to-pdf` version
    might fix this; re-check before assuming it's still broken if this
    ticket sits for a while before execution.

## OS/board note (not a package, but arm64-adjacent and load-bearing)

The Station P1 Pro does **not** take a generic Ubuntu Server arm64 ISO the
way an x86 box does — Rockchip boards need a board-specific U-Boot/kernel.
Armbian publishes official support for this exact board
(`armbian.com/boards/firefly-rk3399`, family `rockchip64` since a 2024
mainline-ification), with a **"Resolute"** image available — Resolute is
Ubuntu 26.04's real codename, matching every other N100 fleet node's OS
baseline (Bes, Anubis, etc. all run Ubuntu 26.04 LTS). See README.md's "OS
bring-up" section for the actual flash/first-boot procedure — Etcher-only,
per the fleet's own USB-writing rule; this is not a case where that rule
needed bending, it's a case where it directly applies to a different image
format than usual.
