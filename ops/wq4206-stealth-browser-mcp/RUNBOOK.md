# stealth-browser-mcp on Lumen (wq#4206)

Upstream: github.com/vibheksoni/stealth-browser-mcp (MIT, Python, nodriver + FastMCP),
pinned at commit `ba95cca933c8` (2026-10-12; `v0.2.5` is eight months older and lacks the
upload-root and HTTP-auth hardening). Installed for the lumen user, registered as the
user-scope Claude Code MCP server `stealth-browser`, so every Claude Code session on Lumen
gets it (fworker workers too; `--strict-mcp-config` local-profile runs do not).

## Install / verify

```
lumen-run start --name wq4206-install -- ops/wq4206-stealth-browser-mcp/install.sh
lumen-run start --root --name wq4206-apparmor -- ops/wq4206-stealth-browser-mcp/install-apparmor.sh
lumen-run start --name wq4206-verify -- ops/wq4206-stealth-browser-mcp/install.sh --verify
```

`--verify` is read-only. It checks the launcher and guard files, the AppArmor profile, the
upstream checkout (at the pin, no local edits), venv imports, a sandboxed Chrome start, and
the claude registration. Live acceptance test (talks MCP over stdio to the registered
command):

```
<DEST>/venv/bin/python -I ops/wq4206-stealth-browser-mcp/probe.py <DEST>
```

`DEST=/home/lumen/.local/share/stealth-browser-mcp/ba95cca933c8`. `PROVENANCE.json`
there records the commit, the Chrome build and its sha256, and the venv freeze.

## What agents get

The `--minimal` surface (21 tools: spawn/navigate/close, click/type/scroll, get_page_content,
execute_script, take_screenshot, file_upload). Typical research use:
`spawn_browser` → `navigate` → `get_page_content` → `close_instance`. Chrome runs headless
(Lumen has no display) with its sandbox on.

The launcher (`lumen_stealth_mcp.py`) runs upstream's `server.py` unchanged, and every tool
call goes through `lumen_stealth_guard.py` first:

- URLs must be http(s) to hosts that resolve only to public addresses. No `file:`, `chrome:`,
  loopback, RFC1918, link-local/metadata, or tailnet 100.64/10. Numeric spellings like
  `2130706433` are resolved before the check.
- After any call on an instance, the tab's real URL is checked. If a redirect or page script
  put the tab on a private host or a local file, the tab is blanked and that call's result
  is withheld.
- `spawn_browser`: `browser_args` must come from a short allowlist (no remote debugging, file
  access, or web-security flags). `user_data_dir` and every file path argument must be under
  `DEST/{uploads,screenshots,profiles}`. A `proxy` must be a public host.
- The `dynamic-hooks` section is always disabled, even if someone drops `--minimal`. It
  `exec()`s agent-supplied Python inside the server process.

Residual risk: a public URL that redirects to an internal service still sends that one GET
before the post-check blanks the tab. That is the same exposure `fleet-search read` already
has.

## Measured anti-bot reach (2026-10-18, Lumen, headless and headed-under-Xvfb)

| Site | Protection | Result |
|---|---|---|
| nowsecure.nl | Cloudflare JS challenge | PASS (upstream `benchmarks.stealth --targets cloudflare`) |
| crunchbase.com/organization/anthropic | Cloudflare managed challenge | FAIL: still "Just a moment..." after 30s, both modes |
| g2.com/products/anthropic-claude/reviews | DataDome | FAIL: empty page after 30s, both modes |

It gets through passive JS challenges. It does not solve interactive Turnstile or DataDome
challenges, and upstream has no captcha solver. It is not a search engine, so it does not
replace SearXNG on its own. It is a fetch/render tier for pages that plain HTTP and the
fleet-search ladder cannot render.

## Bumping the pin

1. Change `PIN` in install.sh and re-audit `src/` (URL-taking tools, file paths, `exec`).
2. If playwright's Chrome build changes, update the binary path in
   `lumen-stealth-browser-chrome.apparmor`, then run both install steps and `--verify`.
3. If upstream renames tool arguments, check `URL_ARGS`/`PATH_ARGS` in the guard.

## Remove

`claude mcp remove -s user stealth-browser`, then delete `DEST`. As root, run
`apparmor_parser -R /etc/apparmor.d/lumen-stealth-browser-chrome` first and delete the
file after. The `xvfb` apt package was installed for the headed-mode test; nothing uses
it now.
