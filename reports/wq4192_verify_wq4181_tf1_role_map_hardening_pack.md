# wq#4192 — verify-pass on wq#4181 (spectrio TF1 appliance role map + Armbian hardening pack)

PR `claude-foreman#2127` (http://100.121.17.5:3000/lumen/claude-foreman/pulls/2127),
commit `8708f266` on `main`. 13 files / 716 insertions under
`projects/spectrio-tf1/roles/`. Author: claude-sonnet-5. Verifier: claude-sonnet-5@high
(fresh instance, wq#4192).

## Claim under test

Adds 3 config-only TF1 appliance roles (tf1-a exit node, tf1-b external-vantage
Gatus+ntfy, tf1-c tailnet-only ADB host) plus a shared `harden-armbian.sh` hardening
pack, with `render_role.py`/`roles.yaml` schema validation and a 16-case
`test_render_role.py`. README explicitly disclaims hardware testing: "nothing here has
been applied to real hardware ... it cannot and does not claim a role boots." The
disclaimer covers *whether it runs on real silicon*; it does not cover whether the
artifact is internally consistent or whether its own described layering model
("Every role runs `harden-armbian.sh` first, then its own role script") actually holds
for all three roles.

## What I checked

1. **Diff cold**: `git show --stat 8708f266` matches the PR's claimed 13 files / 716
   insertions.
2. **Ran the shipped probe**: `python3 render_role.py --validate` → `OK: 3 roles valid`.
   `python3 -m pytest test_render_role.py -q` → **16 passed**. The negative cases
   (`test_invalid_roles_are_rejected`) genuinely exercise outside-population inputs
   (powersave governor on an exit node, allow-all ACL on the vantage box, non-tailnet
   ADB host) — a real falsifiable probe, not a rubber stamp.
3. **tf1-a (`tf1-a-exit-node.sh`) and tf1-c (`tf1-c-adb-host.sh`)**: both are
   self-contained — every systemd unit and firewall script is written via
   `install -m ... /dev/stdin <<HEREDOC` to a canonical absolute system path
   (`/etc/systemd/system/*.service`, `/usr/local/sbin/*.sh`, `/etc/sysctl.d/*.conf`),
   with no dependency on where the generating script itself lives. tf1-c's iptables
   firewall (`-C ... || -I`/`-A` guard) is correctly idempotent and correctly orders the
   tailscale0 ACCEPT (`-I`, inserted at chain head) before the blanket DROP (`-A`,
   appended at chain tail), so re-running it is safe and the rule order is correct.
4. **`harden-armbian.sh` idempotency** (its own header claims "Idempotent: re-running
   after a reboot or re-apply is safe"): every step except the fstab edit is genuinely
   idempotent (grep-guarded HDMI cmdline edit, `install` overwrites for udev/journald/
   watchdog configs, `systemctl enable`, `rfkill block`). The fstab step
   (`harden-armbian.sh:57`) is not:
   ```
   sed -i -E 's/^([^#].*[[:space:]]ext4[[:space:]]+)([^[:space:]]+)([[:space:]]+[0-9]+[[:space:]]+[0-9]+)$/\1\2,noatime,commit=600\3/' /etc/fstab
   ```
   has no guard against the options field already containing `noatime`/`commit=600`.
   Verified directly: running the substitution twice against
   `UUID=abc / ext4 defaults 0 1` yields
   `defaults,noatime,commit=600,noatime,commit=600` on the second pass; running it once
   against an fstab line that **already** has `noatime,commit=600` (plausible — Armbian
   SBC images commonly ship eMMC-tuned mount options out of the box) duplicates on the
   *first* run too. `test_hardening_script_covers_every_ticket_requirement` only
   `grep`s the script's static text for the substring `"noatime,commit=600"` — it never
   executes the sed against a sample fstab, so this never surfaces in the shipped suite.
   Contradicts the script's own documented idempotency claim; moderate severity (mount
   tolerates duplicate options, but it's wrong and compounds on every re-apply).
5. **tf1-b (`tf1-b-external-vantage/`) — tried to break the central "same layering as
   tf1-a/c" claim.** `roles.yaml`'s `tf1-b-external-vantage` entry has `config_dir:
   "tf1-b-external-vantage"`, not a `config_script` (unlike tf1-a/tf1-c, which both have
   one). Checked whether anything else installs the role: `find
   projects/spectrio-tf1/roles -maxdepth 1 -type f` and the `tf1-b-external-vantage/`
   directory listing together show only `gatus-config.yaml`, `gatus.service`,
   `launch-gatus.sh`, `launch-ntfy.sh`, `ntfy-server.yml`, `ntfy.service` — no script
   anywhere in the diff copies `gatus.service`/`ntfy.service` to
   `/etc/systemd/system/`, and `render_role()` (`render_role.py:91-98`) only ever emits
   `systemctl enable --now <svc>` for whatever is in `role["services"]` — it assumes the
   unit files are already in place, which is true for tf1-a/c (their `config_script`
   installs them) but **never becomes true for tf1-b**. The README's own "Layering"
   section claims uniformity across all three roles ("Every role runs
   `harden-armbian.sh` first, then its own role script") — tf1-b has no role script,
   contradicting that claim about its own artifact.

   Independently, `gatus.service`/`ntfy.service`'s `ExecStart` hardcodes
   `/home/lumen/projects/spectrio-tf1/roles/tf1-b-external-vantage/launch-gatus.sh` (and
   the `launch-ntfy.sh` equivalent) — a path under **Lumen's** home directory, not a
   path on the tf1-b appliance (a separate physical Armbian S905X3 box with its own
   root filesystem). This path doesn't even match where these files live in the repo
   under verification (`/home/lumen/foreman/projects/spectrio-tf1/roles/...` — note
   "foreman" is missing), so it isn't even self-consistent with this checkout, let
   alone resolvable on the target device. tf1-b's entire stated purpose
   (`roles.yaml:58`, README `covers_gap`) is to monitor and alert on Lumen "from
   OUTSIDE its own network, with an alert path independent of Lumen itself" — a service
   unit whose `ExecStart` only resolves on Lumen's own filesystem directly undermines
   that independence claim, on top of simply not existing on the appliance.
   `test_external_vantage_covers_every_ticket_requirement` (`test_render_role.py:78-89`)
   never inspects `gatus.service`/`ntfy.service` at all — confirmed via
   `grep -n "ExecStart\|gatus.service\|ntfy.service" test_render_role.py roles.yaml`:
   the only hits are the `services:` list entries in `roles.yaml`, nothing in the test
   file. This gap is invisible to the shipped probe.

   Checked whether this is an inherited precedent from wq#3430 (the earlier 5-role plan
   this PR extends): `grep -rn "ExecStart\|/home/lumen" ops/wq3430-spectrio-tf1-deploy/`
   finds nothing — wq#3430 never shipped any systemd unit files at all (its own
   `install.sh` says "nothing to install," config/schema only). tf1-a and tf1-c prove
   this PR's own author knows how to ship a working self-contained install script; tf1-b
   simply doesn't have one. This is a defect novel to this PR, not an inherited gap.

## Verdict

**CONTRADICT.** tf1-a and tf1-c are sound: self-contained install scripts, correct
idempotency, correct firewall rule ordering, real negative-case test coverage. tf1-b is
not: it has no install mechanism at all (unlike its two siblings in the same PR, and
contradicting the README's own claim that every role shares the same
hardening-pack-then-role-script layering), and the systemd units it does ship point
`ExecStart` at a path on Lumen's own filesystem rather than anywhere on the tf1-b
appliance — undermining the specific "independent of Lumen" property that is tf1-b's
reason for existing. None of this is caught by the shipped 16-case suite, which never
inspects `gatus.service`/`ntfy.service` or exercises `harden-armbian.sh`'s fstab edit
against sample input. The config-only/no-hardware-grant disclaimer in the README covers
"has this booted on real silicon" (no, and that's disclosed); it does not cover "is the
artifact internally consistent with its own documented design," which is where tf1-b
fails.

Per archetype 8, this escalates to opus-5@high for judgment: whether tf1-b needs a
`config_script` (mirroring tf1-a/c's self-contained heredoc-install pattern, with
`ExecStart` pointing at wherever the role script places the launch scripts on the
appliance, not at a Lumen-side path) before this can be considered equivalent in
quality to its sibling roles, and whether the `harden-armbian.sh` fstab step should gate
on `noatime`/`commit=` already being present (or switch to an idempotent tool like
`findmnt`/`tune2fs` option-setting) before its "idempotent" claim is accurate.

No code changes made by this verify-pass (archetype 8 is verify-only). Scratch fstab
repro files left under `/tmp/fworker-wq4192/` (outside both repos).
