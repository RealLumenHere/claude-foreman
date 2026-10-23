# wq#4519 — judgment on wq#4508 / PR#2372 (limpet/result_store.py)

**Verdict: CONTRADICT. CONFIRMED defect, medium severity — unsanitized `tool`
name lets `store_result()` write arbitrary-content files outside the
workspace's evidence tree. Escalated to opus-5@high, upheld independently.**

wq#4508's deliverable gets the stated decision core right: `band_for()`'s
2000-char boundary is tested exactly at, above, and below the threshold
(`limpet/test_result_store.py:15-26`); `store_result()` fails closed with
`ResultStoreError` on a warm-band result with no summary
(`test_result_store.py:37-40`), never silently truncating or inlining raw
text; the cold file is credential-masked before it touches disk
(`test_result_store.py:54-61`); `summary_model()` correctly routes through
`model_router.resolve_profile()`'s `"speed-critical"` profile, not a
reasoning model (`test_result_store.py:64-74`); and `context.py`'s
`entry_for()` never lets the full blob reach the context entry
(`test_context.py:23`). All 11 shipped tests pass. But every test's `tool`
argument is the literal string `"nmap"` — the one input class the module's
core filesystem operation actually depends on for safety was never varied,
so the boundary below could not have failed (wq#2630 posture).

## The defect

`limpet/result_store.py:77-78`, `cold_path_for()`:

```python
def cold_path_for(workspace: str, tool: str, content_hash: str) -> str:
    return os.path.join(workspace, RESULTS_RELDIR, f"{tool}-{content_hash}.txt")
```

`tool` is interpolated into a path join with zero validation — no check
against an allowlist, no rejection of `/`, `..`, or a leading `/` (which
makes `os.path.join` discard every preceding segment outright).
`_write_cold()` (lines 81-87) then `os.makedirs()`s whatever directory that
produces and writes the (masked) full result text into it. `store_result()`
passes `tool` straight through at line 120.

The codebase already has house precedent for exactly this class of
identifier: `manifest._NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")`
(`limpet/manifest.py:64`), documented inline as "lowercase, so it is safe as
a filename and a dependency key," gates `[plugin].name` before it is used as
a filename component anywhere else in Limpet. `result_store.py`'s `tool`
parameter is used identically — as a filename component derived from a
plugin/child-server-controlled identifier — but skips that gate entirely.

opus-5@high's independent re-read additionally traced where `tool` actually
originates in the one real caller that exists today:
`ops/wq3808-limpet-mcp-meta-server-a1/meta_server.py:131-132` builds each
tool name as `namespaced(entry.name, child_tool["name"])`, where
`child_tool["name"]` comes **unchecked** from a child MCP server's
`tools/list` response — i.e. a third-party plugin's own server picks that
string. A `group.` namespace prefix does not stop a `../` escape embedded
after it.

## Fresh evidence (independently reproduced by both the sonnet verifier and
the opus judge, in separate sandboxes, not reused from a single run)

```python
import tempfile, os, sys
sys.path.insert(0, "/home/lumen/foreman/limpet")
import result_store as rs

with tempfile.TemporaryDirectory() as ws:
    workspace = os.path.join(ws, "workspace")
    os.makedirs(workspace)
    big = "x" * (rs.HOT_THRESHOLD + 1)
    stored = rs.store_result(workspace, "../../../../tmp/evil", big, summary="s")
    print(stored.cold_path, os.path.isfile(stored.cold_path),
          os.path.realpath(stored.cold_path).startswith(os.path.realpath(workspace)))
```

Sonnet verifier's run: file landed at
`.../.tmp/tmp/evil-9f2c9dcd…c5c5.txt`, several directories above the
intended workspace root; `inside workspace: False`.

Opus judge's independent run (separate tmpdir, separate process) reproduced
the same escape, plus two additional variants:

- `"srv.x/../../../../escaped"` (closer to the real `group.tool` shape
  `namespaced()` produces) — still escapes, confirming the `group.` prefix
  provides no containment.
- A leading-`/` tool name (`"/tmp/absolute_evil"`) — `os.path.join` drops
  the workspace entirely, writing straight to `/tmp/absolute_evil-<hash>.txt`.
  Worse than the first case: no traversal sequence needed at all.

Both runs confirm the shipped test suite still passes unmodified (11/11) —
the defect is invisible to the existing tests by construction, not a flaky
probe.

## Escalation

Per archetype-8 protocol, this contradiction of wq#4508's implicit "done"
verdict was escalated to a fresh opus-5@high instance. It independently
re-read `result_store.py`, `manifest.py`, and (going further than the
initial sonnet pass) `meta_server.py` to locate the real-world source of
`tool`, independently re-ran the reproduction with two additional variants,
and rendered its own verdict: **CONFIRMED, medium severity, latent** — no
caller wires this module into the live path yet (`meta_server.py` doesn't
call `result_store.store_result` currently), so nothing has actually escaped
a workspace in production. The opus pass also corrected the sonnet
verifier's framing: the attacker controls the destination *directory* and
the file *content*, not an arbitrary *filename* (the name always ends in a
content-derived `-<sha256>.txt`), so this is directory-traversal-to-written-
file, not a free-form overwrite of a chosen existing path such as
`~/.bashrc`.

Fabricated-evidence check (instinct 8): both passes confirm every path,
regex, and line number cited above is read directly from the shipped source
(`result_store.py`, `manifest.py`, `meta_server.py`) or the spec doc; the
malicious `tool` strings are test inputs each pass constructed itself and
labeled as such, not values presented as already present in the system.

## Remediation (not applied — this is a verify/judgment pass, not a
re-implementation)

1. Validate `tool` against an allowlist before it reaches `cold_path_for()`
   — e.g. a regex permitting the `group.tool` namespace shape
   (`manifest._NAME_RE` widened for the `.` separator it already allows, or
   a dedicated tool-name pattern), raising `ResultStoreError` on mismatch,
   same fail-closed posture `store_result()` already uses for a missing
   summary.
2. Belt-and-suspenders: after building `path`, assert
   `os.path.realpath(path).startswith(os.path.realpath(os.path.join(workspace, RESULTS_RELDIR)) + os.sep)`
   before writing, independent of whatever the allowlist regex permits.
3. Add `..`, `/`, and leading-`/` cases to `test_result_store.py` as
   permanent regression probes — each should raise, not quietly succeed.

## Scope note

Not evaluated: whether to revert wq#4508's merge. PR#2372 is already in
`main` (commit `37cb3451`) but `result_store.store_result()` has no live
caller yet (`meta_server.py` does not invoke it), so no workspace has
actually had a file written outside it in production. This judgment's
recommendation is to fix before the agent core (still design-only) wires
this module into the dispatch path, not to revert the merge.
