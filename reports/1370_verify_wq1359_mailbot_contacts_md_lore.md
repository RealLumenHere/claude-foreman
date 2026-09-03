# wq#1370 — verify-pass: wq#1359 mailbot contacts/*.md lore loader (PR#6, mailbot)

**Verdict: CONFIRM** — the loader does what the PR claims: `contacts/jonathan.md`,
`_lore_jonathan_only.md`, and `_lore_lumenjonathan.md` (landed inert by wq#1200, zero
readers before this) are now bundled as raw text via a `wrangler.toml` `Text` module rule
and threaded into `generateReply()`'s system prompt, gated on a strict sender-address /
Cc match — never a fuzzy body-content guess, never a replacement of the base persona.

PR: http://100.121.17.5:3000/lumen/mailbot/pulls/6 (open, mergeable, head `d006ae6` on
`feat/wq1359-mailbot-contacts-md-lore-has-zero-reader-a1`). Scope: `src/worker.js` (+36/-2)
and `wrangler.toml` (+6), diff pulled via `pulls/6.diff` and read cold against the branch
tip (`git show d006ae6:...`), not the PR body's own description of itself.

## What the diff does

- `wrangler.toml`: adds `rules = [{ type = "Text", globs = ["**/*.md"], fallthrough = true }]`
  so any `.md` import bundles as a raw string at build time.
- `src/worker.js`: three static imports of the contact files; `JONATHAN_EMAIL` constant
  (`netwearcdz@gmail.com`); `isJonathanCcd(email)` (To/Cc address match); `loadContactLore
  (senderAddr, email, angry)` — Jay-as-sender gets `jonathan.md` (+`_lore_jonathan_only.md`
  when `angry`), anyone with Jay Cc'd gets `_lore_lumenjonathan.md`, everyone else gets
  `null`. `generateReply()` appends non-null lore into `system` as an owner-supplied,
  non-overridable background block. Called once per non-probe inbound and threaded through
  both the initial draft and the red-tape regen retry so a regenerated reply keeps the lore.

## Verification performed

1. **Read the diff cold** off `pulls/6.diff`, not the PR's own prose summary of it.
2. **Confirmed the three contact files exist on the PR branch** (`git ls-tree -r
   origin/feat/wq1359-...:contacts/`) and that the PR's specific evidentiary claim —
   `netwearcdz`, `"shredded cheese"`, `"Orchestrator"` each appear in the real file content —
   is true (`grep -rn` hit all three, in `jonathan.md`, `_lore_jonathan_only.md`, and
   `_lore_lumenjonathan.md` respectively, not fabricated).
3. **Statically confirmed the `wrangler.toml` rule is valid** for the pinned `wrangler
   ^4.106.0`: `wrangler-dist/cli.d.ts:2515` — `ConfigModuleRuleType` includes `"Text"`;
   `:2505-2510` — the `{ type, globs, fallthrough }` shape matches exactly what's in the
   diff. Could not run `npx wrangler deploy --dry-run` myself (allowlist gap, below), so
   this static confirmation is the strongest evidence available in-session; PR body's own
   claim of a dry-run build inspecting bundled output is consistent with what the type
   definitions allow.
4. **`node --check src/worker.js` equivalent**: also blocked by the same allowlist gap;
   read the full diff hunks by hand instead — no unbalanced braces/parens, `contactLore`
   destructured with a `null` default in `opts`, both call sites at the two `generateReply`
   invocations (initial + regen) pass it through.
5. **Adversarial probe — sender/Cc address shape**: checked `postal-mime`'s actual type
   (`node_modules/postal-mime/postal-mime.d.ts:45-64`): `email.to`/`.cc` are `Address[]`
   where `Address` can be a group (`{ name, group: Mailbox[], address: undefined }`, e.g. a
   mailing-list header) not just a `Mailbox`. `isJonathanCcd`'s `(a?.address || "")
   .toLowerCase()` degrades a group entry to `""` instead of throwing — safe.
6. **Adversarial probe — parse-failure fallback**: `PostalMime.parse` failures fall back to
   `email = { subject: "", text: "", from: {...} }` (worker.js:345) with no `.to`/`.cc` at
   all. `isJonathanCcd`'s `email.to || []` / `email.cc || []` absorbs the missing fields —
   no crash on a malformed inbound.
7. **Adversarial probe — angry-lore gating**: `angry` is a body-regex match
   (`ANGRY_TRIGGER`, worker.js:131/382) settable by *any* sender's own email body, not just
   Jay's. But `_lore_jonathan_only.md` only loads inside the `addr === JONATHAN_EMAIL`
   branch, so a third party can't self-grant it by faking the trigger phrase — only mail
   actually addressed from Jay's address can. (Whether `From:` itself can be spoofed
   upstream of this code — no SPF/DKIM check visible — is a pre-existing, system-wide
   property of every sender-gated feature in this file, not something this diff introduces;
   out of scope for a diff-scoped verify-pass.)
8. **Adversarial probe — CC-triggered lore is content-safe for strangers**: read
   `_lore_lumenjonathan.md` in full. It's explicitly headed "sharable with anyone Jay
   refers" with hard exclusions (no minors, no sexual content, no literal-harm threats) and
   was reviewed/scrubbed under wq#1200, which predates and is untouched by this diff — this
   PR only wires up a loader for already-approved content, it doesn't author or relax it.
9. **Probal for double-load/duplication**: `system` is a local `let` rebuilt fresh inside
   `generateReply()` on every call (including the regen retry), so no risk of the lore block
   being appended twice into one prompt.
10. **Confirmed primary tree isolation**: `/home/lumen/mailbot` (primary checkout) has
    unrelated uncommitted WIP from the wq#1338 lane in the same three files this PR touches
    — exactly as the PR body warns. Did not touch it; instead created a throwaway detached
    worktree (`/home/lumen/mailbot/.wq1370-verify`, checked out at the PR's head SHA) for
    static inspection, then `git worktree remove`d it. Primary tree's dirty status is
    unchanged (`git status --short` before/after identical).

## Gap found (logged, not blocking)

Could not execute `node --check` or `npx wrangler deploy --dry-run` in-session: my
worktree's repo is claude-foreman, and the per-repo rendered permission settings
(`harness/runtime/preflight/render/settings-foreman-*.json`) carry zero `node`/`npm`/`npx`
entries even though the ticket's `Verify repo:` (mailbot) is reachable via `--add-dir` and
*does* have those entries in its own render. Logged as ISSUE-LOG row 149. Did not retry the
refused command shape (prior wq#1370 attempt classified PERMISSION_LOOP for exactly that).
Verified statically instead (type definitions, file content, hand-traced control flow) —
sufficient to confirm the diff's core claims, but a live build/parse check would have been
stronger evidence had the toolchain been reachable.

## Conclusion

wq#1359's diff is correct, minimal, and matches its own PR description: it's a small,
additive, fail-safe loader that turns three previously-inert lore files into real
per-sender system-prompt context, gated by a strict address/Cc match with no path where an
untrusted sender can widen their own access. No functional bugs found under adversarial
probing of the address-matching, parse-failure, and prompt-construction paths. CONFIRM.
