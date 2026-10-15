# wq#3944 — Limpet competitive feature parity: breakdown into sub-tickets

wq#3944 is a placeholder. It was blocked on wq#3929's competitive analysis
(`design/wq3929_competitive_analysis.md`, PR #1901). That analysis has now been reviewed,
and this doc records how each adopt item (§5, A1–A11) was turned into work. Each build
ticket names its design hook, the files it touches, and acceptance tests that can fail.

| Adopt | Gap | Disposition | Ticket | Pri | Deps |
|---|---|---|---|---|---|
| A2 per-tool-call evidence metadata | G1 | filed | wq#3964 | p1 | — |
| A1 reconstruction obligation ledger | G2 | filed | wq#3965 | p1 | — |
| A3 adversarial critic pass | G3 | filed | wq#3966 | p2 | wq#3965 |
| A7 OFRAK modify/repack plugin | G7 | filed | wq#3967 | p2 | — |
| A5 model-capability tier | ReCopilot | filed | wq#3968 | p3 | — |
| A6 per-workspace SQLite RE index | Recon-SQL | filed | wq#3969 | p3 | — |
| A4 target-class → toolset routing | reverse-skill | filed | wq#3970 | p3 | — |
| A8 read-only live console | G5 | filed | wq#3971 | p3 | wq#3964 |
| A10 tshark + fuzzer in `core` | G6 | filed | wq#3972 | p3 | — |
| A9 manifest signing | G8 | folded into wq#3945 item (3) | wq#3945 | p2 | — |
| A11 outbound bundle connector | G4 | not filed | — | — | — |

## Decisions

- **A2 is ordered ahead of A1.** The ledger, the critic, the SQLite index and the console
  all read per-call evidence. A2 gives them that data, so it lands first. A1 does not
  hard-depend on it: an obligation can cite workspace log entries that exist today.
- **A3 depends on A1.** The critic's deterministic half checks ledger state, and a
  disagreement opens a ledger obligation instead of being dropped.
- **A9 belongs in wq#3945.** That ticket already owns trust and signing for the plugin
  system, as well as discovery and marketplace work, which was another of wq#3944's
  expected areas. A separate ticket would split one design across two lanes.
- **A11 is not filed.** Its "file" half (80% of G4 per wq#3929 §3) already exists: wq#3934
  exports the workspace as md/html/json. The other halves, email and SaaS connectors, are
  outbound network paths, which an offline-first appliance should not grow by default.
  Revisit only if a user asks for a delivery channel.
- **A3/A5/A6 rest on *described* prior art.** Search was down when wq#3929 ran (ISSUE-LOG
  #1325) and is still down on 2026-10-15: `fleet-search web` returned
  `all general web-search engines suspended/blocked` with no Brave fallback (wq#2595).
  wq#3929 §6 says to re-verify these platforms before making a build commitment, so each
  of those tickets begins with a prior-art step. They are not blocked, because the
  Limpet-side feature stands on its own.

The scope areas wq#3944 listed map onto these tickets as follows. Investigation model:
A1, A3. Evidence tracking: A2, A8. Plugin discovery/marketplace: wq#3945. Agent workflow
patterns: A3, A4. Specialized RE model routing: A5.
