# wq#1151 P2 — exposing lumen-kb-mcp via mcp-oauth-worker (REVIEW, not deployed)

Status: **designed, deliberately not deployed from the build lane.** The ticket
records this phase as security-sensitive with a review gate before going live.
This doc is that review input; a privileged session applies it after sign-off.

## Shape (same as Lumen Commander)

```
claude.ai connector ──HTTPS/OAuth──▶ mcpw (mcp-oauth-worker) ──Tailscale──▶ 127.0.0.1:8781 lumen-kb-mcp
```

- lumen-kb-mcp stays loopback-only (unit pins `IPAddressAllow=localhost`);
  mcpw is the **only** ingress, terminating OAuth exactly as it does for
  Commander. No new port is opened on any public interface.
- mcpw route entry (adjust to the live mcpw config schema at deploy time):
  upstream `http://127.0.0.1:8781/mcp`, path `/kb`, same OAuth client/issuer
  as the Commander route, **separate scope string `kb`** so a Commander token
  cannot hit the KB route and vice versa.

## What data is exposed once live

- Full read of every note in /home/lumen/kb (list_topics, read_note, search).
- Write access: write_note can create/overwrite any note (git history is the
  undo path — every write is committed, nothing is destroyed silently).
- Attachment **names** only; `list_attachments` never serves file bytes.
- NOT exposed: anything outside the vault (kb_paths guards + systemd
  `ProtectHome=read-only` + `ReadWritePaths=/home/lumen/kb`), vault .git
  internals (hidden-name guard), attachment contents.

## Go-live checklist (privileged session)

1. Vault content audit: `grep -ri` the vault for credentials/PII before first
   exposure — the vault becomes readable to anything holding a `kb`-scoped
   token. Nothing secret belongs in the KB; secrets live in Vault, full stop.
2. Confirm the unit is running the hardened server (tests/ green on host,
   handshake check in apply.sh passes).
3. Add the mcpw route (detached systemd-run for the mcpw restart — instinct 4:
   mcpw is on the Commander/operator path).
4. Verify scope isolation: a Commander-scoped token against /kb must get 401/403.
5. Register the connector in claude.ai with the P5 instruction block
   (README §connector instruction).

## Rollback

Remove the mcpw route + restart mcpw (detached). The KB server itself never
needs to change to un-expose; loopback binding is the invariant.
