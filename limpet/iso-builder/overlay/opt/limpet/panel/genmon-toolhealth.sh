#!/bin/sh
# docs/desktop-spec.md section 4.1 -- panel item 4 (tool health summary).
j=$(limpet list --json 2>/dev/null) || { echo "<txt>Tools: ?</txt><tool>registry unreachable</tool>"; exit 0; }
ok=$(printf '%s' "$j" | jq '[.plugins[] | select(.state=="active")] | length')
tot=$(printf '%s' "$j" | jq '.plugins | length')
bad=$(printf '%s' "$j" | jq -r '[.plugins | to_entries[] | select(.value.state!="active") | "\(.key): \(.value.state) (\(.value.reason))"] | .[]')
icon="dialog-ok-apply"; [ "$ok" = "$tot" ] || icon="dialog-warning"
echo "<img>$icon</img><txt>Tools: ${ok}/${tot}</txt>"
echo "<tool>Healthy ${ok} of ${tot} plugins${bad:+\n$bad}</tool>"
echo "<click>xfce4-terminal -e 'limpet list'</click>"
