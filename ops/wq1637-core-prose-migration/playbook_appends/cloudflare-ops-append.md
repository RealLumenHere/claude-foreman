Target: /home/lumen/instructions/playbooks/cloudflare-ops.md — append at end of file.
Source ticket: wq#1637, matches instincts_candidates.yaml's `cord-secret-handoff`.

---

## CORD — Frank → agent secret handoff (LIVE 2026-09-02, wq#1367)
An agent that needs a value only Frank can supply NEVER stalls and NEVER asks in chat:
`cord request "<what>" --note "<why>"` pages Frank, blocks, and prints the value on stdout —
and nowhere else: never a log, journal line, ticket note, report, commit, or chat message.
`cord` is deliberately NOT behind Cloudflare — plaintext secrets must not transit CF TLS
termination. Do not add a cloudflared ingress rule for cord. The audit table records who
asked/filled/claimed a request, never the value itself.
