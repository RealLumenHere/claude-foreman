# wq#2444: D300 resale, D700 pairing for the Mac Pro pool

Frank's plan (2026-09-24): test the D300s we own, sell the good pairs, and use the
money to put D700s in the Macs. If it pays, buy more Mac Pros to part out.

**Verdict:** the card swap does not pay for itself. Selling a D300 pair covers about
40% of a D700 pair, so each box costs about $155-175 net. The case for D700s is
inference (about 12 GB of VRAM across two cards), not profit, so buy **one** D700 pair
first and benchmark it on our own box before swapping any more. Details below.

## 1. What we actually own

The PCI ids come from lumen_state and the vendor subsystem ids from upstream `pci.ids`
(`106b:012a/012b` = D300, `106b:0125/0126` = D500, `106b:0127/0128` = D700):

| box | cards | PCI id | VRAM | source |
|-----|-------|--------|------|--------|
| isis | 2x D300 (Curacao XT) | 1002:6810 | 2x2 GB | `isis.hardware`, verified 2026-09-26 |
| hathor | 2x D300 (Curacao XT) | 1002:6810 | 2x2 GB | `hathor.hardware`, verified 2026-09-26 |
| nephthys | 2x **D500** (Tahiti LE) | 1002:679e | 2x3 GB | `nephthys.gpu` |
| khepri | 2x **D500** (Tahiti LE) | — | 2x3 GB | `khepri.gpu_dpm` says "Tahiti LE" |
| wadjet, +2 uninventoried | unknown | — | — | `mac-pool.hardware` |

**Correction:** `nephthys.gpu_dpm` says "both D300s", and `mac-pool.gpu_hardware` says
the cards are "all ~2GB". Tahiti LE is the D500, which has 3 GB. A D500 pair sells for
more than a D300 pair (§2), so list those four cards as D500. Before listing any card,
confirm it with `lspci -nn` and `mem_info_vram_total`. The SOP now has this table
(`docs/wq2714_d300_gpu_resale_sop.md`, "Which card is which").

**Physical constraint:** each Mac Pro 6,1 takes a matched A/B pair, and the boot SSD
blade plugs into one of the two GPU boards. You cannot pull GPUs out of a running pool
box without replacement cards. Also, isis lost power when its cards were disabled on
2026-09-19 (`isis.service_state`). So a D300 pair only becomes sellable once a
replacement pair is on hand, or when it comes out of a spare box that is being parted.

## 2. Testing the cards (task 1)

The harness already exists: `ops/wq2714-d300-gpu-resale-validation/` with the SOP at
`docs/wq2714_d300_gpu_resale_sop.md` (wq#2714). It runs a headless bind check,
`memtest_vulkan`, and a compute soak, then a manual FurMark/glmark2 check with a monitor
attached.

- **Fixed in this PR:** the wq#2716 verify-pass found that the grade log recorded
  `TOOL_MISSING` as `FAIL`. On a new rig with no tools installed, that would have marked
  good cards as scrap. `orchestrator.sh` now copies each stage's own result from
  `<stage>.json` into the log. `ops/tests/test_wq2444_resale_grade_rollup.py` has 5
  tests. All pass with the fix, and 2 fail with the old orchestrator (checked by
  stashing the fix).
- **Not run on real cards.** This worker had no fleet-exec grant, so `grant_missing_env`
  blocked it and raw `ssh` is not allowlisted (ISSUE-LOG). Stage 1 needs no hands and can
  run in place on the box that holds the cards. The next step is a redispatch with
  `needs: ssh(hathor), ssh(khepri)`: install `vulkaninfo`/`clinfo`/`memtest_vulkan`/
  `clpeak` and run stage 1 on each card. Start with hathor and khepri. Leave isis alone,
  because its GPU state has already caused one power-loss incident.
- The VBIOS dumps on maksoor:`~/re/macpro_vbios/` are reference material only. A VBIOS
  that reads out cleanly does not show that the core or VRAM is healthy.

## 3. Prices (task 2)

These are **asking** prices from SearXNG/Google CSE on 2026-09-28. eBay's sold-listings
filter could not be fetched (engines hit CAPTCHAs, and WebFetch is not permitted for
this worker). Before setting a price, Frank should check the "Sold items" filter.

| item | asking | source |
|------|--------|--------|
| D300 matched A+B pair | $94.12 | eBay search snippet |
| D500 matched A+B pair | $119.99 (eBay) to $230.87 (Part Haven) | eBay, parthaven.com |
| D700 A+B pair + screws | $225-250 (eBay, OBO); $243.57 (Part Haven) | eBay, parthaven.com |
| 6-core 3.5 / 32 GB / D300 system | $199.99 | eBay refurb |
| 6-core 3.5 / 64 GB / D500 system | $205 + $24.96 ship | eBay |
| 12-core 2.7 / 64 GB / 1 TB / D700 system | $450-460 | eBay pre-owned |

Swap math for one box, assuming about 13% eBay fees and about $12 shipping:

| step | amount |
|------|--------|
| D300 pair sells for $94, about $70 net | +$70 |
| D700 pair costs about $230-245 delivered | −$230 to −$245 |
| **net cost per box** | **−$160 to −$175** |

D500 boxes come out about $20-40 better per box.

**The parting-out loop** does not work on GPUs alone. A 12-core D700 system costs
about $450, which is roughly the D700 pair ($230) plus a $200 residual for the CPU, 64 GB
of RAM, SSD, and chassis. A cheap D300 system at about $200 gives a D300 pair worth
about $70. Whether parting out makes money depends on CPU, RAM, and SSD resale, which I
did not price. Also, most buyers of a whole-box D300 system are the same buyers who
would buy it intact, so the margin is thin. It is only worth doing when a box is priced
well below $200.

## 4. Would D700s help inference? (task 3)

- **D300 (2 GB) and D500 (3 GB):** too small. Two D500s give 6 GB, which fits nothing
  better than what isis already runs on CPU. lumen_state's "too small" verdict stands for
  these cards.
- **D700 pair (2x6 GB = 12 GB):** prior art shows this is real. kovasky.me ran llama.cpp
  with Vulkan (RADV) on dual D700s, kernel params `amdgpu.si_support=1 radeon.si_support=0`:

| model | prefill pp2048 (tok/s) | decode tg32 (tok/s) | decode @32k ctx (tok/s) |
|-------|-----------------------:|--------------------:|------------------------:|
| gpt-oss-20b Q4_K_S | 157.85 | 15.14 | 13.15 |
| Qwen3.5-9B UD-Q4_K_XL | 168.27 | 12.52 | 11.25 |
| Gemma-4 26B-A4B IQ4_XS | 52.22 | 8.84 | — |

  Our CPU-only baseline on the same MacPro6,1 is isis running qwen3.6-35b-a3b: 60 tok/s
  prefill, 10.9 decode. Hathor running llama3.1:8b on Ollama does 13.6 prefill
  (`research/INFERENCE_NODE_REGISTRY.md:49-53`). With D700s, a 20B-class model does
  about 2.6x isis's prefill, and prefill is the bottleneck for agent-sized prompts.
  These are different models on someone else's box, so they point in a direction but do
  not prove anything. Our 35B-A3B at Q4 is about 20 GB and will not fit in 12 GB. The
  win would come from routing a model that fits in 12 GB (the gpt-oss-20b / 9B class) to
  D700 boxes. The registry would also need its "CPU-only" row changed per box.
- Power was not measured. Idle draw is about 85 W CPU-only (hathor), and a D700 under
  load will add a lot to that.

## 5. Recommendation

1. **Don't pre-sell the D300s.** They can't come out of pool boxes without replacements,
   and selling them covers only about 40% of a D700 pair.
2. **Pilot one D700 pair (about $230, a physical task for Frank).** Put it in khepri: its
   D500s are worth more than D300s, and it isn't the always-warm node. Then run the
   kovasky llama.cpp Vulkan recipe against the registry baseline. Continue only if a model
   we actually route gets **≥2x prefill**. If it doesn't, resell the pair at about cost,
   losing only fees (about $30).
3. **If the pilot passes,** swap one box at a time. Each swap frees a D300 or D500 pair.
   Grade each freed pair with the wq#2714 harness while it is still in its box, sell the
   PASS pairs as "tested working" and the FAIL pairs as parts.
4. **Buy whole Mac Pros for parts only below about $200 delivered,** and only after the
   pilot. Above that, the D700-system residual doesn't leave a margin.

## Evidence

- Harness fix: `ops/wq2714-d300-gpu-resale-validation/orchestrator.sh` (`run_stage`).
  Test: `python3 ops/tests/test_wq2444_resale_grade_rollup.py`. 5/5 OK with the fix,
  FAILED (failures=2) with the old orchestrator.
- PCI ids: upstream `pci.ids` lines 2998/3011, 3037/3038, 3216/3217.
- Market data: `fleet-search web` queries on 2026-09-28 (listed in §3). D700 benchmark:
  https://kovasky.me/blogs/mac_pro_trashcan_ai/. Community guide:
  https://www.reddit.com/r/ollama/comments/1rd5ely/.
