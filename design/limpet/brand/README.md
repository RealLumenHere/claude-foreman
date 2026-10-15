# Limpet branding assets

Visual identity for the Limpet appliance. The prose spec is
[`docs/branding-spec.md`](../../../docs/branding-spec.md) (wq#3907); this
directory is the committed source of truth it describes. wq#3948 added the
GRUB/Plymouth/LightDM/GTK themes, wallpaper, and app icons below.

```
design/limpet/brand/
  tokens.json            # colors + typography, machine-readable source of truth
  limpet-logo.svg        # master mark (amber shell, blue rays, rock base)
  export-assets.sh       # renders every PNG export from the SVG masters
  themes/
    grub/theme.txt       # GRUB2 gfxmenu theme (dark palette)
    plymouth/            # animated boot splash (.plymouth + .script)
    lightdm/limpet-greeter.css   # greeter recolor + hardware status bar strip
    gtk/gtk.css          # XFCE accent sheet over stock Greybird-dark
  wallpaper/limpet-wallpaper.svg # subtle branded desktop background
  icons/                 # app icons: limpet-agent/-workspace/-settings .svg
```

## Palette

All assets paint **only** token hexes from `tokens.json` (dark palette:
background `#0C1414`, surface `#152221`, panel `#1C2B2A`, foreground `#D6E2DF`,
muted `#7A8C8A`, primary/ray `#3EC5F0`, secondary/shell `#D9A441`,
accent/success `#5FA876`, warning `#E5B35A`, error `#E06C6C`).

The wq#3948 ticket text proposed a different "installer theme" (`#1a1a2e` navy,
`#00d4aa` teal, `#00ff41` Matrix green). Those were an early draft; the
formalized palette in wq#3907 superseded them — it is WCAG-AA verified
(`limpet/test_limpet_brand.py`) and already shipped on every operator surface
(greeter conf, panel XML, MOTD). Introducing a second, unverified palette would
split the brand, so these themes use the committed tokens.
`limpet/test_limpet_themes.py::test_installer_hexes_are_absent` guards against
the old hexes reappearing.

## Rasters are generated, not committed

Per `docs/branding-spec.md` §5, PNGs are *derived*, never committed. Run
`./export-assets.sh [OUTDIR]` (needs `librsvg2-bin`) to render them;
`limpet/iso-builder/build.sh` stage9 runs it into the image's
`/opt/limpet/brand/` (the runtime path the greeter conf and themes reference).
The ticket named `/home/lumen/projects/limpet/branding/` as the target; that is
a non-git deployment path outside this repo. Git-tracked sources live here;
`export-assets.sh OUTDIR` can populate any deployment path.

## Verification

```
python3 -m pytest limpet/test_limpet_brand.py limpet/test_limpet_themes.py \
  limpet/iso-builder/test_set_grub_theme.py
```
