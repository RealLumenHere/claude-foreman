# wq#2312 — verify of wq#2250 (claude-foreman PR#811, merged 27c1fd5)

Verifier: claude-sonnet-5@high, fresh instance. Author: claude-sonnet-5. Verdict:
**CONFIRM** — no contradiction, no escalation.

wq#2250 (C2 of wq#2241) claims to build `fleet-node render [--check|--apply]`
(`harness/node_render.py`, `harness/fleet_node.py`) as three renderers —
`dispatcher/config.json` `local_backends`, `research/models.yaml` local rows +
`local-worker` group, and the four `LOCAL_POOL_MODELS` literal tuples
(`orchestrator/judgment.py`, `harness/fworker.py`, `despag/admission.py`,
`despag/app.py`) — driven from `research/inference_nodes.yaml`, satisfying design
§8 T2/T3/T4, and that 12 pre-existing harness failures on the branch are
environmental (ISSUE-LOG #563/#564), not caused by this diff. I did not trust
the done-notes at face value; I read the diff cold, cross-checked it against
`research/INFERENCE_NODE_REGISTRY.md` §3/§4/§8, ran its probe live, and tried to
break the untested edges myself.

## Per claim

| wq#2250 claim | Result | Evidence |
|---|---|---|
| `fleet-node render --check` reproduces the current repo's three targets byte-for-byte (T2) | CONFIRMED, independently reproduced | Ran `python3 harness/fleet_node.py render --check` directly (not via pytest) against the live `/home/lumen/foreman` checkout on `main`: `fleet-node render --check: 0 target(s) drifted from the registry`, exit 0. |
| T2/T3/T4 suite passes (`dispatcher/tests/test_node_render.py` + `harness/tests/test_prefix_list_parity.py`) | CONFIRMED, count is actually higher | Ran the exact liveness command from the wq#2250 ticket: `python3 -m pytest dispatcher/tests/test_node_render.py harness/tests/test_prefix_list_parity.py -q` → `26 passed`. Done-notes say "24 pass" — a stale/miscounted figure (13+13=26 tests in the two files as they exist now), not a functional discrepancy: every test passes, none are skipped or xfailed. |
| No sixth `LOCAL_POOL_MODELS` copy; the five (four editable + frozen governor) agree with the registry (T3) | CONFIRMED | `test_exactly_five_files_define_the_literal` passes; independently grepped the diff — only `orchestrator/judgment.py`, `harness/fworker.py`, `despag/admission.py`, `despag/app.py` gained sentinels, `governor/governor_core.py` untouched, matching design §3's "governor is never rendered into." |
| Governor-exemption round trip holds for every registry id, with and without `@low` effort suffix (T4) | CONFIRMED | `test_every_registry_id_is_governor_exempt_everywhere` and `test_node_suffixed_id_stays_exempt_under_the_naming_rule` pass against the real registry (hathor-llama8b, nephthys-qwen14b, qwen36-local). |
| Dispatcher JSON stays valid and passes the existing `dispatch_config_lint` gate after render | CONFIRMED | Traced `render_dispatcher()`: every emitted row line ends in a trailing comma, immediately followed by the `_generated_end` string key — verified against the live `dispatcher/config.json` that this is exactly the shape on disk; `render_dispatcher` itself round-trips the output through `json.loads` and `dispatch_config_lint.validate_local_backends` before returning, so a shape regression fails loud, not silent. |
| 12 pre-existing harness failures are environmental, not wq#2250's fault | CONFIRMED | `kb/ISSUE-LOG.md` rows 563/564 (filed by the author at wq#2250's own time) document `test_evidence_gate.py`, `test_wq1894_sigterm_cancel.py`, `test_memory_injection.py`, `test_stop_gate_worker_traps` failing identically on unmodified base `486691a` inside a worker session (FOREMAN_TICKET_ID set), with a stated root cause (stop_gate reads pinned run_dir acceptance; order-dependent race) unrelated to node_render. |
| Models.yaml migration preserves history as comments, doesn't silently drop fields | CONFIRMED | Diffed `research/models.yaml` directly: `status`, `pools`, `context_tokens` for the three local rows moved from live YAML keys to sentinel-generated content; the removed hand-written notes (`wq#2070`, `wq#750`, `wq#2133` context) survive as plain comments immediately above the sentinel block, matching design §3 "hand-written neighbours survive." |

## Adversarial attempts (no confirmed bug found)

- Manually drove `node_render.render_dispatcher`/`render_models` against a
  registry with `nephthys` forced to `state: draining` — a code path
  (`node_render.py:102-103`) with **zero test coverage** in either suite, since
  none of the three real nodes is currently draining. Result: the dispatcher
  correctly drops the node from both its individual row and the `local-worker`
  group membership list; `models.yaml`'s row survives with `status: draining`
  (intentional per §5.2 — draining ≠ retired); the group's `served_ctx` (a
  `min()` over non-`NO_SLOT_STATES` members, which does NOT exclude
  `draining`) stays pessimistic rather than wrong — including a draining
  node's ctx in the min can only lower the declared group ctx, never let a
  request overflow a smaller real window. Not a bug, but flagging the gap:
  the design's own §8 doesn't assign draining-state render coverage to C2
  (T6, drain/retire, is C4/wq#2253's job), so this is forward-looking scope,
  not a regression here.
- Checked whether `cold`/`down` states (the other two non-`live` states
  `render_dispatcher` does *not* skip) could ever emit a broken row: `isis`
  is seeded `state: down` in the real registry today and does keep its
  dispatcher row — matches `test_retired_node_is_omitted_but_down_node_keeps_its_row`
  and the design doc's explicit call-out ("isis... its probe fails so it
  already contributes 0 slots — row left in place").
- Looked for a case where `_splice`'s single-BEGIN/single-END requirement
  could silently corrupt a file instead of raising — `test_missing_sentinel_is_an_error_not_a_silent_skip`
  covers the missing-sentinel case; by inspection `_splice` also raises on
  a duplicate BEGIN/END or an inverted order (`begins[0] >= ends[0]`), so
  there's no silent-corruption path I could find.
- Considered escalating on the notes' "24 pass" vs. the actual 26 — decided
  this is a stale headcount in prose, not a substantive claim about behavior;
  the underlying assertion ("the suite passes") is true and I reproduced it
  myself rather than trusting the number.

## Conclusion

Every load-bearing claim in wq#2250's done-notes reproduces independently:
the three renderers match the live repo byte-for-byte under `render --check`,
the full T2/T3/T4 suite passes (26/26, exceeding the claimed 24), the
governor-exemption round trip holds, and the pre-existing harness failures
are documented and unrelated. I found one untested code path (draining-state
handling) and hand-verified it behaves conservatively rather than incorrectly;
it does not contradict any claim wq#2250 actually made. **CONFIRM.**
