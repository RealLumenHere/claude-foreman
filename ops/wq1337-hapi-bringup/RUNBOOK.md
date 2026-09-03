# wq#1337 — Hapi bring-up runbook: boot-to-benchmark for hapi-1 (BC-250)

SOT: `/home/lumen/projects/bc250/BC250_PROJECT.md` — "CURRENT STATE & DECISIONS" block,
read that first; it can move faster than this file. This runbook exists so card 1 goes
boot-to-benchmark the day thermal pads (Gelid Xtreme 2.0mm back / 1.5mm front + PTM7950)
land — no bench dependency to write or dry-run everything short of the physical card.

**Staging note:** the ticket asked for delivery under `/home/lumen/projects/bc250/runbook/`.
That path is outside this worker's worktree and not in `harness/agents.yaml:36-43`
add_dirs (same gap as ISSUE-LOG #129, hit again on this attempt — `bc250` still absent
from the default grant list as of this run). Staged here instead, at the foreman repo's
established `ops/wq<id>-<slug>/RUNBOOK.md` convention (see `ops/wq941-sentinel-provision/`,
`ops/wq1184-gatus-health-checks/`). Move/copy this directory to
`/home/lumen/projects/bc250/runbook/` once a worker or Frank has write access to that path
— content does not change, only location.

**Nothing here is live.** No service is installed, no card is touched, the LiteLLM rung
(§8) is staged DARK. This is paper + scripts only.

---

## 0. Preconditions (physical, [HANDS])

- PTM7950 applied to the APU die; Gelid Xtreme pads (2.0mm back / 1.5mm front) applied.
  Confirm the GDDR6 backplate has a metal spreader/plate for the pad to dump heat INTO —
  a pad against bare PCB does nothing (BC250_PROJECT.md line 19).
- Bench fan (80mm side-mount) in place for initial validation only — not the 24/7 airflow
  path.
- Card networked: 1GbE to the GL-iNet-uplinked switch, DHCP reservation once the MAC is
  known. Join Tailscale for remote mgmt (`tailscale up --hostname=hapi-1`) so the rest of
  this runbook can run over SSH instead of a keyboard on the bench.

## 1. OS install

Ubuntu 24.04 LTS **server**, minimal, headless — no display server; the card has no
video output path worth using and 40CU inference needs every watt on compute
(BC250_PROJECT.md line 169: "headless SSH + systemd-networkd only").

## 2. 40CU unlock

The SOT doc names "duggasco 6.12-40cu kernel" — **verified against the live repo this
attempt and corrected**: `duggasco/linux-bc250-40cu` does not exist on GitHub, and there
is no `6.12-40cu` branch anywhere in duggasco's repos (checked via GitHub API,
2026-09-03). The real, live repo is **`duggasco/bc250-40cu-unlock`** (default branch
`main`, only branch `main`): "Re-enable all 40 CUs on the AMD BC-250 (gfx1013 / Cyan
Skillfish). Kernel patch + build script. 1.61x compute scaling verified." It is a kernel
**module** patch (rebuilds `amdgpu.ko` against your existing kernel headers via DKMS-style
build), not a full custom kernel — no need to boot a different kernel image.

```sh
sudo apt install -y gcc make zstd "linux-headers-$(uname -r)"
git clone https://github.com/duggasco/bc250-40cu-unlock.git
cd bc250-40cu-unlock
sudo ./scripts/bc250-enable-40cu.sh build
sudo ./scripts/bc250-enable-40cu.sh enable   # writes /etc/modprobe.d/bc250-40cu.conf, reboots
```

Register-level mechanism (for when the build script fails and manual patch is needed —
README "Option 2"): writes `CC_GC_SHADER_ARRAY_CONFIG` `0xfff80000`→`0xffe00000` and
`SPI_PG_ENABLE_STATIC_WGP_MASK` `0x7`→`0x1F`, guarded by `device == 0x13FE` and module
param `bc250_cc_write_mode=3`. Both registers are required — either alone is a no-op
(driver reports 40 CUs but SPI dispatches to 24, or vice versa).

### Verify (before touching boot params or governor)

```sh
dmesg | grep active_cu_number        # expect: active_cu_number 40
dmesg | grep bc250-40cu              # expect: mode=3 se=0 sh=0 CC=0xfff80000->0xffe00000 SPI=0x00000007->0x0000001f
amdgpu_top -d                        # 40-bit CU mask on GC_TCP_STATUS SE0_SE1__SH0_SH1__CU_MASK
RADV_DEBUG=info vulkaninfo --summary 2>&1 | grep num_cu   # expect: num_cu = 40
```

`amdgpu_top` is not in Ubuntu's repos; build from source (`cargo install amdgpu_top`) or
grab a release binary from `Umio-Yasuno/amdgpu_top`.

Optional: `./scripts/cu_map.sh` (in the cloned repo) before enabling, to record this
card's stock harvest pattern (which 16 of 40 CUs are fused off) for the fleet's shared
harvest-map dataset per the repo's README.

## 3. Boot params

Per SOT (BC250_PROJECT.md lines 36, 166), append to `GRUB_CMDLINE_LINUX_DEFAULT` in
`/etc/default/grub`:

```
amdgpu.ppfeaturemask=0xffffffff iommu=pt amdgpu.gttsize=14750
```

```sh
sudo sed -i 's/^GRUB_CMDLINE_LINUX_DEFAULT=".*"/GRUB_CMDLINE_LINUX_DEFAULT="amdgpu.ppfeaturemask=0xffffffff iommu=pt amdgpu.gttsize=14750"/' /etc/default/grub
sudo update-grub
sudo reboot
```

`amdgpu.gttsize=14750` (MiB) is what makes the 16GB unified GDDR6 pool usable as GTT for
the model beyond whatever BIOS UMA carves out — pairs with the BIOS step below.

## 4. BIOS: UMA 512MB dynamic [HANDS]

Enter BIOS setup, set the UMA (Unified Memory Architecture) frame buffer to **512MB,
Dynamic** mode (not a fixed/static allocation). This is the memory-unlock decision from
BC250_PROJECT.md line 36 — dynamic UMA + `amdgpu.gttsize=14750` is what gets the model
weights (Qwen3.6-35B-A3B IQ3, ~13GiB) into addressable GPU memory out of the 16GB total.

## 5. Mesa 25.0.2+ (oibaf PPA)

```sh
sudo add-apt-repository ppa:oibaf/graphics-drivers
sudo apt update
sudo apt install -y mesa-vulkan-drivers libvulkan1 vulkan-tools mesa-utils
```

**Do not settle for 24.3.x** — it has a RADV bug where subgroup broadcasts produce
incorrect matmul outputs (fixed in Mesa MR !31245; BC250_PROJECT.md line 168). Verify
after install:

```sh
vulkaninfo --summary | grep -i "Mesa\|driverVersion\|version"
glxinfo -B | grep -i "mesa\|OpenGL version"
```

Confirm the reported Mesa version is >= 25.0.2 before proceeding — if the PPA hasn't
caught up, hold and re-check rather than run inference on a known-bad matmul path.

## 6. Governor: cyan-skillfish-governor-smu

Repo `filippor/cyan-skillfish-governor`, **default branch is literally `smu`**
(verified via GitHub API 2026-09-03 — confirms the SOT doc's "smu branch" naming).
Binary and package name is `cyan-skillfish-governor-smu`; config lives at
`/etc/cyan-skillfish-governor-smu/config.toml` on every packaging path (AUR, COPR,
release archive installer, and the Debian package built via `cargo-deb`).

Ubuntu 24.04 has no prebuilt `.deb` in a repo — build one:

```sh
git clone --branch smu https://github.com/filippor/cyan-skillfish-governor.git
cd cyan-skillfish-governor
cargo install cargo-deb
cargo deb
sudo dpkg -i target/debian/cyan-skillfish-governor-smu_*_amd64.deb
```

(If `cargo`/rustc isn't already on the box: `sudo apt install -y cargo rustc`, or the
repo's "Generic Linux" path — download a GitHub Releases archive and run
`scripts/install.sh` — avoids the Rust toolchain entirely.)

### Pin 1800MHz / 65W

The governor is adaptive (load-target driven ramp between `[[safe-points]]`
frequency/voltage pairs), not a single static-clock switch — "pin 1800/65W" means: cap
the safe-points table so nothing above ~1800MHz is reachable, and set `[gpu]
set-method = "smu"` (not `"kernel"`) so limits route through the SMU API
(`bc250-collective`'s contribution) rather than raw kernel voltage control. Start from
the upstream example `config.toml` and truncate:

```toml
[gpu]
set-method = "smu"

[load-target]
upper = 0.80
lower = 0.65

[temperature]
throttling = 85
throttling_recovery = 75

[[safe-points]]
frequency = 500
voltage = 700

[[safe-points]]
frequency = 1175
voltage = 700

[[safe-points]]
frequency = 1400
voltage = 750

[[safe-points]]
frequency = 1600
voltage = 800

[[safe-points]]
frequency = 1700
voltage = 850

[[safe-points]]
frequency = 1800
voltage = 900
# do not add points above 1800MHz — this box is inference-only, not gaming clocks
# (BC250_PROJECT.md line 30: "Do NOT run 2 GHz gaming clocks 24/7")
```

There is no separate `power_limit_w` key in this config schema (checked the upstream
example file directly) — 65W is the *measured consequence* of an 1800MHz ceiling per
the SOT thermal table (line 240), not an independently configured limit. **Verify it
holds** with `bench.sh`'s power logging (§9) rather than trusting the config alone.

```sh
sudo systemctl start cyan-skillfish-governor-smu   # manual test run first
# then, once verified against a real load:
sudo systemctl enable --now cyan-skillfish-governor-smu
```

## 7. Vulkan environment (ROCm is a HARD NO — GFX1013 has no ROCm userspace support;
`HSA_OVERRIDE_GFX_VERSION` silently corrupts compute results, never use it)

```sh
export RADV_PERFTEST=aco
export RADV_DEBUG=noibs,nobinning
export VK_ICD_FILENAMES=/usr/share/vulkan/icd.d/radeon_icd.x86_64.json
# only if NaN outputs appear under multi-client 40CU load (known llama.cpp bug,
# dequantize_mul_mat_q shader, MR #6789 pending upstream):
export LLAMACPP_VULKAN_COMMAND_BUFFER_COUNT=1
```

These are baked into `llama-server.service` (§8) via `Environment=` lines so they apply
identically whether started by systemd or by hand.

## 8. Serve: llama-server, NOT Ollama

Ollama drops MoE throughput ~60 → ~25 tok/s on this model family (SOT line 34) — build
and run `llama.cpp`'s `llama-server` directly.

```sh
sudo apt install -y build-essential cmake libvulkan-dev glslc
git clone https://github.com/ggml-org/llama.cpp.git
cd llama.cpp
cmake -B build -DGGML_VULKAN=ON -DCMAKE_BUILD_TYPE=Release
cmake --build build --config Release -j"$(nproc)" --target llama-server
sudo install -m0755 build/bin/llama-server /usr/local/bin/llama-server
```

Model: **Qwen3.6-35B-A3B** (MoE, ~3B active params) at **IQ3** quant with **Q4_0 KV
cache**, target footprint ~13GiB (fits the 16GB unified pool with headroom for the OS +
KV cache growth at 10k ctx). Pull/quantize via whatever the fleet's standard GGUF source
is at deploy time (llama.cpp's own quantizer, or a pre-quantized HF repo) — left as a
deploy-time step since the exact GGUF artifact isn't pinned yet.

Systemd unit: `llama-server.service` (this directory) — installs to
`/etc/systemd/system/llama-server.service`, expects the model at
`/opt/hapi/models/qwen3.6-35b-a3b-iq3.gguf` (adjust `ExecStart` if the deploy path
differs). Serves OpenAI-compatible `/v1/completions` and `/completion` on `:8080`.

### Service account and model directory — do this BEFORE enabling the unit

The unit runs as `User=llama` / `Group=render` with `ReadWritePaths=/opt/hapi`. Neither
the account nor the directory exists on a stock Ubuntu 24.04 install, and systemd fails
the unit at start if it cannot resolve `User=` to a UID or if a `ReadWritePaths=` path is
missing — so `systemctl enable --now` below fails unconditionally without this step.

```sh
# render/video ship with udev on 24.04; create them only if this image lacks them
getent group render >/dev/null || sudo groupadd -r render
getent group video  >/dev/null || sudo groupadd -r video
# system account, no login shell, no home; -G covers running llama-server by hand (§7)
getent passwd llama >/dev/null || \
  sudo useradd -r -M -s /usr/sbin/nologin -G render,video llama
sudo mkdir -p /opt/hapi/models
sudo chown -R llama:render /opt/hapi
sudo chmod 0755 /opt/hapi /opt/hapi/models
```

`chown` uses `llama:render` (not `llama:llama`) because `Group=render` in the unit makes
`render` the service's *primary* group at runtime; group-owning the tree by `llama`'s own
group would leave `/opt/hapi` non-writable under `ReadWritePaths=`. If the GGUF is pulled
as root, re-run the `chown` afterwards so the model file is readable by the service.

```sh
sudo cp llama-server.service /etc/systemd/system/llama-server.service
sudo systemctl daemon-reload
sudo systemctl enable --now llama-server
systemctl is-active llama-server   # expect: active — anything else, `journalctl -u llama-server -n50`
curl -s http://127.0.0.1:8080/health
```

## 9. Benchmark: `bench.sh`

Resolves the SOT doc's open discrepancy (BC250_PROJECT.md lines 203-227): Source A
(Level1Techs + duggasco, 2048ctx) reports 8.4 tok/s generation at 40CU; Source B
(akandr, real-world deploy) reports 38-39 tok/s on a similar model. `bench.sh` runs the
**same card, same build** at 512 / 2048 / 10000 context to see where in that range
hapi-1 actually lands, and whether the gap is context-length-driven (akandr's own data
shows 27.2→20.7 tok/s degrading small→10K ctx, so some of the 7x gap is expected, not
all of it).

```sh
./bench.sh --host 127.0.0.1 --port 8080 [--soak]
```

Per context depth, logs to `bench-results-<timestamp>.jsonl`: prompt tok/s, generation
tok/s (from llama-server's own `timings` block in the completion response — no separate
profiler needed), package power (`hwmon` `power1_average` under
`/sys/class/drm/card*/device/hwmon/hwmon*/`), and a throttle flag (temperature at/above
the governor's `throttling` config value during the run, from the same hwmon `temp1_input`).

`--soak` runs a >60 minute continuous-generation loop against the GDDR6-heavy MoE path
and watches for the SOT pass/fail bar (line 25): **FAIL = crash or corrupted/garbage
output in the 30-60 minute window** — this box is headless with no framebuffer, so the
literal "pixel artifacts" criterion from the SOT doc doesn't apply; the proxy here is
(a) the llama-server process dying or hanging (no response within a generous timeout),
(b) `dmesg` showing a GPU reset / `VM_CONTEXT1_PROTECTION_FAULT` / ECC event, or (c)
generated output degenerating into repeated tokens or non-UTF8 garbage mid-run, which is
the same class of symptom a corrupted GDDR6 read would produce whether or not there's a
screen attached to show it as a pixel.

## 10. LiteLLM `hapi-1` rung — staged DARK

`litellm-hapi1-dark.yaml` (this directory) is a `model_list` entry ready to splice into
the fleet's live LiteLLM `config.yaml`, **not merged, not live**. It stays dark until
**wq#266** (hapi-1 quant eval vs Isis: candidate UD-Q2_K_XL Instruct-2507 vs Isis
`qwen3:32b`, pass bar `verdict_agree>=0.9 AND leaks=0 AND rationale>=3.5`) passes.
wq#266 itself is blocked on hapi-1's first boot — this file is what unblocks *it*, not
the other way around: once hapi-1 is serving per §8, wq#266's eval can run against it,
and only after that gate passes does this snippet get merged into the real config and
the rung goes live. See the file's own header comment for the exact activation steps.

## 11. Pass/fail summary

| Check | Bar | Source |
|---|---|---|
| 40CU unlock | `active_cu_number 40` in dmesg, `num_cu = 40` in vulkaninfo | §2 |
| Mesa version | >= 25.0.2 | §5, SOT line 167 |
| Governor ceiling | sustained package power ~65W at 1800MHz under load | §6, SOT line 240 |
| Generation throughput | measured at 512/2048/10k ctx, compare to both SOT sources | §9, SOT lines 206-227 |
| Soak | no crash, no GPU reset event, no output corruption for 60+ min | §9, SOT line 25 |
| wq#266 gate | `verdict_agree>=0.9 AND leaks=0 AND rationale>=3.5` vs Isis | §10 |

Only after all six hold does the LiteLLM rung (§10) get merged from dark to live.
