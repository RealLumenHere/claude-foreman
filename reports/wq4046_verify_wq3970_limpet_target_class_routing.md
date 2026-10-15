# wq#4046 — verify wq#3970: LIMPET target-class -> toolset routing table

**Verdict: CONFIRM**

PR: http://100.121.17.5:3000/lumen/claude-foreman/pulls/1982 (merged 5a41057d31)
Author model: claude-sonnet-5. Verifier: claude-sonnet-5@high (fresh instance), per
wq#4046. Worktree: `/home/lumen/worktrees/wq4046-a1`.

## Scope

wq#3970 adds `limpet/registry/routing.json` (declarative target-class -> ordered
toolset-group table), `limpet/registry/routing.py` (schema validation, tool-id
resolution against `tool-catalog.json`, fail-closed `resolve_target_class`), and
`limpet/registry/tests/test_routing.py`. Pure data + loader, no caller wired in yet
(meta-server/wizard are design-only — confirmed, see below).

## What I checked

1. **Acceptance criteria from the ticket** (`wq show 3970`): "schema validation
   test, test that every routed tool id resolves in tool-catalog.json, router test
   returning the expected group for 3 fixture target classes." All three present
   in `test_routing.py` and genuinely exercised:
   - `test_shipped_routing_table_is_schema_valid`
   - `test_every_routed_tool_resolves_in_tool_catalog`
   - `test_router_resolves_rockchip_firmware` / `_android_apk` / `_pcap` (3 classes)

2. **Ran the tests, not just read them.**
   `python3 -m pytest limpet/registry/tests/test_routing.py -v` → 12/12 pass.
   `python3 -m pytest limpet/registry/tests/ -q` → 37/37 pass (matches the done-notes
   claim, no regressions in the sibling registry suite).

3. **The catalog-resolution test is not a vacuous pass.** `load_catalog_tool_names()`
   reads the real, committed `limpet/tool-catalog.json` (confirmed: dict with a
   `tools` list, 42 entries, not a stub). Manually cross-checked every tool id named
   in `routing.json` (unblob, binwalk, rkdeveloptool, ghidra, radare2, apktool,
   jadx, frida, scrcpy, cutter, qemu-user, gdb-multiarch, netzob, fiedka) against
   that list in a one-off script — all 14 resolve. `fiedka` carries a
   pre-existing "ticket-named; metadata unverified" caveat in the catalog itself
   (not introduced by this PR) but it does resolve by name, which is all
   `validate_tools_resolve` claims.

4. **Tried to break `routing.py` directly**, beyond the shipped tests:
   - Malformed tables (non-mapping, missing `target_classes`, empty
     `toolset_groups`, missing `stage`, empty `tools`, duplicate `stage` within
     one class, non-string tool ids) — all correctly flagged by
     `validate_routing_table`.
   - Confirmed `seen_stages` is scoped per target class (not leaked across
     classes), so duplicate `stage` names across *different* classes correctly do
     not false-positive.
   - Confirmed `resolve_target_class` fails closed (`RoutingError`) on an unknown
     class and lists the known ones — matches the module's own stated design
     intent ("not a guess, a fail-closed").
   - Checked for integration overclaiming: grepped `limpet/target_devices.py` for
     any reference to `routing` — none. The PR's own claim that the meta-server
     and wizard integration are "design-only so far" is accurate, not inflated.

5. **Checked the doc citations against the actual design docs** (never trust a
   line-number claim unverified, per doctrine). `routing.py`'s docstring and
   `README.md` both cite `design/wq3806_limpet_architecture.md:152-159` for the
   two-level-router quote. The quote ("the agent asks for the toolset relevant to
   the current target class ... and only those schemas are mounted") actually
   lives at lines 167-168 of that file, not 152-159 — a stale ~10-15 line offset.
   Traced the citation: it is not something wq3970's author introduced — it's
   copied verbatim from the wq#3970 ticket body itself (`wq show 3970`) and
   ultimately from `design/wq3929_competitive_analysis.md:187`'s own citation of
   the same wrong range. This is a pre-existing stale citation three steps up the
   chain, not fabricated by this diff's author, and it doesn't affect the
   table/loader's correctness. Noting it rather than treating it as a
   contradiction — it's a documentation nit, not a functional defect, and the PR
   author had no way to know the upstream citation had drifted without
   independently re-deriving it (which is good practice but a higher bar than
   "verify your own diff").

## Verdict rationale

No functional bugs found. All ACCEPT criteria are met and were independently
re-run, not just read. The catalog-resolution check is grounded in the real
committed catalog (42 real tool entries), so it can actually fail if a routed
tool id typos or a catalog tool gets renamed — this is a probe that can fail, not
a tautology. Fail-closed behavior on unknown target classes is correct and
tested. No overclaiming of integration status. One pre-existing stale design-doc
line citation noted above, inherited from the ticket text itself — not a reason
to contradict this PR.

No escalation to opus-5@high: no contradiction found.
