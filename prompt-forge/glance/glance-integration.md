# Glance integration — Prompt Forge into home.lumennode.uk (wq#957)

Target file: `/home/lumen/glance/glance.yml` (live config, back it up first:
`cp glance.yml glance.yml.bak.$(date +%Y%m%d_%H%M%S)` — house convention).
Apply, then restart the glance container to pick it up.

## 1. Full column: Prompt Forge replaces Plug Power

Delete the `custom-api` widget titled `Plug Power` from the `size: full`
column (currently the first widget there) and put this in its place:

```yaml
          - type: iframe
            title: Prompt Forge
            source: https://prompts.lumennode.uk
            height: 640
```

The source is the public hostname, not `localhost:8792` — the iframe loads in
the viewer's browser, so it must go through cloudflared (and Cloudflare
Access, which the viewer has already passed to see home.lumennode.uk).

## 2. Small column: Plug Power moves to the left sidebar

Frank's spec: Plug Power sits where the Custom API ("Other") drawer is, with
the drawer underneath. The drawer lives INSIDE the wq#770 generated markers
(`ui-inventory --write-glance` rewrites everything between them), so a
hand-placed widget can only survive OUTSIDE the markers. Placement that
survives regeneration:

Immediately AFTER the `# END GENERATED SERVICES` line in the first
`size: small` column, insert the Plug Power widget verbatim as it appears in
the full column today (the `custom-api` widget, `url:
http://localhost:5959/assets/plugs.json`, template and all).

Resulting column order: Lumen Services bookmarks → Other drawer → Plug Power.
This inverts Frank's exact "drawer underneath Plug Power" order; putting Plug
Power above the drawer means editing inside the markers, which the next
`ui-inventory --write-glance` silently reverts. If the exact order matters,
the durable fix is a one-line change in `/home/lumen/bin/ui-inventory` to
render the drawer at the top of the generated block — file a follow-up ticket
rather than hand-editing inside the markers.
