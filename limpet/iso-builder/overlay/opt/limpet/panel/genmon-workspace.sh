#!/bin/sh
# docs/desktop-spec.md section 4.2 -- panel item 1 (active workspace info).
# Depends on `limpet workspace current --json` (workspace-manager ticket, not yet
# built as of wq#3946); degrades gracefully to "No workspace" until it exists.
j=$(limpet workspace current --json 2>/dev/null)
if [ -z "$j" ] || [ "$(printf '%s' "$j" | jq -r '.id // empty')" = "" ]; then
  echo "<img>folder-open</img><txt>No workspace</txt><click>limpet-wizard</click>"; exit 0
fi
id=$(printf '%s'  "$j" | jq -r '.id')
tgt=$(printf '%s' "$j" | jq -r '.target')
ev=$(printf '%s'  "$j" | jq -r '.evidence_count')
echo "<img>folder-open</img><txt>WS: ${id}</txt>"
echo "<tool>Target: ${tgt}\nEvidence items: ${ev}</tool>"
echo "<click>xfce4-file-manager /var/limpet/workspaces/${id}</click>"
