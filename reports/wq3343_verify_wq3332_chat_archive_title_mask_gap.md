# wq#3343 verify: wq#3332 chat-archive export + local digest pipeline

Archetype 8 adversarial verify-pass. Author: claude-sonnet-5. Verifier: claude-sonnet-5
(fresh instance, same model as author).

PR: http://100.121.17.5:3000/lumen/claude-foreman/pulls/1547, merged to `main` as
`6f744c55`. Files: `bin/chat-export`, `bin/tests/test_chat_export.py`,
`ops/wq3332-chat-digest-local/{chat-digest-local,chat-digest-pull.sh,
chat-digest-pull.service,chat-digest-pull.timer,install-chat-digest-local.sh,README.md,
tests/test_chat_digest_local.py}`.

## What wq#3332/PR#1547 claimed

Phase 2: `bin/chat-export` parses claude.ai `<chat>` blocks pasted on stdin into a
deduplicated JSON archive. Phase 2b: `ops/wq3332-chat-digest-local/` stages a CLI that
runs a synced-down copy of that archive through a local Ollama model on an inference
node (hathor/nephthys; the ticket's isis/maat targets corrected against
`research/inference_nodes.yaml`, with reasoning).

Both tools carry an explicit, unconditional secret-masking guarantee in their own
docstrings:

- `bin/chat-export`: "Anything shaped like a credential is replaced by `<redacted>`
  before it ever reaches disk (same patterns as chat-digest: cf*_ tokens, github_pat_,
  sk-..., Bearer, long hex/base64, JWT, password= values)."
- `chat-digest-local`: "Secrets are masked twice: bin/chat-export already masks the
  archive body, and this CLI masks again before building the prompt..." — i.e. the
  design assumes the archive chat-export writes is already clean.

## What I checked

- Read `bin/chat-export` and `ops/wq3332-chat-digest-local/chat-digest-local` cold.
- Ran both test suites unmodified: `python3 -m pytest bin/tests/test_chat_export.py
  ops/wq3332-chat-digest-local/tests/test_chat_digest_local.py -v` → **12/12 passed**,
  matching the ticket's claim.
- Traced `parse_blocks()` (`bin/chat-export:66-86`): `body` is run through `mask()`
  before being stored (`"body": mask(body)`, line 84), but `title` (line 83) is taken
  directly from the unmasked `body` string extracted one line earlier (`tm =
  re.match(r"\s*Title:\s*(.+)", body)`, lines 74-76) and stored as-is — never passed
  through `mask()`. `url` (line 79) is likewise stored unmasked.

## Contradiction found (CONFIRMED)

**The archive's `title` field bypasses the masking the tool's own docstring promises
unconditionally.** Reproduced live against the shipped `bin/chat-export`, not a
rewritten copy, with a runtime-built (never-literal) fake credential per house masking
rules:

```
$ python3 -c "
fake_pat = 'github_pat_' + 'Q9' * 22
text = '<chat url=\"https://claude.ai/chat/11111111-2222-3333-4444-555555555555\" ' \
       'updated_at=\"2026-01-01T00:00:00Z\" kind=\"summary\">\nTitle: leaked %s in title\n' \
       'some body text\n</chat>' % fake_pat
..." | bin/chat-export --archive /tmp/archive_probe.json
$ cat /tmp/archive_probe.json
{
  "chats": [
    {
      "id": "11111111-2222-3333-4444-555555555555",
      "url": "https://claude.ai/chat/11111111-2222-3333-4444-555555555555",
      "updated_at": "2026-01-01T00:00:00Z",
      "kind": "summary",
      "page_token": "",
      "title": "leaked github_pat_<...44 more chars, unredacted...> in title",
      "body": "Title: leaked <redacted> in title\nsome body text"
    }
  ]
}
```

`body` is correctly redacted; `title` carries the same credential-shaped string
verbatim to disk, at `~/.chat-archive/archive.json` on Lumen. This is not a contrived
case: `title` is *structurally derived from the same freeform chat text* that
motivated the masking requirement in the first place (the `Title: ...` line is part of
what the browser pastes in), so any credential a user's chat title happens to echo
(a very ordinary thing to put in a chat title: "rotate the AKIA... key", "token leak
in prod") survives chat-export untouched.

The practical blast radius is two machines, not one: `chat-digest-pull.sh`
(`ops/wq3332-chat-digest-local/chat-digest-pull.sh:20`) rsyncs this archive.json
verbatim from Lumen down to nephthys every hour (`rsync -a
"$LUMEN:.chat-archive/archive.json" "$MIRROR"`), so the unmasked title is duplicated
onto a second node's disk in plaintext before `chat-digest-local` ever runs. That CLI
does mask the title again before using it in a prompt
(`chat-digest-local:86`, `title = mask(chat.get("title") ...)`), so the
*downstream digest* stays clean — but that is a mitigation for the Ollama prompt, not
for the archive file itself, which both machines now hold at rest with the secret
exposed.

## What I did not find broken

- `body` masking: correct, both in `chat-export` and in `chat-digest-local`'s
  re-mask-before-prompt / re-mask-before-print (verified via the `FAKE_PAT`-based unit
  tests, which do pass, and by direct reproduction above).
- Merge semantics (`merge()`, `bin/chat-export:110-129`): added/updated/unchanged/
  skipped counts and `updated_at`-newer replacement all behave as documented; dedup by
  `id` works across pages.
- `chat-digest-local --dry-run` path: builds one prompt per chat, correctly falls back
  title→id→"(untitled)", respects `--model` override, exits 2 on missing/bad archive,
  exits 1 only when every chat errors — all matches the docstring and all 6 of its
  tests pass.
- Node targeting correction (hathor/nephthys only, isis/maat excluded with cited
  `research/inference_nodes.yaml` evidence) and the hathor-timer exclusion
  (`install-chat-digest-local.sh` only wires the timer on the box it's run from, and
  the README documents not installing it on hathor) — both check out against the
  registry as described.

## Required but missing: `ops/wq3332-chat-digest-local` has no `--verify` handler

Per `ops/VERIFY_CONTRACT.md`, every `install.sh` outside a lane repo needs a
`--verify` handler before a verify pass can confirm it; a repo-only check does not
count (wq#1696). `install-chat-digest-local.sh` has none — confirmed by reading the
full file, no `--verify` branch exists. Moot for this pass's disposition (the title-
mask gap disposes this CONTRADICT on its own), but whoever picks up the fix needs to
add one (byte-match `chat-digest-local`/`chat-digest-pull.sh`/unit files against
`~/bin`, `venabled`/`vactive` the timer) before this bundle can ever be verify-closed.

## Action taken

CONTRADICT. Note posted to wq#3332 via `wq note`. Not re-implementing
`parse_blocks()`/`mask()` per ticket instruction ("Do NOT re-implement; verify").
Escalating per ticket policy to opus-5@high judgment, filed as wq#3355.
Fix shape for whoever picks it up: mask `title` (and, for completeness, `url`) the
same way `body` already is, at `bin/chat-export:83` (`"title": mask(title)`) before
the block is appended — and add a regression test asserting a credential-shaped
string in the `Title:` line of the input is redacted in the archive's `title` field,
not just its `body` field, so this probe can fail again before merge.
