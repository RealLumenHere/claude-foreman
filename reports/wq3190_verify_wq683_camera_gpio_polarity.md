# wq#3190 verify: wq#683 [questfree] Mainline kernel port: Quest 1 DTS (msm8998-monterey)

Verdict: **CONTRADICT** (escalates per ticket policy to opus-5@high judgment)

PR: lumen/questfree PR#17, merged to `main` as `ad79140` (identical content to the
author's branch tip `1c159f2`, `feat/wq683-questfree-mainline-kernel-port-quest-1-d-a4`).
Diff verified: `git diff ed3ea6d ad79140 -- docs/dts/msm8998-oculus-monterey.dts`
(126 lines added: camera, DSI panel, audio codec sections, lines 505-629 of the
merged file).

## What wq#683/PR#17 claimed

Commit message (`ad79140` / `1c159f2`): extend
`docs/dts/msm8998-oculus-monterey.dts` with three Quest-unique peripheral blocks
— 4x OV7251 tracking cameras, dual NT35597 DSI panels, CM710x audio codec — kept
as disabled/commented documentation blocks (no upstream driver, no verified
mainline binding label). States: "GPIO/regulator/clock/register values are RE'd
from `quest1_decompiled.dts`" citing line ranges 6146-6276 (cameras),
14680-14748 (DSI panel), 17214-17232 (cm710x).

wq#683's own done-notes explicitly scope out the ticket's original milestone
("kernel boots to initramfs, ADB works, peripherals enumerate") as unreachable
for a headless worker (no `gitlab.com/msm8998-mainline/linux` fork, no physical
Quest 1). That scope narrowing is disclosed, not hidden, and is reasonable given
tier-0 constraints — not itself contradicted here.

## What I checked

Read the diff cold, then cross-referenced every RE'd value in the new DTS
comment blocks against the cited `docs/quest1_decompiled.dts` line ranges
(18553-line file, present in this worktree/repo — reachable, unlike the prior
wq683 attempt logged at `kb/ISSUE-LOG.md #56`).

- **Cameras** (`quest1_decompiled.dts:6146-6276`, `oculus,camera@0..3`):
  - `csiphy-sd-index`/`csid-sd-index`/`cci-master` per camera (0/0/0, 1/1/0,
    2/2/1, 2/3/1) — match source exactly.
  - `mount-angle = <90>` (source `0x5a`) — matches all 4.
  - `reset-gpios = <&tlmm N GPIO_ACTIVE_HIGH>` — source `gpios = <0x66 N 0x00>`
    (phandle `0x66` = `pinctrl@03400000`, `compatible = "qcom,msm8998-pinctrl"`,
    `gpio-controller; #gpio-cells = <2>` at `quest1_decompiled.dts:9530-9538` —
    a standard gpiolib controller, so the second cell follows
    `include/dt-bindings/gpio/gpio.h`: `0x00 = GPIO_ACTIVE_HIGH`,
    `0x01 = GPIO_ACTIVE_LOW`). Flag `0x00` → `GPIO_ACTIVE_HIGH` is correct for
    all 4 cameras' reset pins.
- **DSI panel** (`quest1_decompiled.dts:14680-14748`): `panel-width-px=720`,
  `panel-height-px=2560`, `framerate-min=55`/`max=60`, `width-mm=74`,
  `height-mm=131`, `dual-dsi-split="dualctl-split"` — all match source
  (`0x2d0`, `0xa00`, `0x37`/`0x3c`, `0x4a`, `0x83`, `config0.qcom,split-mode`).
  No defect found here.
- **Audio codec** (`quest1_decompiled.dts:17214-17232`): `reg=0x2c`,
  `clock-names="osr_clk"`, bus address `0xc1b6000` (parent `i2c@c1b6000`,
  confirmed shared with `max98927@3A` at the same bus, matching the header
  comment's "sharing the bus with a max98927 speaker amp") — all match source.
  No defect found here.

## Contradiction found (CONFIRMED)

`powerdown-gpios = <&tlmm N GPIO_ACTIVE_HIGH>` is wrong for **all 4 cameras**.
Source `pwdn-gpios = <0x66 N 0x01>` (`quest1_decompiled.dts:6165, 6198, 6231,
6264`) — flag `0x01` on the same standard-cells gpio controller used above is
`GPIO_ACTIVE_LOW`, not `GPIO_ACTIVE_HIGH`. This is not an ambiguous binding: the
same node's `reset-gpios` correctly converts flag `0x00`→`GPIO_ACTIVE_HIGH`
using this exact mapping, so the author demonstrably applied the right
conversion to one property and the inverted one to the adjacent property in the
same node, for all four cameras (camera0 pin 137, camera1 pin 138, camera2 pin
139, camera3 pin 140 — `0x89`-`0x8c`).

Because this doc's stated purpose is "documentation and future driver
development" (header comment, `msm8998-oculus-monterey.dts:15-16`), a future
implementer copying these values into a live driver would assert the
powerdown line backwards on all 4 sensors. The nodes are currently
comment-wrapped and `status = "disabled"`, so nothing compiles or runs today —
this cannot regress a build — but it is a factual inaccuracy in the one thing
this PR was for: faithfully transcribing RE'd values.

## Secondary observation (not escalation-worthy on its own)

The commit message states "GPIO/**regulator**/clock/register values are RE'd
from `quest1_decompiled.dts`," but no regulator-supply properties appear
anywhere in the three added blocks, despite the cited camera range defining
three named+valued supplies per sensor (`cam_vio`/`cam_vana`/`cam_vdig`,
`quest1_decompiled.dts:6154-6160` etc.) and the cited cm710x range defining
three more (`dvdd-core`/`avdd-core`/`amic-core`,
`quest1_decompiled.dts:17217-17222`). The commit's own description overstates
what was captured; the omission itself is defensible (no verified mainline
regulator name/phandle to attach to without kernel-tree access, same "left
undocumented rather than guessed" doctrine used elsewhere in this PR) but the
claim should have said so rather than listing "regulator" as RE'd.

## Things I looked for and did not find a contradiction in

- Comment-block nesting/DTS syntax: each of the 3 new blocks is a standalone
  `/* ... */` explanatory header followed by a separate, properly closed
  `/* node-body */` comment — no unterminated or nested C comments introduced.
- `cm710x@2c` compatible string casing (`c-media,cm710x` vs. source's
  `C-Media,cm710x`) — lowercasing the vendor prefix is correct DT convention,
  not a transcription error.
- Could not run `dtc -@` / `cpp` compile validation (no kernel tree checkout
  reachable from this worktree — same constraint wq#683 itself hit, and moot
  here since the added content is entirely comment-wrapped and does not affect
  compilation either way).

## Action taken

Contradiction posted to wq#683 via `wq note`. Not re-implementing/fixing the
DTS per ticket instruction ("Do NOT re-implement; verify"). Fix for whoever
picks up the follow-up: flip `powerdown-gpios` on all 4 `oculus_cameraN` nodes
from `GPIO_ACTIVE_HIGH` to `GPIO_ACTIVE_LOW`
(`docs/dts/msm8998-oculus-monterey.dts:533,545,557,569`), and either add the
regulator-supply properties (voltage values are in
`quest1_decompiled.dts:6154-6160`/`17217-17222`) or soften the commit-message
claim to match what's actually documented.
