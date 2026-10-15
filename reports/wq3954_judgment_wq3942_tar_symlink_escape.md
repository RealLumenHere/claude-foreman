# wq#3954 — judgment: verify-pass of wq#3942 (LIMPET offline exploit database)

**Ticket under verify:** wq#3942, "LIMPET BUILD: offline exploit database —
searchsploit + cura" — merge commit `a3b3e559` into `/home/lumen/foreman`.
**Archetype:** 8 (adversarial verify-pass). **Author model:** claude-sonnet-5.
**Verifier:** claude-sonnet-5@high (this ticket, wq#3954), fresh instance.
**Escalated to:** opus-5@high, on a confirmed contradiction (self-grading risk —
author and verifier are the same model).

## What was checked

- Read the full diff (`git show --stat a3b3e559`): 16 files, 1031 insertions —
  `device_exploit_db.py` (SQLite build/query), `searchsploit_ops.py` (subprocess
  wrapper), `exploit_lookup_ops.py` (combines both), `server.py` (FastMCP tool),
  `limpet_update.py` (`--exploit-db` git-pull / `--from-usb` tar-sideload update
  CLI), seed JSON, tool-registry manifest, doc citation, and tests.
- Ran the real test suite: `python3 -m pytest ops/wq3942-limpet-exploit-db-a1/tests
  -q` — **24 passed**.
- Ran the ops bundle's live-target probe logic by hand (the `install.sh --verify`
  script itself was refused by the per-command Bash allowlist — no `Bash(bash *)`
  pattern beyond `scripts/*` exists in `harness/worker-settings.json`; reproduced
  both halves of its probe directly instead): built the real SQLite DB from the
  shipped seed JSON and queried it back — `n=4`, `soc_family="mt8167"` →
  `exploit_tool="mtkclient"`. Matches the script's own assertions. **No fake
  inputs** — this exercises the real sqlite3 file format, not a mock.
- Verified `device_exploit_db.query_devices()` builds its SQL with parameterized
  `?` placeholders (column names are hardcoded, not interpolated) — no SQL
  injection surface despite building the WHERE clause dynamically.
- Verified `exploit_lookup_ops.lookup()` correctly catches
  `searchsploit_ops.SearchsploitError` (raised when the binary is missing) and
  degrades to `{"ok": False, ...}` rather than failing the whole MCP call — matches
  the README's claim.
- Confirmed the seed data traces to real sources (4 rows, each citing an upstream
  tool repo/site) and that `TOOL_REGISTRY.md` / `searchsploit.yaml` honestly state
  `exploitdb` isn't installed yet (no invented claims, no overclaiming of an
  ISO pipeline that doesn't exist — matches fleet instinct 8).
- Adversarially probed `limpet_update.py extract_from_usb()`'s stated
  path-traversal guard — see Finding 1.

## Finding 1 — CONFIRMED CONTRADICTION (primary, escalation trigger)

**The `--from-usb` tar-extraction path-traversal guard in `extract_from_usb()`
(`limpet_update.py:66-88`) does not do what its own docstring claims, and is
currently safe only by accident of this host's Python version.**

- Docstring: "Refuses to extract any member whose resolved path would land
  outside `checkout_dir` (a malicious or corrupt tarball could otherwise write
  anywhere on the filesystem via '../' path components)."
- The guard loop computes `member_path = (dest_resolved / member.name).resolve()`
  for every member and raises if it's outside `dest_resolved` — **before any
  member has been extracted**, and it inspects only `member.name`, never
  `member.linkname`.
- PoC (built independently by both the verifier and the opus escalation): a
  tarball with (1) a symlink member `link` → `../outside` (relative, pointing
  outside `checkout_dir`), followed by (2) a regular file `link/evil.txt`. At
  check time `link` doesn't exist on disk yet, so `link/evil.txt`'s path
  normalizes lexically to *inside* `dest_resolved` — the guard loop flags
  **both members as inside**, never raising.
- On this host (Python 3.14.4), the actual `extract_from_usb()` call still
  raises — but the error comes from `tarfile.extractall()`'s own new default
  `data_filter` (PEP 706, default only since Python 3.14), **not** from the
  shipped guard. Confirmed by isolating the guard loop alone (prints
  `INSIDE`/`INSIDE` for both members, no raise) versus the full call.
- Opus-5@high independently reproduced this (own PoC, own isolation test, and a
  third test forcing `extraction_filter=fully_trusted_filter` to simulate
  pre-3.12 / legacy-override behavior): the forced-legacy-filter run **returned
  successfully and wrote `evil.txt` outside `checkout_dir`** — a real escape, not
  a theoretical one. **Verdict: CONTRADICTION CONFIRMED.**

**Severity: latent-but-real, medium-high.** `--from-usb` exists specifically for
LIMPET's air-gapped posture — the explicit threat model is an untrusted USB
tarball. The PR's own README confirms no ISO build pipeline exists yet, so the
Python version LIMPET will actually ship/run is undetermined; this guard provides
**zero** protection against the symlink-chain escape on any interpreter where the
default extraction filter isn't `data` (Python <3.12, or 3.12/3.13's
deprecation-warning-only default, or any environment that sets
`extraction_filter=fully_trusted_filter`). The fix is small and well-scoped: check
`member.issym()`/`member.islnk()` and validate `member.linkname` in the same
guard loop, and/or pass `filter="data"` explicitly to `extractall()` rather than
relying on an unstated interpreter default. Do not conflate this with a larger
rewrite — the rest of `limpet_update.py` (git-pull path, `rebuild_db`,
`refresh_searchsploit`) is sound and well-tested as-is.

## Other checks — no findings

- `device_exploit_db.py`: idempotent rebuild (DROP+recreate, no duplicate rows on
  re-run), required-field validation on seed load, case-insensitive substring
  query — all behave as documented and are covered by real (non-mocked) sqlite3
  tests.
- `pull_from_github()`: `git pull --ff-only` (never force-merges local drift) /
  `git clone --depth 1` split is appropriate and raises `UpdateError` rather than
  silently falling back to a stale DB on failure, matching its own docstring.
- Tests mock `subprocess.run`/`shutil.which` throughout for the searchsploit and
  git-network paths (appropriately — no real binary/network exercised) while
  running the SQLite and tar-extraction logic for real against `tmp_path` — matches
  the README's stated test philosophy and holds up under inspection.

## Allowlist friction (reported per RULES, not retried)

`bash ops/wq3942-limpet-exploit-db-a1/install.sh --verify` (the ticket's prescribed
live-target verify command) is refused by the per-command Bash allowlist —
`harness/worker-settings.json` only allows `Bash(bash scripts/*)`, not bare
`Bash(bash *)` or this ops-bundle path. Worked around by running both of the
script's own probe halves (`pytest`, then the inline `python3 -c` sqlite
build/query snippet) directly, which are separately allowlisted — functionally
equivalent evidence, but the prescribed command itself could not be run verbatim.

## Verdict

**CONTRADICTED — wq#3942 should be reopened for a minimal, scoped fix.** All
other acceptance items (searchsploit wrapper, curated device-exploits DB, MCP
tool, git-pull update path, registry/doc wiring, honest non-overclaiming of the
missing ISO pipeline) hold up under adversarial review and real test execution.
The one confirmed defect is narrow, well-understood, and sits exactly on the
air-gapped/untrusted-USB-input boundary this feature exists to serve safely.

Recorded via `wq review 3942 reject --reason "tar --from-usb path-traversal guard
doesn't check symlink targets; see wq#3954 judgment report + ISSUE-LOG row
1337"`. ISSUE-LOG row 1337 logs the finding and this judgment's recommendation.
