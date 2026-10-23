#!/usr/bin/env bash
# Render Limpet raster exports from the committed SVG masters.
# docs/branding-spec.md §5: "Rendered exports are produced by the provisioning
# ticket from these two files" — this is that producer. PNGs are NOT committed;
# they are generated here into $OUT (default /opt/limpet/brand) at ISO build
# time by limpet/iso-builder/build.sh stage9.
#
# Requires rsvg-convert (librsvg2-bin) on the build host.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="${1:-/opt/limpet/brand}"
LOGO="$HERE/limpet-logo.svg"
WALL="$HERE/wallpaper/limpet-wallpaper.svg"

command -v rsvg-convert >/dev/null || {
  echo "export-assets: rsvg-convert not found (apt install librsvg2-bin)" >&2
  exit 1
}
mkdir -p "$OUT" "$OUT/grub" "$OUT/plymouth" "$OUT/icons"

# Panel / launcher icon sizes + greeter badge (branding-spec.md §1.1 table).
for px in 16 22 24 48 128 256; do
  rsvg-convert -w "$px" -h "$px" "$LOGO" -o "$OUT/limpet-logo-$px.png"
done

# GRUB theme mark (theme.txt references limpet-logo-128.png in its own dir).
cp "$OUT/limpet-logo-128.png" "$OUT/grub/limpet-logo-128.png"

# Plymouth: full mark + a single ray dot sprite at primary #3EC5F0.
cp "$OUT/limpet-logo-256.png" "$OUT/plymouth/limpet-logo-256.png"
rsvg-convert -w 24 -h 24 \
  <(printf '%s' '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10" fill="#3EC5F0"/></svg>') \
  -o "$OUT/plymouth/ray-dot.png"

# App icons (branding-spec.md §1.1 panel sizes, docs/desktop-spec.md §9 set).
for name in agent workspace settings; do
  for px in 16 22 24 48; do
    rsvg-convert -w "$px" -h "$px" "$HERE/icons/limpet-$name.svg" \
      -o "$OUT/icons/limpet-$name-$px.png"
  done
done

# Panel genmon status icons (docs/desktop-spec.md §4.1/§4.3) -- replace the
# generic freedesktop icon names (dialog-ok-apply, dialog-warning,
# network-wired, network-offline) the genmon scripts used before wq#4602.
for name in status-ok status-warning status-offline; do
  for px in 16 22 24 48; do
    rsvg-convert -w "$px" -h "$px" "$HERE/icons/limpet-$name.svg" \
      -o "$OUT/icons/limpet-$name-$px.png"
  done
done

# Desktop wallpaper (branding-spec.md §4.2 optional centered mark; this ships
# the subtle branded variant).
rsvg-convert -w 1920 -h 1080 "$WALL" -o "$OUT/limpet-wallpaper.png"

echo "export-assets: wrote exports to $OUT"
