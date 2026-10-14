# wq#2744 P1 Commander -- execute-on-arrival checklist

Linear version of README.md. Check off in order; do not skip ahead. Anything
marked **[FRANK]** needs a human physically present or driving (per
control-plane rule 3 and physical-task convention) -- everything else can be
run by an orchestrator once the previous step is checked.

## Per box (repeat this whole section for p1-primary, then p1-backup)

- [ ] **[FRANK]** Unbox, note serial/model/actual RAM+storage off the label.
- [ ] Download Armbian firefly-rk3399 "Resolute" image, verify sha256.
- [ ] **[FRANK]** Flash to microSD via Balena Etcher (never dd).
- [ ] Check/populate `armbian_first_run.txt` on the boot partition (Ethernet,
      no WiFi) before first boot.
- [ ] **[FRANK]** Insert SD, connect Ethernet, power on.
- [ ] Find DHCP lease (router admin page, or `nmap -sn 192.168.8.0/24` from Lumen).
- [ ] **[FRANK]** SSH as root, complete first-run wizard: new root password,
      create user = hostname (p1-primary / p1-backup), password = the first-boot credential stored in Vault (item: p1-first-boot; never write it here), sudo.
- [ ] `fleet-provision-ssh <hostname> <user>@<dhcp-ip> --password "$P1_FIRST_BOOT_PW" --ts-key tskey-auth-...`
- [ ] Static LAN IP via netplan (pick an unused 192.168.8.x).
- [ ] `free -h` -- confirm actual RAM; if materially different from "assume 4GB",
      edit this box's `dc-gateway.service` MemoryHigh/Max before first start
      (~50%/~70% of total).
- [ ] `lumen_state.py ingest <hostname> ...` (hardware/lan_ip/tailscale_ip) + `state-regen`.
- [ ] `scp -r foreman/ops/p1-commander <hostname>@<ts-ip>:/home/<hostname>/`
- [ ] `ssh <hostname>@<ts-ip> 'sudo /home/<hostname>/p1-commander/install.sh --role <primary|backup> --hostname <hostname>'`
- [ ] `apt-get install cloudflared` (pkg.cloudflare.com repo, arm64 resolves automatically).
- [ ] **[FRANK]** `cloudflared tunnel login` (browser auth) THEN
      `cloudflared tunnel create <hostname>-mcp` -- run ON this box, never on Lumen.
- [ ] Merge `cloudflared-ingress-<hostname>.yml`'s ingress block into this
      box's `/etc/cloudflared/config.yml` (tunnel uuid + credentials-file
      from the previous step), `systemctl enable --now cloudflared`.
- [ ] **[FRANK]** DNS CNAME `mcp-<hostname>.lumennode.uk` -> `<uuid>.cfargotunnel.com`
      + CF Access app on that hostname, policy = reuse service token
      `e23d092b-c2d2-4093-b1ec-ecd1a08ec2c0`.
- [ ] `curl -i https://mcp-<hostname>.lumennode.uk/mcp` -> expect 403 (no token, proves Access is gating).
- [ ] Generate `/home/<hostname>/.ssh/id_ed25519`, distribute pubkey to Lumen +
      every fleet node's authorized_keys.
- [ ] **[FRANK]** `python3 mcpw-diag.py --base-url https://mcp-<hostname>.lumennode.uk --save-token /home/<hostname>/.secrets/mcpw-watchdog.token`
      -- full register->authorize->token->initialize against THIS box's own
      hostname (not through mcpw's pool yet).
- [ ] Confirm `systemctl status mcp-gateway-watchdog.timer` is active; let it
      tick clean for a day before moving to the next box / to cutover.

## Both boxes green -- cutover (see mcpw-cutover-plan.md for full detail)

- [ ] **[FRANK]** Confirm mcpw's own `/authorize` is back to real CF Access
      (not the 2026-09-25 outage's temporary passphrase form).
- [ ] `cp wrangler.toml wrangler.toml.bak.<date>_pre_p1_cutover`
- [ ] Edit `MCP_BACKENDS` to `[{"name":"p1-primary","url":"https://mcp-p1-primary.lumennode.uk"},{"name":"p1-backup","url":"https://mcp-p1-backup.lumennode.uk"}]`
- [ ] **[FRANK]** `npx wrangler deploy` -- note the version id for rollback.
- [ ] `curl https://mcpw.lumennode.uk/pool` -> confirm p1-primary + p1-backup
      present, lumen absent.
- [ ] `python3 mcpw-diag.py --base-url https://mcpw.lumennode.uk` -> PASS
      through the front door.
- [ ] Soak (Frank's call how long) before treating the cutover as accepted.

## Drill (wq#2781) -- only after the cutover has soaked

- [ ] **[FRANK]** `./p1-ha-drill.sh`, live, one step at a time, Frank watching.
- [ ] Fill in `reports/mcp_ha_drill_<date>.md` latencies, `lumen_episode.py commit`.

## Retire Lumen's control-plane role -- last, deliberate, separate session

- [ ] **[FRANK]** `sudo systemctl disable --now desktop-commander-mcp` on Lumen
      (leave unit files in place for fast manual revert).
- [ ] Decide (Frank): remove `mcp-backend.lumennode.uk` cloudflared ingress on
      Lumen, or leave it defined-but-unreferenced as free insurance.
- [ ] Close wq#2744, wq#2779, wq#2781.

## Every dc-gateway / desktop-commander patch deploy (wq#3804) -- MANDATORY, not optional

A patch on disk is not a patch in memory: node loads `dist/*.js` once at child start. wq#3645 and
wq#3276 (redaction) sat inactive from 2026-10-08/10-11 until the 2026-10-13 restart because the running
DC child predated the files. Do NOT close a DC-patch ticket until all of these pass:

- [ ] Restart the gateway so the child reloads: `systemctl restart dc-gateway` -- from a DETACHED
      unit with a deadman (the restart kills whatever Commander session issued it), e.g. the pattern
      in `horus:/home/horus/dc-sandbox/restart-deadman.sh`. **[FRANK present]** on the active node.
- [ ] `~/bin/dc-redaction-gate.sh --probe 3100` -> `DC_REDACTION_GATE OK ... probe=pass`
      (staleness check + live echo-a-fake-key probe; exit 1 = STALE/PROBE_FAIL, do not close).
- [ ] Repeat on every standby that could be promoted (neith, khnum): their DC child + dist must pass
      the same gate. As of 2026-10-13 they do NOT (supergateway + unpatched 0.2.46) -- see wq#3804 notes.
- [ ] Record the gate's OK line in the ticket notes as the evidence.

`mcp-gateway-watchdog.sh --mode dcredact` re-runs the gate hourly and pages ntfy/aq if a deploy was
left unrestarted. `p1-update.sh --apply` also runs the staleness check at the end.

## Ongoing (after everything above is live)

- [ ] Install `p1-update-gate.sh` + `.service` + `.timer` on Lumen (3 files,
      this directory) -- weekly, read-only, alert-only.
- [ ] `p1-update.sh` runs are always manual, always Frank-driven, on ONE box
      at a time, never both in the same window.
