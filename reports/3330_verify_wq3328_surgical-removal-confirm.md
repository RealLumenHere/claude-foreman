# wq#3330 — verify-pass on wq#3328's fix (PR #1539)

**Verdict: CONFIRM.** `remove_chrome_mcp_ingress.py`'s surgical `find_block()`
removal (commit `776ac7ad`, branch
`feat/wq3328-wq3321-fix-remove-chrome-mcp-ingress-py--a1`) correctly fixes the
defect wq#3326 found (`yaml.safe_load` + `yaml.safe_dump` round-trip dropping
every comment and reformatting every untouched ingress entry on `--apply`).
No new correctness or safety defect found under adversarial fixture testing.
Not re-implemented — read cold, probed, verified only.

## What changed (read cold from the diff)

`ops/wq3321-chrome-mcp-retirement/remove_chrome_mcp_ingress.py`: `main()` no
longer does `doc["ingress"] = [... ]` + `yaml.safe_dump(doc, ...)`. Instead
`find_block(lines, hostname)` walks the raw file text, locates the `ingress:`
key, determines the list-item indent from the first real entry, then scans
each `- ...` item by indentation, parsing **only that one item's own text**
via `yaml.safe_load` to confirm `hostname` equality before returning its
`[start, end)` line range. `main()` splices those exact lines out of the
original text (`"".join(lines[:start] + lines[end:])`) — every other byte of
the file, including comments and other entries' formatting, is untouched.
Added `test_remove_chrome_mcp_ingress.py` (4 tests, fixture with 2 comments +
4 entries) asserting byte-identical output except for the removed entry's own
two lines.

## Claims checked

- **Author's own test suite, run fresh (not trusted from the PR
  description):** extracted `remove_chrome_mcp_ingress.py` +
  `test_remove_chrome_mcp_ingress.py` from commit `776ac7ad` into
  `/tmp/fworker-wq3330/probe/`, ran `python3 -m pytest`. **4/4 PASS.**
- **Live liveness probe, via the sanctioned job-runner path** (ISSUE-LOG row
  975 — `bash .../install.sh --verify` is refused directly by the Bash
  allowlist; `lumen-run start -- bash install.sh --verify` is the documented
  workaround, used here instead of the ad hoc `python3 ... --dry-run` route
  wq#3326 fell back to):
  `lumen-run start --name wq3330-ops-verify -- bash
  ops/wq3321-chrome-mcp-retirement/install.sh --verify` →
  `MISS cloudflared still serves chrome-mcp.lumennode.uk: found:
  {'hostname': 'chrome-mcp.lumennode.uk', 'service': 'http://localhost:3103',
  'originRequest': {'connectTimeout': '30s', 'tcpKeepAlive': '30s',
  'keepAliveConnections': 100}}` — `VERIFY FAIL (1)`. Same originRequest
  shape and same MISS wq#3326 reported: confirms the fix is still unapplied
  (expected — `--apply` needs root/Commander, not a build worker) and that
  the verify handler itself still works, unregressed by this PR.
- **Six independent adversarial fixture probes** (pinned, synthetic —
  `/tmp/fworker-wq3330/probe/probe_adversarial.py`), not reused from the
  author's own test, built from the exact `originRequest` shape the live
  `--dry-run` disclosed (3 keys: `connectTimeout`, `tcpKeepAlive`,
  `keepAliveConnections`) plus edge placements:
  - **A** — real 3-key `originRequest`, matches live shape: removes only
    chrome-mcp, 3 entries remain, nested multi-line dict for the *other*
    entries untouched.
  - **B** — compact style, no blank-line separators between entries: still
    resolves item boundaries correctly (boundary detection doesn't depend on
    blank lines).
  - **C** — chrome-mcp immediately before the trailing hostname-less
    catch-all, no blank line, file ends mid-list: correct removal, valid YAML
    after.
  - **D** — chrome-mcp is the *first* list item: correct removal.
  - **E** — near-miss hostnames present (`notchrome-mcp.lumennode.uk`,
    `chrome-mcp.lumennode.uk.evil.com`): exact-match only, neither near-miss
    touched, confirming the per-item `yaml.safe_load` + equality check (not a
    text/regex substring match) is what actually gates removal.
  - **F** — file ends with no trailing newline exactly at the end of the
    target entry (EOF mid-block): no crash, no `IndexError`, resolves
    cleanly.
  All six: target entry removed, all other entries/comments byte-identical,
  output re-parses as valid YAML with the expected remaining entry count.

## One scope note, not a defect

The line immediately preceding the removed entry (its own descriptive
comment, e.g. `# chrome-mcp: retired 2026-09-29, ...`) is left behind,
orphaned, since `find_block` only returns the matched item's own lines —
confirmed via the author's own `test_removed_lines_are_exactly_the_matching_entry`
and reproduced in probe A. This is not a regression or an unstated claim:
wq#3326's fix spec asked only for "the removed entry's own lines" to be
excised byte-identically, and the commit message's "every other line ...
stays byte-identical" claim is literally true — the orphaned comment line
itself is untouched, just no longer attached to anything. Flagging for
whoever eventually runs `--apply` to hand-clean that one comment line, not
blocking this verify.

## Coverage

Diff read cold from `git show 776ac7ad` (this worktree's branch does not
contain wq#3328's commit — verified `merge-base --is-ancestor` is false — so
the fix was extracted via `git show <sha>:<path>` rather than trusted from a
merged working tree). Re-ran the author's test suite independently rather
than trusting the PR's reported pass. Probed with fixtures the author's own
suite doesn't cover (real 3-key `originRequest`, compact formatting, boundary
positions, near-miss hostnames, missing trailing newline) specifically
looking for ways `find_block` could over-delete, under-delete, or corrupt
unrelated entries — the original "destroys ALL cloud[flared formatting]"
failure mode. None found. Confirmed the bundle's `--verify` handler itself
still passes through unregressed, via the ISSUE-LOG-documented sanctioned
path rather than a direct `--dry-run` workaround.

## Disposition

CONFIRM. No escalation (no contradiction). wq#3328's diff is safe to land as
the fix for wq#3326's finding.
