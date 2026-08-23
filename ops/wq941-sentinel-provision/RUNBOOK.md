# wq#941 — SENTINEL P0 provisioning runbook (M70s Gen 3)

Design: `/home/lumen/decisions/SENTINEL_PRIME_DESIGN.md` §1, §7 (P0).
Hardware: Lenovo ThinkCentre M70s Gen 3, MT-M 11T7, S/N MJ0JDCVP. No GPU (deliberate — design §1).

Status: **blocked on physical work.** As of 2026-08-23 there is no `sentinel` node on the
tailnet — the machine has no OS, disks, or final RAM config. Steps marked **[HANDS]** need
Frank at the machine; everything else a fleet worker can finish over SSH once the node joins
the tailnet.

## 1. [HANDS] Disks + first boot

- Install **1TB NVMe (M.2 2280)** — OS + models + sentinel's own metric/log history.
- Install a **separate SATA SSD** — sentinel state DB only (full disk on one volume must not
  kill both; design §1).
- Install OS (Debian stable or Ubuntu LTS server, minimal). Hostname: `sentinel` — named for
  its function, not the Egyptian worker convention (design §1).
- Mount the SATA SSD at `/var/lib/sentinel` (its own filesystem; `nofail` in fstab so a dead
  SSD doesn't hang boot).

## 2. [HANDS] Power

- **UPS/battery-backed, on a DIFFERENT circuit from Lumen.** Independence is the whole point.
- Record which circuit each of Lumen and sentinel is on, in the PR that closes this ticket.
- BIOS: set "restore power on AC loss" so it self-recovers after an outage.

## 3. Tailnet join [HANDS to paste the auth key; rest is on-box]

```sh
curl -fsSL https://tailscale.com/install.sh | sh
tailscale up --hostname=sentinel
```

Verify from Lumen: `tailscale status | grep sentinel` and `ssh sentinel true`.

## 4. CPU identification (design §8 Q1)

```sh
lscpu | grep -E 'Model name|Core|Thread'
```

Record the exact SKU (i5-12400/12500/12600) in the ticket notes and in `lumen_state.py`.

## 5. RAM benchmark — decide 2x32 @3200 vs 4x32 @2666/2933 (the consequential choice)

Token throughput on the MoE path is memory-bandwidth bound. Consumer Alder Lake boards
commonly downclock when all 4 slots are populated. Measure, don't assume.

For EACH config (**[HANDS]** to swap DIMMs between runs):

```sh
# a) what the board actually negotiated
sudo dmidecode -t memory | grep -E 'Configured Memory Speed|Size:' | grep -v 'No Module'

# b) raw bandwidth (Intel MLC; or sysbench memory as fallback)
./mlc --max_bandwidth
# fallback: sysbench memory --memory-block-size=1M --memory-total-size=64G run

# c) the number that matters — tok/s on the candidate model
# build llama.cpp (CPU-only), then:
./llama-bench -m Qwen3-30B-A3B-Q4_K_M.gguf -p 512 -n 128 -t $(nproc)
```

**Keep whichever config wins (c).** Record both configs' dmidecode speed, bandwidth, and
tok/s in the ticket notes. Expected: ~10-15 tok/s on dual-channel DDR4-3200 (design §2).

## 6. Model benchmark + resident config (ticket item 6)

Candidates (Q4 quant): Qwen3-30B-A3B (primary, ~3B active/token); benchmark at least one
alternative in class before committing. For each: `llama-bench` tok/s + resident RSS.

Serving config (ollama or llama-server), non-negotiables from design §2:
- **Permanently resident**: `OLLAMA_KEEP_ALIVE=-1` (or keep llama-server running under systemd).
- **>= 2 concurrent slots** (`OLLAMA_NUM_PARALLEL=2` / `--parallel 2`) — Isis's single-slot
  saturation is the named anti-pattern.
- Local only. No LiteLLM, no cloud fallback.

## 7. Register the node

Add `sentinel` to `/home/lumen/minerva/lumen_state.py` (separate PR in minerva) with the
verified CPU SKU, final RAM config + measured tok/s, disk layout, and role
`fleet-response-node`. **NO FLEET AUTHORITY in this phase** — registration is inventory only;
P1 (Pulse, observe-only) is a separate gated ticket.

## Acceptance for closing wq#941

1. `ssh sentinel` works from Lumen over tailnet.
2. CPU SKU recorded; RAM config chosen by measured tok/s (both configs' numbers recorded).
3. NVMe + separate SATA state volume mounted per §1.
4. UPS on a different circuit than Lumen, circuits documented.
5. Chosen model resident with >=2 slots; tok/s recorded.
6. Node registered in `lumen_state.py` with no authority granted.
