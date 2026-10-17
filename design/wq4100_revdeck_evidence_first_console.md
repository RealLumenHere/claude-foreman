# wq#4100 — LIMPET DISSECT: Rev-Deck (biniamf/ai-reverse-engineering) — evidence-first console

Status: research/design. Author: build worker, 2026-10-17. Dissects **Rev-Deck**
(`biniamf/ai-reverse-engineering`) as the strongest *open* prior art for Limpet's read-only
live operator console (gap **G5**, feature **A8**; `design/wq3929_competitive_analysis.md:417-420,496`),
paired with **EMBArk** (`e-m-b-a/embark`) as the multi-user reference
(`design/wq3929_competitive_analysis.md:236-239`). The job is to pull Rev-Deck's
*evidence-first* discipline into the console Limpet already ships (wq#3971,
`limpet/workspace_console.py`) and to show that "every LLM claim must cite inspectable
evidence" is the **same obligation** the reconstruction ledger already enforces (A1/G2,
`limpet/obligations.py`), not a second mechanism to build.

## 0. Grounding — and an honest verify-gap

Rev-Deck could **not** be read live this session. Every external channel was down at dispatch:
`fleet-search web` returned `all general web-search engines suspended/blocked` (brave/ddg/
google-cse/startpage CAPTCHA'd, no `BRAVE_API_KEY` — wq#2595 still open); `fleet-search github`
returned `401 — no usable token`; the session's `WebSearch`/`WebFetch` tools were not granted.
The name `biniamf/ai-reverse-engineering` / "Rev-Deck" appears **nowhere** in this worktree or
`/home/lumen/kb` — it entered only through this ticket, sourced from Frank's
`new-tool-evals-2026-10-16.md` and the G5/A8 rows of the competitive analysis (both outside
this worker's sandbox). Per instinct 8 that makes the name **ticket-introduced, not
Claude-fabricated**, so it is a legitimate design input — but its *internals* are
characterized from the ticket's one-paragraph brief plus general evidence-first RE-console
design, **not** from source. Every Rev-Deck property below is tagged `[ticket-stated]` and
should be re-confirmed by a verify-pass once search is restored (ISSUE-LOG #1387). Nothing in
the Limpet recommendations depends on an unverified Rev-Deck detail: each lands on a fleet
fact cited to real code.

## 1. Rev-Deck's three invariants (the whole point of dissecting it)

Rev-Deck is `[ticket-stated]` an evidence-first web UI over **headless Ghidra**. Stripped to
design primitives, it holds three invariants — and all three are *refusals*, not features:

1. **Binaries are NEVER executed.** Analysis is static only (Ghidra decompile / strings /
   imports / xrefs / call graph). The target is attacker-controlled bytes; the tool reads it,
   never runs it.
2. **Every LLM factual claim MUST cite inspectable evidence.** A model statement about the
   binary is not allowed to stand on its own — it must point at a function, string, import,
   xref, or a **bounded** call-graph slice the operator can click and inspect. A claim with no
   citation is not rendered as a claim.
3. **The blast radius is closed.** The server binds `127.0.0.1` (no LAN exposure), and the
   provider API key lives **server-side** only — it is never shipped to the browser.

These are the same three postures Limpet already argues for from first principles; Rev-Deck is
the external confirmation that an *evidence-first* RE console is the right shape, and that the
discipline belongs at the **claim boundary**, not bolted on as an afterthought.

## 2. EMBArk — the multi-user axis Rev-Deck does not cover

Rev-Deck is single-operator `[ticket-stated]`; **EMBArk** is the prior art for *team* use —
a multi-user web console over the EMBA firmware pipeline, dissected as source-separate prior
art in wq#4005 (`design/wq4005_emba_dissection.md:34-36`; `design/wq3929_competitive_analysis.md:236-239`).
The two decompose cleanly along one axis:

- **Rev-Deck → the claim contract** (what a single view of one target must guarantee: every
  claim cites, nothing executes, key stays server-side).
- **EMBArk → the access model** (many operators, many targets, who-sees-what, report sharing).

For Limpet's *read-only live console* the Rev-Deck axis is load-bearing and the EMBArk axis is
deferred: the console ships today as a single-operator `tail -f` (§3), and multi-user is a
later concern gated behind the manifest-signing/trust work (G8,
`design/wq3929_competitive_analysis.md:425-426`). Pull Rev-Deck's claim contract **now**; keep
EMBArk's access model on the shelf with a one-line pointer so it is not re-discovered.

## 3. What Limpet already ships (do not rebuild)

The console exists: **wq#3971**, `limpet/workspace_console.py` — a follow-mode reader of the
per-call evidence log `evidence/tool-calls.jsonl` that the meta-server appends one JSON block
per `tools/call` (`limpet/workspace_console.py:1-8`, contract pinned at
`limpet/workspace_console.py:33`). It is **read-only by construction** — the module only ever
opens the log for reading, exposes no control action, and never writes the workspace
(`limpet/workspace_console.py:16-20`) — which is Rev-Deck invariant (3)'s blast-radius posture
expressed as "the console cannot touch the box." It renders each block as one line
`ts  tool  ok  coverage` and, under `--json`, one projected object per line for a GUI pane,
both derived from a single `project()` so the two views cannot disagree
(`limpet/workspace_console.py:79-84`). `follow()` streams appends from a tracked byte offset,
holding a torn trailing line back until its newline arrives
(`limpet/workspace_console.py:130-162`).

The claim contract (invariant 2) also already exists, as the **reconstruction obligation
ledger** (wq#3965, A1/G2): `resolve()` **refuses to close an obligation without at least one
real evidence ref** (`limpet/obligations.py:224-227`), and an evidence ref is validated to be
either a REA `ev_<hash>` id or a path that actually exists *inside the workspace*, with
absolute paths and `..`-escapes rejected (`limpet/obligations.py:141-158`). That is exactly
Rev-Deck's "a claim must cite inspectable evidence, and the evidence must be real" — enforced
server-side, in code, today. `require_done()` then blocks "done" while any obligation is still
open (`limpet/obligations.py:285-290`), the ledger's analogue of Rev-Deck refusing to present
an unsupported conclusion.

**Conclusion of the dissection:** Limpet does not need a Rev-Deck clone. Invariant (1) is the
workspace untrusted-input rule (`design/wq3806_limpet_architecture.md:247-250`); invariant (3)
is the read-only console plus the appliance's local binding; invariant (2) is the obligation
ledger. The one place Rev-Deck is stricter than the shipped console is **coverage**, below.

## 4. The silent-coverage-gap — the one real fix

Rev-Deck's invariant (2) is absolute: a claim either cites or is not shown. Limpet's per-tool
*coverage note* (gap G1 — "every tool return carries a coverage/limitations note",
`design/wq3929_competitive_analysis.md:77-78`; seen on REA returns,
`design/wq3998_rea_morluto_dissection.md:39`) is where Limpet is **laxer** than Rev-Deck, and
the console makes it worse:

- `project()` copies `coverage` straight through (`limpet/workspace_console.py:59-66`), and
  `format_line()` renders a `None`/empty coverage as the literal `"-"`
  (`limpet/workspace_console.py:75`).
- So a tool that **declared full coverage**, a tool that **declared a gap**, and a tool that
  **said nothing at all** can all three surface to the operator identically. Absence of a
  coverage note collapses, silently, into the same glyph as "nothing to note." That is the
  **silent-coverage-gap**: the console cannot distinguish *covered* from *uninspected*, so an
  operator reading the live tail cannot see where the agent is flying blind — the exact
  failure this ticket cites against REA.

**Fix (design, small, lands on the shipped console):** make coverage a three-state field the
console renders distinctly, so *absent* is never silently equal to *clean*.

1. `project()` emits `coverage_state ∈ {declared, gap, absent}` alongside the free-text note:
   `gap` when the note names a limitation, `declared` when the tool asserted coverage, `absent`
   when the block carries no coverage key at all (`limpet/workspace_console.py:59-68`).
2. `format_line()` renders `absent` as a visible marker distinct from both a clean `declared`
   and a `"-"` empty-string note — e.g. `?cov` — so an uninspected call *stands out* in the
   tail rather than blending in (`limpet/workspace_console.py:71-76`). The `--json` view
   already carries the raw field; it gains `coverage_state` for the GUI pane to style.
3. The meta-server's evidence writer should **require** every `tools/call` block to carry an
   explicit coverage key (even `"full"`), so `absent` means a genuinely non-conforming tool,
   not a tool that simply forgot — turning the console marker into a conformance signal. (This
   is the writer's lane, `limpet/sanitize.py:21,68` names the on-disk contract; raise it there,
   do not widen the console's write surface — read-only is invariant.)

This keeps the console read-only (it still only *reads* and *renders*), costs one derived field
plus one render branch, and closes the gap Rev-Deck closes by construction. The probe that can
fail (wq#2630): feed the console a three-block fixture — one `declared`, one `gap`, one with no
coverage key — and assert all three render **distinctly**; today's code fails it because block
1 and block 3 both print `"-"`.

## 5. Build items (ranked value ÷ cost), each tied to real code

1. **Coverage three-state in the console (§4).** Highest value, near-zero cost; turns the
   existing tail into an evidence-first view. `limpet/workspace_console.py:59-76`.
2. **Evidence-writer coverage conformance (§4.3).** Makes `absent` meaningful. Writer lane,
   `limpet/sanitize.py:68` on-disk contract; coordinate with the wq#3964 EvidenceLog owner.
3. **Click-through locator in the `--json` projection.** Rev-Deck's "click the evidence":
   carry each block's `evidence_id` (already projected, `limpet/workspace_console.py:66`) as a
   resolvable locator into `evidence/` so the GUI pane can open the cited artifact — the ledger
   already proves the ref is real-and-in-workspace (`limpet/obligations.py:141-158`), reuse that
   validator. Design-only until the GUI pane (wq#3903) exists.
4. **EMBArk access model — deferred.** One-line pointer only; gated behind G8 manifest signing
   (`design/wq3929_competitive_analysis.md:425-426`). Do not build for a single-operator
   appliance.

## 6. Verdict

Rev-Deck validates the Limpet console's shape rather than changing it: the three invariants
(no-execute, cite-or-don't-show, closed blast radius) are already the workspace untrusted-input
rule, the obligation ledger, and the read-only local-bound console respectively. The one
concrete defect it surfaces is the **silent-coverage-gap** in `limpet/workspace_console.py:75`
— *absent* coverage rendering identically to *clean* — fixed by a three-state coverage field
(§4) plus writer-side conformance. Everything else is a deferred pointer (EMBArk multi-user,
GUI click-through). Open item: a verify-pass on `biniamf/ai-reverse-engineering` once web/github
search is restored, to confirm the `[ticket-stated]` invariants against source (ISSUE-LOG #1387).
