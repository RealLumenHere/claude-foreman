# wq#2808 — tailnet-scoped port restrictions belong in raw PREROUTING (STAGED, not applied)

Session sec-2-9d0da3, 2026-09-26. Frank's instruction: stage only, prove offline, do not
apply. Nothing in this directory has touched the kernel; `iptables-restore --test` was the
only iptables call and it parses without loading.

## Files
- `rules.v4.staged` — the full ruleset to install: live `iptables-save` (06:54Z, 337
  lines) plus ONE raw rule. Derived from LIVE, not `/etc/iptables/rules.v4`, because the
  persisted file lags live by 5 docker/loopback PREROUTING rules (see Audit, 4).
- `live_vs_staged.diff` — the whole difference, one line:
  `-A PREROUTING -i tailscale0 -p tcp -m tcp --dport 3100 -m comment --comment wq2808-commander-3100-tailnet-drop -j DROP`
- `TEST_PLAN.md` — rule-by-rule walk of every flow that must stay open, and the one that
  must close, plus the live checks apply.sh runs.
- `CONVENTION.md` — the fleet rule going forward (ticket scope item 2).
- `apply.sh` — the apply, with a detached auto-rollback timer. NOT run.

## Offline proof
```
sudo iptables-restore --test rules.v4.staged   -> exit 0 (parsed; --test never loads)
iptables-save before == iptables-save after     -> live ruleset untouched by the test
diff live rules.v4.staged                       -> exactly the one line above
```

## Audit (ticket scope item 1)
1. Live filter INPUT, in order: policy DROP; rule 1 `-j ts-input`; ts-input rule 2
   `-i tailscale0 -j ACCEPT` (terminal). Everything below rule 1 in INPUT is dead
   against tailnet-sourced packets and LIVE against LAN/docker packets:
   - `lumen-11434-lockdown-{docker,lo,drop}` — tailnet half already handled by the
     wq#2769 raw rule; the INPUT rules still govern LAN (drop) and docker/lo (accept).
     Keep.
   - `lumen-3100-lockdown-{lo,drop}` — the tailnet half is the hole this ticket
     closes; the INPUT rules still govern LAN. Keep, add the raw rule.
   - `pokemon-kiosk-lan 8871` (LAN accept), docker-source accepts for 8418/5678/11434,
     bridge accepts for 8095, RELATED,ESTABLISHED, the six ufw jumps — none of these
     assume they can block tailnet traffic; they are LAN/docker allowances. No change.
2. Provisioning scripts: `bootstrap/remote-provision.sh:44` does `ufw allow in on
   tailscale0` on every node; `foreman/ops/wq857-victorialogs-bes/install-victorialogs.sh:42`
   adds a per-port ufw allow on tailscale0. Neither writes an INPUT rule that expects
   to restrict tailnet traffic, so neither carries this defect; both encode the
   trust-the-whole-tailnet default that makes raw-table restrictions necessary.
3. Persisted snapshots `/etc/iptables/rules.v4.bak.*` (Apr–Aug): the same
   `lumen-11434`/`lumen-3100` INPUT pattern in every one since 20260719; no other
   tailnet-blocking INPUT rules found.
4. `/etc/iptables/rules.v4` (saved 06:00Z) != live: live carries five raw PREROUTING
   rules (three docker `-d 172.x ! -i br-…`, `127.0.0.1:4000` and `:5006` lo-only) and one
   DOCKER accept that the 06:00 save missed, and the wq2769 rule sits at a different
   offset. apply.sh ends with `netfilter-persistent save` so persisted == live again;
   a reboot before that would load the stale file. (Finding, not fixed here: whatever
   adds the lo-only drops does not save.)
5. Listeners on `0.0.0.0`/`*` reachable from any tailnet peer via ts-input today and
   NOT covered by a raw rule: 3103 (chrome-mcp, wq#2768 CRITICAL, Frank-driven), 3104
   (memory-mcp), 3333 (mcp-gateway, token-auth since wq#2748), 5678 (n8n), 8090
   (beszel), 7070, 8080, 8082, 8123, 5003, 5959, 3080. Only 3100 is in this ticket's
   scope; 3103/3104/3333 are staged as commented lines for wq#2768 to switch on.

## Evidence that closing 3100 to the tailnet breaks nothing
- mcpw (`mcp-backend.lumennode.uk`) -> cloudflared on Lumen -> `http://localhost:3100`
  (`/etc/cloudflared/config.yml:83-84`): the packet arrives on `lo`, never `tailscale0`.
- `ss` on :3100: the only established peer is `::ffff:127.0.0.1` (cloudflared).
- No file under agent/, autonomy/, bin/, foreman/harness, foreman/ops, minerva/,
  instructions/ references `100.117.66.102:3100`; sentinel probes
  `http://localhost:3100/health`; Bes carries no reference to Lumen:3100.
- Tailscale SSH is OFF (`RunSSH: false`); SSH is OpenSSH on 0.0.0.0:22 and reaches
  the box through ts-input's accept — port 22 is not mentioned by any staged rule.
