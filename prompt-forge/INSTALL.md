# Prompt Forge — deploy steps (wq#957)

Build is complete and tested in-repo; these steps need root / docker / the
LiteLLM master key, which BUILD workers don't hold (guard denies sudo and
docker). Same staged posture as the wq#718 dashboard. Everything below is
idempotent — safe to rerun.

## 1. Sync the repo to the primary tree

Merge the PR; `/home/lumen/foreman` picks it up on the normal sync. Verify
`/home/lumen/foreman/prompt-forge/server.py` exists.

## 2. Mint the LiteLLM service key (optional but wanted)

Per docs/litellm/service_keys.md: add a `prompt-forge` entry (suggested
budget $5/30d — a few hundred Sonnet calls) to `SERVICES` in
`litellm-provision-keys`, rerun it, confirm
`/home/lumen/.secrets/litellm-service-keys/prompt-forge.key` exists and is
mode 0600 lumen:lumen.

Without the key everything still works minus clarifying questions and the
framing section — the UI labels the output "deterministic only".

Model: the gateway has NO `claude-sonnet-5` rung. The `claude-max-*` rungs are
CLI-backed and time out on synchronous requests (measured 90s+, empty body), so
they are unusable for this web UI. DEPLOYED DEFAULT (wq#957, 2026-08-27):
`deepseek-v4-flash` (1s, clean JSON, adequate for clarify+framing), set via
drop-in `/etc/systemd/system/lumen-prompt-forge.service.d/10-model.conf`.
Re-list with `python3 /home/lumen/pstack/tools/lumen_panel.py --list-models` if
choosing a different rung.

## 3. Install + enable the service

```bash
sudo cp /home/lumen/foreman/prompt-forge/systemd/lumen-prompt-forge.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now lumen-prompt-forge
curl -s http://127.0.0.1:8792/api/health   # → {"ok": true, "llm": true|false}
```

## 4. Expose prompts.lumennode.uk

Add to `/etc/cloudflared/config.yml` ingress (above the catch-all), then
restart cloudflared and route DNS:

```yaml
  - hostname: prompts.lumennode.uk
    service: http://localhost:8792
```

```bash
cloudflared tunnel route dns <tunnel> prompts.lumennode.uk
sudo systemctl restart cloudflared
```

**Auth (design open question 4, decided): Cloudflare Access.** Add
`prompts.lumennode.uk` to the same Access application/policy that fronts the
other private hostnames before or in the same sitting as the DNS route — the
generated prompts embed node IPs and autonomy grants, so this hostname must
never be reachable unauthenticated. The service itself binds loopback only,
so nothing is exposed until this step is done deliberately.

Scripted, idempotent version of this step (wq#1017):
`ops/wq1017-promptforge-cf-access/add_access_hostname.py` — looks up the
Access app already covering `home.lumennode.uk` and appends
`prompts.lumennode.uk` to it (no-ops if already present, refuses rather than
guesses if the lookup is ambiguous). Needs an Access-mgmt CF token file; see
that script's README for why one isn't wired up yet.

## 5. Glance dashboard

Apply `prompt-forge/glance/glance-integration.md` to
`/home/lumen/glance/glance.yml` (backup first), then restart the glance
container.

## 6. Smoke test

Open home.lumennode.uk → Prompt Forge panel where Plug Power was. Type
"Starting a new project. We are going to reverse engineer a Google Glass
device." → Generate → expect 1-3 questions (e.g. which generation) → answer →
prompt appears with the live node roster and the current autonomy grants.
Copy button copies.
