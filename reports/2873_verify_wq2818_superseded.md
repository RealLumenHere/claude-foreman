# wq#2873 verify of wq#2818 (PR #1184): SUPERSEDED, main is consistent

Verifier: final-push-ad230d, 2026-09-29.

- wq#2818 (7ca13c99, squashed as 4a6a7e3f) renamed `test_auto_lane_non_sweep_ticket_still_unmapped` to
  `..._uses_lane_default` and asserted a plain auto ticket dispatches via the LANE_REPO default.
- wq#2870 (PR #1192, 04:07Z) restored the refusal in `gates.LaneRepo` / `router.repo_for_ticket`
  (wq#713 MUST 7: refuse rather than guess a repo). fe0068ae then restored the original test name and
  assertion (`lane_unmapped` skip, no lumen-run call).
- Current main: `test_auto_lane_non_sweep_ticket_still_unmapped` at test_dispatcher.py:1557;
  `-k auto_lane` = 2 passed on an export of fc6d70b8.
- Verdict: wq#2818's change does not stand (its premise was retired 19 minutes after it landed), but nothing is
  broken on main. The lane-default positive case is covered by test_router.py::test_auto_lane_defaults_to_foreman.
- No follow-up needed beyond closing this verify. DESIGN.md row 7 was reverted with the test (checked).
