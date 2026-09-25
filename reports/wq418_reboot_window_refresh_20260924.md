# wq#418 — reboot window: refreshed 2026-09-24. FRANK DECISION REQUIRED.

The ticket's facts were from 2026-08-08, seven weeks old. Everything below is measured
today, read-only. **Nothing was rebooted, and nothing was changed.**

Headline: the 12-node batch **cannot run as a batch**. Only **2 of 12** nodes can be
proven to come back from a reboot.

## 1. Reboot-safety gate, all 12 nodes

Run with `lumen-reboot-gate` (shipped today under wq#1401), which implements the
triple-check `maintenance-window.md` requires.

| node | auto-return | why |
|---|---|---|
| **bes** | **PASS** | BIOS AC-restore on **and** p7 controllable |
| **nephthys** | **PASS** | `auto_boot_verified=yes`, full cycle validated 2026-07-31 |
| anubis | FAIL | on controllable p9, **no BIOS AC-restore fact** |
| isis | FAIL | on controllable p1, no AC-restore fact |
| thoth | FAIL | on controllable p10, no AC-restore fact |
| seshat | FAIL | on controllable p6, no AC-restore fact |
| lumen | FAIL | on controllable p11, no AC-restore fact |
| maat | FAIL | on controllable p4, no AC-restore fact |
| sia | FAIL | on controllable p8, no AC-restore fact |
| sobek | FAIL | AC-restore on, but **unmetered — no plug at all** |
| apep | FAIL | nothing recorded, and **offline 11d** |
| thor | FAIL | nothing recorded (no plug, no AC-restore fact) |

**The good news: 7 of the 10 failures are a missing *fact*, not missing hardware.**
anubis, isis, thoth, seshat, lumen, maat and sia all sit on controllable Tapo plugs. If
their BIOS AC-restore is actually set, they become gate-eligible the moment that is
recorded — no hardware, no spend. The gate refuses today because nobody wrote it down,
and the policy is explicit that unknown auto-return is a refusal (thoth/apis lessons).

Full gate on the two that pass — both **REBOOT-SAFE**, all three legs green:

```
bes       auto_return PASS | services_enabled PASS | boot_blocking PASS  -> REBOOT-SAFE
nephthys  auto_return PASS | services_enabled PASS | boot_blocking PASS  -> REBOOT-SAFE
```

## 2. Is a reboot even still needed? Mostly yes, and it has drifted further

| node | pending | running kernel |
|---|---|---|
| anubis | yes — linux-image-7.0.0-31, linux-base, libc6 | 7.0.0-30 |
| bes | yes — linux-image-7.0.0-31, linux-base, libc6 | 7.0.0-30 |
| thoth | yes — linux-image-7.0.0-31 | 7.0.0-30 |
| maat | yes — linux-image-7.0.0-31, linux-base, libc6 | 7.0.0-30 |
| sia | yes — linux-image-7.0.0-31, linux-base, libc6 | 7.0.0-30 |
| isis | yes — linux-image-7.0.0-34, linux-base | 7.0.0-31 |
| nephthys | yes — linux-image-7.0.0-34, linux-base | 7.0.0-31 |
| **sobek** | **no** — already rebooted | 7.0.0-31 |
| **thor** | **no** — already rebooted | 7.0.0-34 |

apep (offline 11d) and seshat (offline 5d) could not be checked.

Five nodes are now **four** kernel releases behind (-30 vs -34 available). The libc6
updates in particular are the reason this should not sit another seven weeks.

## 3. The two side decisions in the ticket

**(a) sia NOPASSWD sudoers — still blocked, still open.**
`ssh sia 'sudo -n true'` exits **1**; `/etc/sudoers.d/` contains only `README`. Automation
on sia is still fully blocked exactly as reported on 2026-08-08. Unchanged, needs your go.

**(b) litellm digest swap — the premise has moved.**
The container is running `sha256:6c82d338a60e7b273ae46bf1d8db814d2856ae010f96c44eeadde574d3893f76`,
up 3 days and healthy. That is **not** the `50e647bd…` digest the ticket proposed swapping
to, so the question is no longer "swap to 50e647bd" but "is the digest we are actually on
the one we want". Worth re-deriving before any supply-chain review is spent on the old one.

**(c) Lumen dbx firmware — NOT APPLICABLE AS WRITTEN.**
The ticket says to run `sudo fwupdmgr update` for the CVE-2026-8863 dbx revocations while
you are present. On Lumen today:

```
UEFI dbx: current version 20230501
UEFI secure boot:  Disabled
UEFI db:           Invalid
WARNING: UEFI capsule updates not available or enabled in firmware setup
```

Two blockers. Capsule updates are not enabled in firmware setup, so `fwupdmgr update`
cannot apply anything; and **Secure Boot is disabled**, so dbx revocations have no
enforcement effect while that stays true. This is a firmware-setup change first, which is
a keyboard-at-the-box job — consistent with "Lumen requires Frank present", but it is a
BIOS visit, not a command.

## 4. What I recommend, for your decision

1. **Reboot bes and nephthys** — both fully gate-clean, both have pending kernel/libc.
2. **Record BIOS AC-restore for the 7 plugged nodes**, then re-run the gate. That likely
   converts most of the batch to eligible at zero cost. If the BIOS setting is not
   actually on, that is the real finding and worth knowing before a window.
3. **sobek** needs a decision of its own: AC-restore is on but it is on no plug, so there
   is no remote recovery path if it hangs on the way down.
4. **thor, apep** — nothing recorded at all; do not reboot until there is.
5. Treat (a), (b), (c) as three separate decisions; none of them blocks the reboots.

## 5. Copy-paste, if you want to act

Re-check any node's gate (read-only, reboots nothing):

```
ssh lumen@100.117.66.102 '/home/lumen/bin/lumen-reboot-gate bes'
ssh lumen@100.117.66.102 '/home/lumen/bin/lumen-reboot-gate nephthys'
```

Record an AC-restore fact once you have confirmed the BIOS setting on a node
(example shown for anubis — only run it for a node you have actually checked):

```
ssh lumen@100.117.66.102 'python3 /home/lumen/minerva/lumen_state.py ingest anubis bios "AC-restore=PowerOn — confirmed by Frank <date>"'
```

I did not reboot anything, and I am not proposing that any agent do so. Per standing
rule, no node reboots without your explicit go.
