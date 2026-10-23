#!/bin/sh
# docs/desktop-spec.md section 4.3 -- panel item 2 (connected hardware status).
# Depends on `limpet hal list --json` (HAL ticket); degrades gracefully to "HW: ?"
# until it exists.
j=$(limpet hal list --json 2>/dev/null) || { echo "<txt>HW: ?</txt>"; exit 0; }
n=$(printf '%s' "$j" | jq 'length')
rdy=$(printf '%s' "$j" | jq '[.[] | select(.ready)] | length')
lines=$(printf '%s' "$j" | jq -r '.[] | "\(if .ready then "✓" else "✗" end) \(.name) [\(.class)]"')
icon="/opt/limpet/brand/icons/limpet-status-ok-16.png"
[ "$n" -gt 0 ] || icon="/opt/limpet/brand/icons/limpet-status-offline-16.png"
echo "<img>$icon</img><txt>HW: ${rdy}/${n}</txt>"
echo "<tool>${lines:-No hardware attached}</tool>"
echo "<click>xfce4-terminal -e 'limpet hal list'</click>"
