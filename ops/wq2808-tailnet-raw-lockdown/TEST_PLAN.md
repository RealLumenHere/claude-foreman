# wq#2808 — rule-by-rule test plan for rules.v4.staged

Packet path on this kernel: raw PREROUTING -> conntrack -> mangle -> nat PREROUTING ->
routing -> filter INPUT. The staged file changes ONE table (raw) by ONE rule, so every
flow below is walked through raw first and then through filter INPUT as it is today.
Live rule numbers are from `iptables-save` at 06:54Z (`/tmp/sec-2-9d0da3/live.v4`).

Legend: raw = the new rule; ts = the ts-input chain; INPUT n = filter INPUT line n.

| # | flow (src -> dst:port, ingress if) | raw PREROUTING | filter INPUT | result | change |
|---|---|---|---|---|---|
| 1 | Thor 100.112.151.100 -> Lumen:22, tailscale0 | dport 22: no rule matches (raw only names 3100 on tailscale0, and 127.0.0.1-dst lo rules) -> policy ACCEPT | INPUT 1 -> ts; ts 2 `-i tailscale0 ACCEPT` | OPEN | none |
| 2 | any tailnet peer -> Lumen:22 | same as 1 | same as 1 | OPEN | none |
| 3 | browser -> mcpw -> cloudflared (on Lumen) -> 127.0.0.1:3100, lo | `-i tailscale0` does not match lo; no lo/3100 rule -> ACCEPT | ts: src 127.0.0.1 matches none of ts 1-5 -> RETURN; INPUT `lumen-3100-lockdown-lo` `-i lo ACCEPT` | OPEN | none |
| 4 | cloudflared via IPv6-mapped `::ffff:127.0.0.1` -> :3100 | IPv4 stack, as 3 | as 3 | OPEN | none (rules.v6 untouched) |
| 5 | **tailnet peer -> 100.117.66.102:3100, tailscale0** | **raw new rule: DROP** | never reached | **CLOSED** | **the fix** (today: ts 2 ACCEPT — any peer gets Commander) |
| 6 | LAN 192.168.8.x -> :3100, enp0s25 | no match -> ACCEPT | ts RETURN; INPUT `lumen-3100-lockdown-drop` DROP | closed | none (already closed) |
| 7 | fleet -> LiteLLM 100.117.66.102:4000, tailscale0 | `-d 127.0.0.1 ! -i lo --dport 4000 DROP` needs dst 127.0.0.1 -> no; new rule needs 3100 -> no; ACCEPT | ts 2 ACCEPT | OPEN | none |
| 8 | Bes 100.110.156.91 -> :11434, tailscale0 | wq2769 rule `! -s bes` excludes Bes -> ACCEPT | ts 2 ACCEPT | OPEN | none |
| 9 | other peer -> :11434, tailscale0 | wq2769 rule DROP | — | closed | none |
| 10 | LAN -> :8871 (pokemon kiosk) | ACCEPT | ts RETURN; INPUT `pokemon-kiosk-lan` ACCEPT | OPEN | none |
| 11 | docker 172.16/12 -> :8418, :5678, :11434 | docker `-d 172.x ! -i br-…` rules key on DESTINATION containers, not the host; ACCEPT | INPUT docker-source ACCEPTs | OPEN | none |
| 12 | br-39ab…/docker0 -> :8095 (llama) | ACCEPT | INPUT bridge ACCEPTs | OPEN | none |
| 13 | tailscale control: udp 41641 in, DERP/443 out | ACCEPT | ts 3 ACCEPT; OUTPUT unfiltered | OPEN | none |
| 14 | return traffic for anything above | — | RELATED,ESTABLISHED ACCEPT | OPEN | none |
| 15 | mcp-pressure-guard / systemctl restart of desktop-commander-mcp | not a network path | — | unaffected | none |

The new rule has exactly three match conditions (`-i tailscale0`, `-p tcp`, `--dport
3100`); every open flow above fails at least one of them, and the closed flow (5) is the
only one that meets all three.

## Live checks apply.sh performs, in order, before it disarms rollback
1. `iptables -t raw -C PREROUTING …` present after insertion (the rule loaded).
2. `curl -m 3 http://127.0.0.1:3100/health` -> 200 (flow 3; the path this session rides).
3. `ssh bes 'curl -m 3 http://100.117.66.102:3100/health'` -> timeout/refused (flow 5 closed).
4. `ssh bes 'curl -m 3 http://100.117.66.102:4000/health/liveliness'` -> reachable (flow 7 unchanged).
5. `ssh bes 'nc -z -w3 100.117.66.102 22'` -> open (flow 1/2; also proven by the ssh in 3).
6. Only then: stop the rollback timer, `netfilter-persistent save`, verify persisted == live.

If any check fails, apply.sh exits without disarming: the timer deletes the rule at
T+180s and pages ntfy. If the session itself is cut (it should not be — flow 3 is lo),
the timer still runs: it is a detached systemd unit, not a child of the shell.
