# wq#1466 — gatus external-endpoint token: research complete, execution blocked

Author: orch-burn-4-cfa06f, 2026-09-24. No secret value appears in this file.

## 1. The gating question is ANSWERED: Gatus v5.10.0 DOES support env substitution

The ticket said to verify from the docs. Verified empirically instead, on the exact
image the fleet runs (`twinproduction/gatus:v5.10.0`), in a throwaway container on a
scratch port with a temp config dir — the live instance was never touched.

Config under test used no literal token at all:

```yaml
external-endpoints:
  - name: envsub-probe
    group: scratch
    token: "${SCRATCH_PUSH_TOKEN}"
```

| push Authorization header | result |
|---|---|
| the **env var's value** | **HTTP 200, accepted** |
| the literal string `${SCRATCH_PUSH_TOKEN}` | HTTP 401 `invalid token` |
| an unrelated wrong token | HTTP 401 `invalid token` |

The middle row is the one that matters: if Gatus were matching the literal config text,
that push would have succeeded. It did not, so substitution genuinely happens at load
time. The accepted push then appeared in `/api/v1/endpoints/statuses`.

So the ticket's primary branch is live: move the value to
`/home/lumen/.secrets/gatus-external.env` (0600), load it into the unit, and replace
every literal with `${...}`. The fallback branch (generate config from a template) is
not needed.

One gotcha for whoever implements it: v5.10.0 **panics at startup** if the config has no
regular `endpoints:` block (`panic: configuration should contain at least 1 endpoint`).
A validation run against a stripped temp config must keep one dummy endpoint or it will
fail for a reason that has nothing to do with the tokens.

## 2. Residue sweep — read-only, counts and hashes only

All occurrences in `config.yaml` are **one single value**, not 38 distinct ones
(confirmed: one distinct sha256 prefix across all 41 matches). This matches the wq#2116
note and means rotation is one new secret, not 38.

| location | files | lines |
|---|---|---|
| `/home/lumen/sentinel/config` | 25 | 795 |
| `/home/lumen/foreman/_runs` (AMBER snapshots) | 21 | 21 |
| `/home/lumen/foreman/ops` | 0 | 0 |
| `/home/lumen/foreman/reports` | 0 | 0 |
| `/home/lumen/agents/work-queue/_runs` | 0 | 0 |

The `ops` and `reports` hits wq#2116 recorded are **gone from the working tree** — those
redactions landed. The `work-queue/_runs` transcript hits are also gone.

The 795 lines under `sentinel/config` are far more than the ticket's "38 plus 9 .bak
copies": there are now **24 .bak copies** of config.yaml, each carrying the full set.

## 3. ROTATION IS CONFIRMED MANDATORY

`git log -S` over the foreman repo finds the pattern in **10 commits**. It is in history,
not just in working files, and wq#2116 could redact the working copies but not rewrite
history. Relocating the value out of config.yaml therefore closes nothing on its own —
anyone with the repo still has the secret. **Rotate, then relocate.**

No pusher under `/home/lumen/bin` or `/etc/systemd/system` contains the literal, so the
pushers source it from somewhere else; that source must be found before rotating or the
probes go dark silently.

## 4. Why execution stopped here

`sentinel-gatus` is a **Docker container** (not the `sentinel-gatus` systemd unit the
ticket assumes — that unit does not exist; `systemctl is-enabled sentinel-gatus` returns
`not-found`). It mounts `/home/lumen/sentinel/config` → `/config`.

wq#2103 ("gatus push 404 for probe-r4-unticketed-recovery") is **claimed by orch-burn-1**
with a live lease to 16:32:51Z. That agent edited `config.yaml` at 12:21 and restarted
the container at 12:22:36 — it is actively debugging push authentication in this exact
file right now. Rotating the shared push token underneath it would break every push probe
mid-diagnosis and make its 404 unreproducible.

Sequence this after wq#2103 releases. The research above is the expensive half and is
done; execution is mechanical once the file is free.

## 5. Ready-to-run plan for the next agent

1. Confirm wq#2103 released and no other lease covers sentinel/gatus.
2. `restic` backup + copy `config.yaml` before any edit.
3. Find the pushers' source of the current value (not in `bin/` or `/etc/systemd/system`).
4. Mint a new token; write `/home/lumen/.secrets/gatus-external.env` (0600).
5. Add `EnvironmentFile=` — note this is a **container**, so it is `--env-file` on the
   docker run / compose definition, not a systemd unit directive.
6. Replace all 41 literals with `${...}`; validate on a scratch port first (keep one
   dummy `endpoints:` entry, per §1).
7. Recreate the container; confirm `/api/v1/endpoints/statuses` is unchanged and one push
   heartbeat is still accepted with the NEW value.
8. Update every pusher to the new value.
9. `chmod 0600` or delete the 24 `.bak` copies; they carry the old value.
10. Close with a residue grep showing zero literals under `sentinel/config`.

The old value stays compromised in foreman history regardless — that is what rotation is
for, and no history rewrite is proposed here.
