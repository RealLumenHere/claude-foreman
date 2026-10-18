# wq#4164 — verify wq#4162 EVALUATE: Paperless-ngx for RE project docs + business receipts

Archetype 8 adversarial verify pass. Author: claude-sonnet-5. Verifier: claude-sonnet-5@high
(fresh instance, same model as author).

PR: http://100.121.17.5:3000/lumen/claude-foreman/pulls/2108 (merged to `main` as e139233b).
Deliverable is a research/eval doc, not code — `research/PAPERLESS_NGX_EVAL.md` (+100), no
other files touched.

## Claim under test

Doc recommends **CONDITIONAL GO**: adopt paperless-ngx on `lumen` for business receipts
(unmet need) now; scope RE-doc ingestion to third-party scanned PDFs only, not the existing
git/grep-native `/home/lumen/re/` + `foreman/reports/`/`kb/ISSUE-LOG.md` corpus; cap the OCR
worker's CPU share as a deploy condition; treat feeding paper receipts as a physical task per
instinct 3, not a gate.

## What I checked

Gitea's PR API 404s without an auth token I don't hold (private repo; `curl` to
`/api/v1/repos/.../pulls/2108` and `/users/lumen/repos` both come back short/404 unauthenticated)
— but the merge commit (e139233b) is already on `main` in this worktree, so I read the shipped
doc directly instead of re-fetching the diff.

- **Fleet resource figures (doc §2, cited as "run this session")** — re-ran the same commands
  on `lumen` myself: `free -h` → 60Gi total / 53Gi available; `nproc` → 4; `df -h /` → 248G
  avail. All three match the doc's table exactly.
- **`ARCHITECTURE.md:12` citation** — read it: `lumen | T5810. Control node + gateway: LiteLLM
  (:4000), wq, foreman dispatcher, docker (~35 containers)...` — matches the doc's "LiteLLM,
  wq, and ~35 other containers" claim verbatim, and the line number is correct.
- **Node-fit claims (isis/sobek/maat/bes)** — cross-checked against the same ARCHITECTURE.md
  node table: isis = Ollama/GPU inference (common role) ✓; sobek = postgresql/cloudflared,
  "its two services change only via detached systemd-run" ✓ matches "policy-restricted to
  specific services"; maat = restart-locked ("*" never-restart) ✓ matches "restart-locked";
  bes = copy target (off-host backup destination) ✓ matches "backup copy-target, not an app
  host." All four check out.
  - Minor completeness gap, not a contradiction: the doc doesn't mention `anubis` (gitea host,
    common+gitea role) in its "no other node is a better fit" survey. Doesn't change the
    conclusion — anubis is service-restricted to gitea the same way sobek/maat are restricted
    — but the survey is one node short of exhaustive.
- **`re-sync/DESIGN.md:33` Nice=15 citation** — `grep -n "Nice=" ops/re-sync/DESIGN.md` → line
  33, "Nice=15 / IO idle on the unit: checksum...". Matches.
- **`re-sync/DESIGN.md:1-4` citation (§4, RE store description)** — read lines 1-4: "/home/lumen/re/
  is the canonical store for every RE project. Working copies live on edge machines (Thor
  ~/projects, maksoor ~/rk3399_dump)." Matches the doc's claim and node names exactly.
- **Receipt-tooling gap claim (§3)** — reproduced the doc's own probe: `grep -rli receipt
  /home/lumen/kb/ /home/lumen/mailbot/` → exactly 2 hits, same files the doc names. Read both:
  `mailbot/seed_20260901.py:33` is an LLM chatbot roleplay line ("I've read the receipts...");
  `kb/ISSUE-LOG.md:710` is masking-false-positive language ("verification-receipt"), unrelated
  to any document store. Both non-hits confirmed by content, not just filename.
- **External paperless-ngx facts** — doctrine requires prior-art search before trusting
  claims about an unfamiliar tool; ran `fleet-search web` rather than taking the doc on faith:
  - RAM figures: a search for the doc's own cited source (selfhostpicks.com hardware-requirements
    page) returned the snippet "~0.8 GB Idle ~2 GB While OCR-ing a document" — matches the
    doc's table (idle ~0.8GB, during OCR ~1.5-2GB) number-for-number.
  - Stack composition (Django + Postgres + Redis + worker, Tika/Gotenberg optional for Office
    conversion): consistent with independent sources found (virtua.cloud tutorial: "runs
    PostgreSQL, Redis, Gotenberg, Tika, and the web server" — Tika/Gotenberg present in that
    compose but, per paperless-ngx's own docs referenced by the doc, only required for
    Office-document conversion, not scanned PDFs).
  - `paperless-gpt` (§5 integration note): real project, confirmed via search
    (github.com/icereed/paperless-gpt — "Use LLMs and LLM Vision (OCR) to handle paperless-ngx").
    Doc's characterization ("routes OCR'd text through an LLM for smarter title/tag
    suggestions") matches the project's own description.
- **Internal consistency** — the CPU-cap mitigation in §2 is correctly carried through to §6
  as a "deploy condition," not a blocker; the RE-doc scoping argument in §4 doesn't contradict
  the business-receipts GO in §3 (different corpora, different reasoning); the "physical task
  not a gate" framing in §6 correctly applies instinct 3 rather than parking work on Frank.

## Falsifiability note

This ticket's deliverable is a research doc, not a code change, so there's no test suite to
run red/green. The closest equivalent to a falsifiable probe is independently re-deriving
every externally-checkable number and citation in the doc rather than trusting the author's
transcription — which is what the above does: every `file:line` citation was opened and read,
every fleet command was re-run live, and every external claim was re-searched from scratch.
None came back contradicted.

## Verdict

**CONFIRMED.** Every verifiable factual claim in the doc — fleet resource figures, file:line
citations against `ARCHITECTURE.md` and `re-sync/DESIGN.md`, the receipt-tooling-gap grep, and
the external paperless-ngx facts (RAM footprint, stack composition, `paperless-gpt`) — checks
out against live re-verification. The one gap found (omitting `anubis` from the node-fit
survey) doesn't change the recommendation and isn't a contradiction. No evidence the
CONDITIONAL GO verdict, its scoping split, or its deploy condition are wrong.

No contradiction found; not escalating to judgment.
