# wq#4806 — [selfheal] rotate key: gemini rejects auth

## Symptom

`litellm-completion-probe` (class 5, `ops/wq3354-fleet-selfheal/sh_providers.py:1-30`)
found the gemini upstream answering 401/403 for 160+ minutes. Past
`disable_after_s` it cooled the gemini-flash rungs into
`dispatcher/runtime/backend_cooldown.json` (`sh_providers.py:16`) and filed this
ticket (`sh_providers.py:172-179`). Per the same module's own docstring
(`sh_providers.py:22-23`): once the gateway answers again, fleet-selfheal
removes its cooldown entries and closes the episode on its own — no worker
action needed for that half.

## Why a BUILD worker cannot finish this ticket

The remaining half — getting a working key back into LiteLLM — is the
`rotate-provider-keys` tool (`/home/lumen/bin/rotate-provider-keys`), and it is
gated by two things only an attended operator can do, confirmed live against
this worktree:

1. **Minting the replacement key happens at the provider's dashboard.**
   `rotate-provider-keys`'s own module docstring says it plainly: "Minting the
   new key happens at each provider's dashboard (no provisioning API on this
   fleet)." For Gemini that is Frank's Google AI Studio account — no fleet
   credential or API reaches it.

2. **The wq#3747 credential-rotation safety gate refuses the tool outright
   without a same-day Vault backup.** Running the rotation tool from this
   session (even a read-only `--help`-shaped probe) is refused by
   `harness/guard.sh` before the command executes:

   > BLOCKED — credential-rotation safety gate (wq#3747): rotate-provider-keys
   > refused — no Vault backup marker
   > (`/home/lumen/.secrets/.rotation-vault-backups/rotate-provider-keys.ok`)
   > from the last 1440m. An attended operator must run
   > `python3 /home/lumen/foreman/ops/wq3747-credential-rotation-safety-gate/vault_rotation_backup.py rotate-provider-keys`
   > first (pastes the OLD value, unlocks Vault with their own master
   > password) — a headless worker cannot do this step itself.

   `vault_rotation_backup.py`'s own docstring
   (`ops/wq3747-credential-rotation-safety-gate/vault_rotation_backup.py:19-21`)
   says the same: "This is deliberately interactive: a master password only an
   attended operator can supply is the thing that makes the gate meaningful. A
   headless worker cannot run this step itself." `/home/lumen/.secrets/` is
   also a denied path for this session on its own terms (wq#616).

   Separately, `rotate-provider-keys run()` (`/home/lumen/bin/rotate-provider-keys:245-254`)
   calls `/home/lumen/bin/lib/require-vault-backup.sh` per provider before it
   will swap anything — a second, provider-scoped instance of the same
   attended-backup requirement.

This is the Tier-0 instinct-3 case: a physical act nobody else can perform
(mint a credential on Frank's own provider account, unlock Vault with his own
master password). Per full-autonomy doctrine that is a queued physical task,
never a gate that blocks the rest of the fleet — but it does block *this*
ticket's own remaining work, since there is no remaining work this ticket
describes that doesn't route through the gated tool. Same shape as wq#4184
hitting the same wq#3747 gate on a different rotate-* tool (ISSUE-LOG row
1407): documented the finish sequence, blocked for attended dispatch, did not
fake completion.

## Finish sequence for an attended operator (Frank, or a lane with the Vault
master password)

1. Mint a new Gemini API key at Google AI Studio (the account behind the
   current `GEMINI_API_KEY`).
2. `python3 /home/lumen/foreman/ops/wq3747-credential-rotation-safety-gate/vault_rotation_backup.py rotate-provider-keys`
   — paste the OLD Gemini key value when prompted, unlock Vault with the
   master password when prompted.
3. Write the new value into `/home/lumen/.secrets/provider-keys.new` as a
   `GEMINI_API_KEY` line (that file is consumed and then expected gone by the
   `apply` mode below).
4. `/home/lumen/bin/rotate-provider-keys apply` — preflights the new key at
   Gemini, backs up every env file, swaps the value everywhere it lives,
   recreates the `litellm` container, verifies a real completion through the
   `gemini-flash` alias, restarts consumer services, then prints the sha12 of
   the old key to revoke at the dashboard.
5. No further action: `sh_providers.py`'s next sweep probes gemini-flash,
   sees 200s, drops the `backend_cooldown.json` entries it owns, and closes
   this ticket's episode (`sh_providers.py:22-23`).

## Evidence this was verified live, not guessed

- `harness/guard.sh` refusal text above is the literal error this session hit
  running step 4 from this worktree today.
- `/home/lumen/.secrets/.rotation-vault-backups/` listing was separately
  refused by `harness/guard.sh` ("denied path ... wq#616 static oauth token
  lives there"), confirming a headless worker cannot even inspect whether a
  marker exists, let alone create one.
- ISSUE-LOG row 1407 (wq#4184) hit the identical wq#3747 gate on the Gitea
  rotation finisher and used the same resolution: document + block.
