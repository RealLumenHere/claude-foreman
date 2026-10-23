# wq#4485 — Thor SSH hardening (prepared, not applied)

Fleet security audit 2026-10-22 (`/home/lumen/scratch/research/fleet-security-audit.md`)
flagged Thor: no explicit `PasswordAuthentication no`, `PermitRootLogin` left at
its commented-out default, UFW installed but inactive, fail2ban not installed.

## Why this directory exists instead of a landed change on Thor

Thor (`100.112.151.100`) is tier-0 hands-off — **never targeted** by any fleet
automation, documented in four independent places:

- `fleet-config/README.md`: "Thor is hands-off ... no restart/reboot/provisioning
  verbs, ever. It is in the inventory only as `readonly`."
- `fleet-config/docs/ARCHITECTURE.md`: "thor | Frank's workstation — tier-0
  hands-off | never targeted; inventory `readonly` group is documentation only"
- `fleet-config/docs/RECOVERY.md` / `RUNBOOK.md`: "thor is never touched" /
  "thor untouched" as a standing constraint that survives any rebuild.
- `fleet-config/ansible/playbooks/site.yml`: the `readonly` group (thor, ptah,
  sekhmet, horus, neith, khnum, hathor) is "deliberately absent — documentation-
  only hosts, never targeted."

SSH config edits, enabling UFW, and installing fail2ban on Thor are exactly the
provisioning verbs that rule forbids a worker (or any fleet automation) from
running, and Thor is Frank's own workstation — a misconfigured UFW rule or a
bad sshd drop-in there risks locking him out of his own machine, which only he
should be the one to risk.

Per full-autonomy doctrine, this is not a decision to gate on — it's a physical
act only Frank can safely perform on his own hands-off box. So the work here is
the part a worker *can* do safely: produce the reviewed, ready-to-run artifact.
Applying it is queued as Frank's own physical task, not a blocker on the ticket.

## What's here

- `99-lumen.conf` — the `/etc/ssh/sshd_config.d/99-lumen.conf` drop-in:
  `PasswordAuthentication no` + `PermitRootLogin no`.
- `fail2ban-jail.local` — `/etc/fail2ban/jail.local`: enables the `sshd` jail
  (systemd backend, maxretry 5, bantime 1h).
- `apply.sh` — dead-man-switch apply/disarm, same posture as
  `ops/wq3108-fleet-input-default-drop/apply.sh`: arms a 180s rollback timer
  *before* touching live sshd/UFW, so a lockout self-heals if disarm never runs.
  UFW allow rules (`tailscale0`, OpenSSH) are added **before** `ufw enable`, so
  the tailnet and direct-SSH paths are never dropped mid-flight.

## How Frank runs it

At Thor's own console/local shell (not dispatched remotely by a worker, not
run from Lumen over SSH):

```bash
sudo bash apply.sh arm ops/wq4485-thor-ssh-hardening
# verify from a NEW ssh session: key auth still works, sudo still works
sudo bash apply.sh disarm ops/wq4485-thor-ssh-hardening
```
