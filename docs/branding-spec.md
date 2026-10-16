# wq#3907 — LIMPET branding: logo, color scheme, visual identity

Design-only ticket. This fixes the visual identity every other Limpet surface spec left
open — the icon/theme pick deferred in `docs/desktop-spec.md` §9 and the TUI palette
deferred in `docs/settings-spec.md` §4.7/§6 — so the provisioning and UI tickets can apply
it verbatim instead of each inventing a look. Companions:
[`design/wq3806_limpet_architecture.md`](../design/wq3806_limpet_architecture.md),
[`docs/desktop-spec.md`](desktop-spec.md), [`docs/agent-chat-spec.md`](agent-chat-spec.md),
[`docs/settings-spec.md`](settings-spec.md).

Two artifacts ship with this spec and are the machine-readable source of truth:

- `design/limpet/brand/tokens.json` — every color/typography token below, as data.
- `design/limpet/brand/limpet-logo.svg` — the mark, drawn only in token colors.

`limpet/test_limpet_brand.py` enforces the contract: every text token must meet WCAG AA
(≥ 4.5:1) against both `background` and `surface` in both modes, and the SVG may use only
token hexes. Change a color by changing `tokens.json`; the test tells you if the change
breaks legibility.

## 0. Identity in one paragraph

Limpet is named for the marine snail that clamps onto a rock and will not be pried off —
the appliance clamps onto a target device the same way. The visual motif is the
**blue-rayed limpet** (*Patella pellucida*): a small translucent amber shell crossed by
rows of brilliant electric-blue dots, found gripping kelp. That gives the identity its
three notes — a dark kelp-water ground, an amber shell, and electric-blue rays — and its
tone: a real animal, drawn plainly, in a terminal-native palette. Professional but
hacker-friendly; no gradients-and-glass corporate sheen, no skull-and-crossbones edgelord
theming either.

Rules of tone:

- **Dark-first.** Every operator surface (greeter, desktop, panel, TUI) defaults to dark
  mode; light mode exists for the documentation site and print/export
  (`docs/reporting-spec.md` HTML reports).
- **Monospace-forward.** The wordmark is set in the code font. Type never pretends the
  product isn't a terminal.
- **Lowercase wordmark**: `limpet`, one word, never "LimPet"/"LIMPET" in user-facing
  type (ticket titles and env vars keep their own conventions).
- **The blue dot is the signature.** Accent sparingly: the `i` tittle in the wordmark,
  status dots, link color. If everything is ray-blue, nothing is.

## 1. Logo

### 1.1 The mark — `design/limpet/brand/limpet-logo.svg`

A conical limpet shell in profile (amber gradient, apex slightly off-center as on the
real animal), three dotted rays of four blue circles radiating from the apex, sitting on
a flat dark bar — the rock it grips. Flat shapes only: it must survive 16 px (panel
icon), 1-bit (plymouth/console), and laser-engraving on the appliance lid.

Derived sizes (provisioning ticket exports these from the SVG; do not redraw):

| Use | Size | Notes |
|---|---|---|
| XFCE panel / launcher icon | 16/22/24/48 px PNG | drop the base bar below 24 px |
| LightDM greeter logo | 128 px PNG | on `background` token, no box |
| GitHub social preview | 1280×640 PNG | mark left, wordmark right, dark mode |
| favicon (docs site) | 32 px | shell + one ray only |

### 1.2 Wordmark

`limpet` set in JetBrains Mono Medium, lowercase, normal tracking, `foreground` token;
the tittle of the `i` replaced by a `primary`-blue dot (one circle, same radius as the
ray dots). Lockup: mark, 0.5× mark-width gap, wordmark at cap-height = 0.45× mark height.

### 1.3 Terminal mark (TUI splash, MOTD, `limpet --version`)

```
      .
     /:\        l i m p e t
    / : \       universal RE appliance
   /  :  \
  '~~~~~~~'
```

The colon column is the blue ray: render `:` and `.` in `primary`, the shell slashes in
`secondary`, the base in `muted`. Plain ASCII, ≤ 7 rows, no ANSI art walls — it must look
right in a 80×24 SSH session and in a log file with colors stripped.

## 2. Color — `design/limpet/brand/tokens.json`

Token-first: surfaces consume names, never raw hexes. Dark mode is primary.

### 2.1 Dark mode (default everywhere)

| Token | Hex | Motif role | Used for |
|---|---|---|---|
| `background` | `#0C1414` | deep kelp water | window/greeter/terminal bg |
| `surface` | `#152221` | — | cards, input fields, code blocks |
| `panel` | `#1C2B2A` | — | XFCE panel, sidebars, headers |
| `foreground` | `#D6E2DF` | nacre (shell interior) | body text |
| `muted` | `#7A8C8A` | — | timestamps, disabled, borders |
| `primary` | `#3EC5F0` | **the blue ray** | links, focus, selection, brand accent |
| `secondary` | `#D9A441` | amber shell | wordmark accents, highlights, prompts |
| `accent` / `success` | `#5FA876` | kelp | ok/active states |
| `warning` | `#E5B35A` | — | degraded/unhealthy states |
| `error` | `#E06C6C` | — | blocked/failed states |

### 2.2 Light mode (docs site, HTML report export)

Same roles, re-weighted for a pale-nacre ground: `background #F7F5EF`, `surface #ECE9E0`,
`panel #E2DED2`, `foreground #1A2423`, `muted #4F5E5C`, `primary #0A6E9B`,
`secondary/warning #8A6114`, `accent/success #2E6E4C`, `error #B03A3A`.

### 2.3 Contrast contract

Every non-background token in §2.1/§2.2 is a *text* color and holds ≥ 4.5:1 (WCAG AA,
normal text) against both `background` and `surface` of its mode — verified mechanically
by `limpet/test_limpet_brand.py`, not by eye. `panel` is a container color; text on
`panel` uses `foreground`/`muted` only (status *dots* on panel may use any token).

### 2.4 ANSI-16 fallback

For terminals without truecolor, `tokens.json#ansi16_fallback` maps roles to standard
colors (`primary→bright_cyan`, `secondary→yellow`, `accent→green`, `warning→
bright_yellow`, `error→bright_red`, `muted→bright_black`) so the TUI theme degrades to
the user's own 16-color scheme instead of dithering.

## 3. Typography

| Role | Face | Debian package | Fallback |
|---|---|---|---|
| Code / TUI / wordmark | JetBrains Mono (OFL) | `fonts-jetbrains-mono` | DejaVu Sans Mono |
| UI / docs body | Inter (OFL) | `fonts-inter` | DejaVu Sans |

Both are packaged in Debian/Ubuntu main, so the provisioning ticket installs them with
apt — no vendored font files, no webfont CDN on the docs site (self-host the two WOFF2s).
Sizes: XFCE UI `Inter 10`, panel genmon `JetBrains Mono 10`, TUI inherits the terminal.
Headings are Inter SemiBold, never a third face.

## 4. Application per surface

### 4.1 LightDM greeter

`lightdm-gtk-greeter` (the XFCE-stock greeter — matches `docs/desktop-spec.md` §1's
plain-XFCE stance). `/etc/lightdm/lightdm-gtk-greeter.conf`:

```ini
[greeter]
background = #0C1414
theme-name = Greybird-dark
icon-theme-name = Papirus-Dark
font-name = Inter 11
default-user-image = /opt/limpet/brand/limpet-logo-128.png
indicators = ~host;~spacer;~clock;~spacer;~session;~power
clock-format = %Y-%m-%d %H:%M
```

Flat `background` color, logo as the user badge, no wallpaper image — the greeter is the
first impression that this is an appliance, not a workstation.

### 4.2 XFCE desktop theme

Reuse, don't author: a bespoke GTK theme is an unmaintainable deliverable for a fleet of
appliance boxes. Pick (resolves `docs/desktop-spec.md` §9 "Pick the Limpet icon set /
theme"):

- GTK/WM theme: **Greybird-dark** (`greybird-gtk-theme`, Xfce's own, maintained).
- Icon theme: **Papirus-Dark** (`papirus-icon-theme`) — has the symbolic/colored
  coverage the §4 genmon `<img>` names resolve against.
- Accent: one drop-in `~/.config/gtk-3.0/gtk.css` recoloring selection/links to
  `primary`: `@define-color theme_selected_bg_color #3EC5F0;` (plus the matching fg).
- Desktop background: solid `background` token (`xfdesktop` color-style solid), with the
  mark optionally centered at 128 px. No photographic wallpaper.
- xfconf keys the provisioning ticket sets: `xsettings /Net/ThemeName Greybird-dark`,
  `/Net/IconThemeName Papirus-Dark`, `/Gtk/FontName "Inter 10"`,
  `/Gtk/MonospaceFontName "JetBrains Mono 10"`.

### 4.3 Start bar (panel)

> wq#4082: no panel ships (`docs/desktop-spec.md` §0a, two stacked terminals). The color
> tokens below apply to the agent TUI status line that replaces it.

The §4 genmon scripts in `docs/desktop-spec.md` color labels via inline `<css>`; those
colors are now fixed to tokens:

| State (effective-state ladder, `limpet/registry_core.py:29`) | Token |
|---|---|
| `active`, ok counts | `foreground` (dot: `success`) |
| `unhealthy` | `warning #E5B35A` |
| `blocked`, `cycle` | `error #E06C6C` |
| `disabled`, stale/no-data | `muted #7A8C8A` |

Panel background: `panel` token at 0.95 alpha, dark-style icons, `JetBrains Mono 10` for
genmon text so counts align.

### 4.4 Agent chat UI (Textual TUI)

Define two registered Textual themes, `limpet-dark` (default) and `limpet-light`, mapping
tokens 1:1 onto Textual's theme slots (`primary`, `secondary`, `accent`, `foreground`,
`background`, `surface`, `panel`, `success`, `warning`, `error`). This is the value of
the `appearance.tui_theme` key in `docs/settings-spec.md` §4.7 — default `limpet-dark`,
the settings panel offers exactly these two plus Textual's built-ins. Role colors inside
the chat transcript: operator prompt `secondary`, agent text `foreground`, tool-call
headers `primary`, evidence/citation lines `muted`, error events `error` — consistent
with the event stream roles in `docs/agent-chat-spec.md`. The Tauri web surface reads the
same `tokens.json` into CSS custom properties; it may not introduce colors of its own.

### 4.5 Documentation site

MkDocs Material (already the fleet's docs default), palette override via
`extra_css` custom properties: `--md-primary-fg-color: #0A6E9B` (light) /
`--md-primary-fg-color--dark: #3EC5F0`, accent likewise, both slate and default schemes
offered with the token values above. Body Inter, code JetBrains Mono, self-hosted.
Favicon and header logo from §1.1. Admonition accents reuse `success/warning/error`
tokens only.

### 4.6 GitHub repo

- Social preview: 1280×640 dark lockup (§1.1 table).
- README header: the SVG mark at 96 px + wordmark, then one line: *"limpet — the
  universal reverse-engineering appliance. Clamps onto a device and doesn't let go."*
- Badges: shields.io `flat` style only, brand-colored badges use `3EC5F0`; no
  for-the-badge shouting, ≤ 5 badges.
- Light/dark: README uses `<picture>` with `prefers-color-scheme` to swap mark variants
  (light variant = same SVG with `foreground`/`surface` swapped per §2.2).

## 5. Asset layout and change control

```
design/limpet/brand/
  tokens.json        # source of truth (this spec is its prose)
  limpet-logo.svg    # master mark; raster exports derived, never committed as source
```

Rendered exports (`/opt/limpet/brand/*.png`, WOFF2s for the docs site) are produced by
the provisioning ticket from these two files and live with the base image, not in git.
Any palette change edits `tokens.json` and must keep `limpet/test_limpet_brand.py` green;
a change that fails AA is rejected by CI, not by taste.

## 6. Open items (for follow-on tickets, not this one)

- Export the raster set (§1.1 table) and commit the GitHub social-preview PNG once a
  repo goes public.
- Decide whether the appliance lid gets the engraved mark (physical task — Frank).
- Plymouth boot splash (console-safe §1.3 mark) if the appliance ever shows boot video.
