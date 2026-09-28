"""wq#2868 judgment regression: the invariant wq#2848's floor-fallback actually buys.

wq#2803 CONTRADICTED wq#2720 because a foreign-prefix stop could cut a body run below
the rule's own floor, failing the whole match and leaving a real token in the clear with
no alarm. wq#2848's fix is not "stop less often" — it is the structural property that a
foreign shape cannot be considered until the rule's own floor is already satisfied, so a
stop can only SHORTEN a match, never prevent one.

This test pins that property rather than a list of offsets: test_mask_foreign_prefix_matrix
samples nine offsets per rule, and sampled offsets are what let wq#2720 ship. The probe
this file drives sweeps every offset from 0 to floor+6 for every rule x every foreign
prefix that rule's charset can spell, and adds three shapes the matrix does not reach —
kv-secret as host (the rule wq#2720 deliberately gave no foreign stop), url-auth as host
(no bounded body at all), and a four-credential chain (whether the multi-pass sweep
drained a chain of stops rather than one). wq#2913 removed the stops and the passes
both; the shapes stay, because what they measure is coverage, not the mechanism.

Section B is the same under-floor class through the OTHER guard `_body()` carries, the
unconditional mask-literal lookahead, which wq#2848 correctly left alone. wq#2922 closes
it with a fail-loud backstop (masking._orphan_matches): the value is not masked (masking
one this short risks re-swallowing `<prefix>MASKED` and reviving the wq#2638 leak), but a
hit now fires every time, so the section's bar is "no SILENT case", not "no fill char
survives" — see the probe's own section_b docstring.

The probe is the evidence artefact of reports/wq2868_judgment_wq2848_floor_fallback.md and
takes a masking-directory argument, so the same cases run against a historical rev.
"""
import os
import sys

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "..", "reports"))
import wq2868_probe_underfloor_class as probe  # noqa: E402


def _run(fn):
    name, total, fails = fn()
    assert total, f"{name}: probe generated no cases — it cannot fail (wq#2630)"
    return name, total, fails


def test_a_foreign_shape_can_never_prevent_a_match_at_any_offset():
    name, total, fails = _run(probe.section_a)
    assert fails == [], f"{name}: {len(fails)}/{total} tokens not masked at all: {fails[:6]}"


def test_kv_secret_host_is_unaffected_by_foreign_shapes_in_its_value():
    name, total, fails = _run(probe.section_c)
    assert fails == [], f"{name}: {len(fails)}/{total} leaked: {fails[:6]}"


def test_url_auth_host_is_unaffected_by_foreign_shapes_in_its_password():
    name, total, fails = _run(probe.section_d)
    assert fails == [], f"{name}: {len(fails)}/{total} leaked: {fails[:6]}"


def test_a_four_credential_chain_is_drained_and_every_label_fires():
    name, total, fails = _run(probe.section_e)
    assert fails == [], f"{name}: {fails}"


def test_a_body_holding_the_mask_literal_below_the_floor_alarms_instead_of_leaking():
    name, total, fails = _run(probe.section_b)
    assert fails == [], f"{name}: {len(fails)}/{total} SILENT: {fails[:6]}"
