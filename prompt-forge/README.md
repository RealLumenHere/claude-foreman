# Prompt Forge (wq#957)

Fleet-aware prompt generator at prompts.lumennode.uk, embedded in
home.lumennode.uk. Frank types a one-line project description; the tool asks
at most 1-3 clarifying questions; out comes a copy-paste-ready session prompt
carrying the full fleet bootstrap, the live node roster, the current autonomy
grants, category rules, and prior project context.

Why: Anthropic's per-conversation classifier kills orchestrator chats that
carry security-adjacent project content, so project work must live in
separate short chats — but each fresh chat used to cost ~10 minutes of
re-explaining the fleet, inconsistently. Full background:
`/home/lumen/decisions/PROMPT_FORGE_DESIGN.md`.

## Architecture — Option B from the design doc

Deterministic template engine + LLM only for the smart parts. The fleet facts
in every prompt are measured at generation time, never model-recalled:

| section | source |
|---|---|
| node roster (IPs, roles) | `lumen_state.py current`, parsed live |
| autonomy grants | `fleet.*autonomy*` state facts, verbatim |
| public services | `sentinel/ui_inventory.json` (wq#770 discovery) |
| prior project context | `/home/lumen/projects/<slug>/` + per-slug state |
| bootstrap, identity, category/filter rules | static text in `templates.py` (changes by PR) |
| clarifying questions, project framing | LiteLLM call — the ONLY model-generated parts, labeled as such in the output |

Files: `server.py` (stdlib http.server, loopback :8792), `fleet_context.py`
(live readers, 60s cache, degrade-to-placeholder), `templates.py` (assembly),
`llm.py` (LiteLLM client, degrade-to-None), `static/index.html` (UI),
`systemd/` + `INSTALL.md` (staged deploy), `glance/` (dashboard patch).

Degradation is a design rule: no gateway, no key, no state store — the
generator still emits a correct deterministic prompt and labels what's
missing. It must work best exactly when the fleet is broken.

## Open questions from the design doc — decided in this build

1. **LiteLLM or direct Anthropic?** LiteLLM, named service key
   `prompt-forge` (env `LITELLM_API_KEY` first, then key file — the
   service_keys.md convention). No key ⇒ deterministic-only mode, not an
   error. Direct Anthropic never — the fleet's budget/attribution story
   lives at the gateway.
2. **Continuation context source?** Project directory (STATUS.md +
   .fworker-rules.yaml + file listing) plus `lumen_state.py current <slug>`.
   NOT the episode store: episodes are session-grained and huge; STATUS.md
   is the curated summary the fleet already maintains for this purpose.
3. **Store generated prompts?** Yes, newest 50 as JSON under
   `/home/lumen/agents/prompt-forge/history/` with a `GET /api/history`
   endpoint. Cheap, and lets a lost chat's prompt be regenerated verbatim.
4. **Auth?** Cloudflare Access on prompts.lumennode.uk (same policy as the
   other private hostnames), loopback bind underneath — see INSTALL.md §4.
   Prompts embed node IPs and autonomy grants; this hostname must never be
   public.
5. **Fleet context for the clarifying-questions call?**
   `fleet_context.compact_summary()`: roster names + one-line roles (no
   IPs), autonomy headline, known project slugs. ~1-2 KB — enough to
   recognize domains and continuations, cheap per call.

## Run / test

```bash
python3 server.py --port 8792          # from this directory
python3 -m pytest tests/ -q            # or: python3 -m unittest discover tests
```
