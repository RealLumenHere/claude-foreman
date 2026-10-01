# wq#3355 judgment: chat-export field masking (wq#3343 CONTRADICT on wq#3332)

Ticket asked for a judgment call, not a blind patch: mask `title`/`url` inline in
`parse_blocks()`, or centralize masking so no future field can be added unmasked by omission.

## Judgment: centralize, mask-by-default, with one named exemption

The defect was an **omission**, not a typo: `bin/chat-export` masked `body` at its single
construction site and left `title` and `url` — both derived from the same freeform chat text —
in plaintext, because every masked field had to be remembered individually. Patching the two
known fields inline leaves that property intact and the next field repeats the bug. So the
default changed instead:

- `mask_block()` (`bin/chat-export:75-84`) masks **every** string field of a chat record except
  the ones named in `OPAQUE_FIELDS`. Opt-out, not opt-in.
- `archive_payload()` (`bin/chat-export:118-126`) is the one funnel through which a record
  leaves the process — both the archive file and `--print` go through it. That also *heals* an
  archive a pre-fix build wrote: the unmasked `title` already on Lumen's disk (and on
  nephthys's, via the hourly `chat-digest-pull.sh` rsync) is redacted on the next write, not
  only in records this run parsed.
- `parse_blocks()` masks at construction too, so in-memory records are clean before merge.

### Why not blanket-mask everything

`page_token` is a 40+ char base64-ish pagination cursor, which matches the trailing long-token
rule in `SECRET_RES`. Masking it would hand the caller `<redacted>` as `last_page_token` — the
value a paginating `recent_chats` walk loops on — and stall the walk. It is the single entry in
`OPAQUE_FIELDS`, with the reason recorded at `bin/chat-export:60-65`. `id` is deliberately *not*
exempt: it is derived from `UUID_RE`, which cannot match any pattern here, and it is read off
the **raw** url before masking so a credential-bearing url cannot destroy the merge key.

## Probes that can fail

`bin/tests/test_chat_export.py:107-166` — title redacted, url redacted with `id` intact,
`page_token` verbatim, an unlisted `future_field` masked without being named, and a legacy
unmasked archive healed on `--print` and on the next write.

The `page_token` test probes from outside the population the fix was designed around: it asserts
the field the fix must **not** touch, so a blanket mask fails it. Falsification against the
pre-fix code, pinned to a rev rather than a live file
(`python3 /tmp/fworker-wq3355/prefix_probe.py 36ec04c5 <worktree>`):

    prefix  (36ec04c5): title_leaked=True   body_leaked=False  page_token_intact=True
    postfix (worktree): title_leaked=False  body_leaked=False  page_token_intact=True

11 passed, `python3 -m pytest bin/tests/test_chat_export.py`.

## Second defect: the bundle's missing `--verify` handler

`ops/wq3332-chat-digest-local` had none (`ops/VERIFY_CONTRACT.md`). Writing one into
`install-chat-digest-local.sh` would have been a gate that can never run: `ops-deploy-drift`
opens exactly `<bundle>/install.sh` (ISSUE-LOG #1151). So the installer is renamed to
`install.sh` and the handler added at `ops/wq3332-chat-digest-local/install.sh:17-35` —
remote-only, because the payload lands on nephthys; an unreachable node is ERR (exit 2), never
MISS (wq#2985), and nephthys currently reads `availability = offline` in the registry, so that
path is the live one today. The remote install user's `$HOME` is resolved over ssh rather than
hardcoded, since `vremote_file` single-quotes the remote path and `~` would not expand.

`vremote_user_unit` is new in `ops/_verify.sh:118-135` (comment at 118-123, body at 124-135): this is the first bundle shipping a
`systemctl --user` timer, and `--user` over a non-interactive ssh has no bus unless
`XDG_RUNTIME_DIR` is set — without it every user unit reports a bus failure that a naive check
would file as a MISS it invented.

`ops/wq3332-chat-digest-local/test_verify_handler_wq3355.py` (7 passed) drives both MISS and ERR
paths through a stub `ssh`, so no live node is needed.

## Not done here

- Deploying the bundle to nephthys (offline since 2026-09-29) — `--verify` now reports the truth
  about it either way.
- `bin/chat-export` is not installed to `/home/lumen/bin` at all (only `chat-digest` is), so the
  archive the pull timer syncs has no deployed producer. Out of scope for this defect and not
  this bundle's payload, but it blocks any end-to-end verify-close of the wq#3332 pipeline.
